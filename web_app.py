from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from dataclasses import asdict
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path
from collections import deque
from urllib.parse import parse_qs, quote

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from config.loader import (
    DEFAULT_SETTINGS_PATH,
    DEFAULT_SYMBOLS_PATH,
    VALID_SYMBOL_TIMEFRAMES,
    load_execution_runtime,
    load_project_config,
)
from config.secrets import load_binance_readonly_credentials, load_futures_binance_readonly_credentials
from exchange.binance_client import BinanceClient
from exchange.binance_client import BinancePrivateReadOnlyAPIError
from execution.account_risk import (
    account_risk_status_payload,
    get_account_risk_status,
    reset_account_risk,
)
from futures_bot.config_loader import load_futures_config
from futures_bot.exchange.binance_futures_client import BinanceFuturesClient
from futures_bot.exchange.futures_rules import parse_futures_symbol_rules
from observability.event_logger import LogRouter, StructuredLogger
from runtime.bot_state import ERROR, PAUSED, RUNNING, STOPPED
from runtime.state import RuntimeStore, build_runtime_state, get_live_gate_status
from storage.db import DEFAULT_DB_PATH, get_connection, initialize_database
from storage.repository import StorageRepository


BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "ui" / "templates"
STATIC_DIR = BASE_DIR / "ui" / "static"
LOG_FILE_MAP = {
    "system": BASE_DIR / "logs" / "system.log",
    "trade": BASE_DIR / "logs" / "trade.log",
    "error": BASE_DIR / "logs" / "error.log",
}
SYMBOL_PATTERN = re.compile(r"^[A-Z0-9]+USDT$")
BOOLEAN_FORM_VALUES = {"true": True, "false": False}
LIVE_CONFIRM_ENV_VAR = "TRADEBOT_CONFIRM_LIVE"
REAL_EXECUTE_ENV_VAR = "TRADEBOT_EXECUTE_REAL"
FINAL_REAL_ORDER_ENV_VAR = "TRADEBOT_FINAL_REAL_ORDER"
DASHBOARD_MARKET_DATA_TIMEOUT_SECONDS = 1
DASHBOARD_MARKET_DATA_CACHE_SECONDS = 60
FUTURES_MARKET_DATA_TIMEOUT_SECONDS = 3
_MARKET_DATA_CACHE: dict[str, object] = {
    "expires_at": 0.0,
    "status": None,
}


app = FastAPI(title="TraderBot Console")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@app.on_event("startup")
def startup_database() -> None:
    initialize_database()


def _read_runtime_status(status_file: str) -> dict:
    path = Path(status_file)
    if not path.exists():
        return {
            "robot_status": "unknown",
            "broker_name": "paper",
            "conservative_mode": False,
            "consecutive_errors": 0,
            "last_error": None,
            "startup_synced": False,
            "symbols": {},
            "last_sync": {
                "cash_balance": 0.0,
                "positions": [],
                "open_orders": [],
                "enabled_symbols": [],
                "warnings": ["runtime_status_not_found"],
                "synced_at": None,
            },
            "account_reconciliation": {
                "configured": False,
                "query_ok": False,
                "status": "unknown",
                "warnings": ["runtime_status_not_found"],
                "error": None,
                "nonzero_assets": [],
                "open_orders": [],
                "checked_at": None,
            },
        }
    return json.loads(path.read_text(encoding="utf-8"))


def _read_paper_state(state_file: str) -> dict:
    path = Path(state_file)
    if not path.exists():
        return {"cash_balance": 0.0, "positions": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def _account_risk_state_file(execution_config) -> str:
    return str(Path(execution_config.runtime_state_file).with_name("account_risk.json"))


def _public_market_data_status(settings: dict, execution_config) -> dict:
    enabled_symbols = list(execution_config.enabled_symbols)
    symbol = enabled_symbols[0] if enabled_symbols else "n/a"
    cached_status = _MARKET_DATA_CACHE.get("status")
    if (
        cached_status is not None
        and time.monotonic() < float(_MARKET_DATA_CACHE["expires_at"])
        and cached_status.get("symbol") == symbol
    ):
        return dict(cached_status)

    if not enabled_symbols:
        status = {
            "base_url": execution_config.exchange.base_url,
            "timeout_seconds": execution_config.exchange.request_timeout_seconds,
            "symbol": "n/a",
            "ping_ok": False,
            "server_time": "n/a",
            "ticker_price": "n/a",
            "exchange_info_ok": False,
            "status": "idle",
            "error": None,
            "message": "No active symbols",
        }
        _MARKET_DATA_CACHE["status"] = dict(status)
        _MARKET_DATA_CACHE["expires_at"] = time.monotonic() + DASHBOARD_MARKET_DATA_CACHE_SECONDS
        return status

    client = BinanceClient(
        base_url=execution_config.exchange.base_url,
        timeout=min(
            execution_config.exchange.request_timeout_seconds,
            DASHBOARD_MARKET_DATA_TIMEOUT_SECONDS,
        ),
        error_log_file=execution_config.error_log_file,
    )
    status = {
        "base_url": execution_config.exchange.base_url,
        "timeout_seconds": execution_config.exchange.request_timeout_seconds,
        "symbol": symbol or "n/a",
        "ping_ok": False,
        "server_time": "n/a",
        "ticker_price": "n/a",
        "exchange_info_ok": False,
        "status": "checking",
        "error": None,
    }
    checks = {
        "ping": client.ping,
        "server_time": client.get_server_time,
    }
    if symbol:
        checks["ticker_price"] = lambda: client.get_ticker_price(symbol)
        checks["exchangeInfo"] = lambda: client.get_symbol_info(symbol)

    errors: list[str] = []
    executor = ThreadPoolExecutor(max_workers=len(checks))
    futures = {executor.submit(check): name for name, check in checks.items()}
    done, pending = wait(futures, timeout=DASHBOARD_MARKET_DATA_TIMEOUT_SECONDS)

    for future in pending:
        future.cancel()
        errors.append(f"{futures[future]}: request timed out")
    executor.shutdown(wait=False, cancel_futures=True)

    for future in done:
        check_name = futures[future]
        try:
            result = future.result()
        except Exception as exc:
            errors.append(f"{check_name}: {exc}")
            continue

        if check_name == "ping":
            status["ping_ok"] = bool(result)
        elif check_name == "server_time":
            status["server_time"] = result.get("serverTime", "n/a")
        elif check_name == "ticker_price":
            status["ticker_price"] = result.get("price", "n/a")
        elif check_name == "exchangeInfo":
            status["exchange_info_ok"] = True

    if errors:
        status["error"] = " | ".join(errors)
        status["status"] = "error"
    elif status["ping_ok"]:
        status["status"] = "ok"
    _MARKET_DATA_CACHE["status"] = dict(status)
    _MARKET_DATA_CACHE["expires_at"] = time.monotonic() + DASHBOARD_MARKET_DATA_CACHE_SECONDS
    return status


def _dashboard_context() -> dict:
    settings = load_project_config()
    execution_config = load_execution_runtime(settings)
    runtime_status = _read_runtime_status(execution_config.status_file)
    live_gate = get_live_gate_status(execution_config)
    runtime_state = build_runtime_state(execution_config)
    safety = settings.get("safety", {})
    manual_real_order_available = (
        execution_config.mode == "live"
        and execution_config.allow_live_trading
        and execution_config.live_execute_enabled
        and bool(safety.get("real_order_method_enabled", False))
        and live_gate.confirm_env_ok
        and live_gate.real_execute_env_ok
        and os.environ.get(FINAL_REAL_ORDER_ENV_VAR) == "YES"
    )
    account_risk = get_account_risk_status(state_file=_account_risk_state_file(execution_config))
    enabled_symbols = list(execution_config.enabled_symbols)
    configured_symbols = list(_configured_symbol_names())
    bot_idle = not enabled_symbols
    return {
        "project_name": "TraderBot Local Console",
        "mode": execution_config.mode,
        "current_broker": runtime_state.broker_name,
        "live_gate": live_gate,
        "allow_live_trading": execution_config.allow_live_trading,
        "live_execute_enabled": execution_config.live_execute_enabled,
        "require_manual_confirm": execution_config.require_manual_confirm,
        "live_confirm_env_var": LIVE_CONFIRM_ENV_VAR,
        "live_confirm_env_is_yes": live_gate.confirm_env_ok,
        "real_execute_env_var": REAL_EXECUTE_ENV_VAR,
        "real_execute_env_is_yes": live_gate.real_execute_env_ok,
        "real_trading_enabled": live_gate.real_trading_enabled,
        "uses_real_order_api": live_gate.uses_real_order_api,
        "auto_strategy_real_order_enabled": False,
        "manual_real_order_available": manual_real_order_available,
        "is_live_mode": execution_config.mode == "live",
        "bot_status": "Bot idle" if bot_idle else runtime_status.get("robot_status", "unknown"),
        "bot_idle": bot_idle,
        "no_symbols_configured": not configured_symbols,
        "is_error_status": runtime_status.get("robot_status") == ERROR,
        "enabled_symbols": enabled_symbols,
        "configured_symbols": configured_symbols,
        "current_time": datetime.now(timezone.utc),
        "runtime_status": runtime_status,
        "account_reconciliation": runtime_status.get("account_reconciliation", {}),
        "account_risk": account_risk_status_payload(account_risk),
        "public_market_data": _public_market_data_status(settings, execution_config),
        "binance_credentials": load_binance_readonly_credentials().public_status(),
        "settings": settings,
    }


def _read_last_lines(path: Path, line_count: int = 100) -> list[str]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        return [line.rstrip("\n") for line in deque(handle, maxlen=line_count)]


def _latest_non_empty_line(path: Path) -> str | None:
    for line in reversed(_read_last_lines(path, line_count=200)):
        cleaned = line.strip()
        if cleaned:
            return cleaned
    return None


def _path_updated_at(path: Path) -> str | None:
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def _sqlite_health() -> dict:
    try:
        initialize_database(DEFAULT_DB_PATH)
        with get_connection(DEFAULT_DB_PATH) as connection:
            connection.execute("CREATE TEMP TABLE IF NOT EXISTS health_check (value TEXT)")
            connection.execute("DELETE FROM health_check")
            connection.execute("INSERT INTO health_check (value) VALUES (?)", ("ok",))
            row = connection.execute("SELECT value FROM health_check LIMIT 1").fetchone()
            connection.commit()
        return {
            "ok": bool(row and row["value"] == "ok"),
            "status": "writable" if row and row["value"] == "ok" else "read_failed",
            "path": str(DEFAULT_DB_PATH),
            "error": None,
        }
    except sqlite3.Error as exc:
        return {
            "ok": False,
            "status": "error",
            "path": str(DEFAULT_DB_PATH),
            "error": str(exc),
        }


def _account_api_health(settings: dict, execution_config) -> dict:
    credentials = load_binance_readonly_credentials()
    credential_status = credentials.public_status()
    if not credentials.configured:
        return {
            "configured": False,
            "status": "missing",
            "ok": False,
            "error": None,
            "api_key_configured": credential_status["api_key_configured"],
            "api_secret_configured": credential_status["api_secret_configured"],
        }

    try:
        client = BinanceClient(
            base_url=execution_config.exchange.base_url,
            timeout=min(execution_config.exchange.request_timeout_seconds, 3),
            error_log_file=execution_config.error_log_file,
            recv_window=execution_config.exchange.recv_window,
            credentials=credentials,
        )
        balances = client.get_account_balances()
        return {
            "configured": True,
            "status": "ok",
            "ok": True,
            "error": None,
            "balance_count": len(balances),
            "api_key_configured": credential_status["api_key_configured"],
            "api_secret_configured": credential_status["api_secret_configured"],
        }
    except Exception as exc:
        return {
            "configured": True,
            "status": "error",
            "ok": False,
            "error": str(exc),
            "api_key_configured": credential_status["api_key_configured"],
            "api_secret_configured": credential_status["api_secret_configured"],
        }


def _health_context() -> dict:
    settings = load_project_config()
    execution_config = load_execution_runtime(settings)
    runtime_status = _read_runtime_status(execution_config.status_file)
    runtime_state = build_runtime_state(execution_config)
    live_gate = get_live_gate_status(execution_config)
    public_market_data = _public_market_data_status(settings, execution_config)
    account_api = _account_api_health(settings, execution_config)
    account_risk = get_account_risk_status(state_file=_account_risk_state_file(execution_config))
    sqlite_status = _sqlite_health()
    last_error_log = _latest_non_empty_line(LOG_FILE_MAP["error"])
    status_path = Path(execution_config.status_file)
    enabled_symbols = list(execution_config.enabled_symbols)
    configured_symbols = list(_configured_symbol_names())
    bot_idle = not enabled_symbols

    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "web_app": {
            "running": True,
            "status": "ok",
            "message": "web_app is serving this health response",
        },
        "bot_runtime": {
            "status": "idle" if bot_idle else runtime_status.get("robot_status", "unknown"),
            "idle": bot_idle,
            "message": "No active symbols" if bot_idle else None,
            "conservative_mode": bool(runtime_status.get("conservative_mode", False)),
            "consecutive_errors": int(runtime_status.get("consecutive_errors", 0) or 0),
            "last_error": runtime_status.get("last_error"),
            "startup_synced": bool(runtime_status.get("startup_synced", False)),
        },
        "app_mode": execution_config.mode,
        "broker": runtime_state.broker_name,
        "real_trading": {
            "enabled": live_gate.real_trading_enabled,
            "uses_real_order_api": live_gate.uses_real_order_api,
            "status": "ENABLED" if live_gate.real_trading_enabled else "DISABLED",
        },
        "binance_public_api": {
            "status": public_market_data.get("status", "ok" if public_market_data.get("ping_ok") else "error"),
            "ping_ok": bool(public_market_data.get("ping_ok")),
            "base_url": public_market_data.get("base_url"),
            "symbol": public_market_data.get("symbol"),
            "ticker_price": public_market_data.get("ticker_price"),
            "exchange_info_ok": bool(public_market_data.get("exchange_info_ok")),
            "error": public_market_data.get("error"),
        },
        "binance_account_api": account_api,
        "account_risk": account_risk_status_payload(account_risk),
        "sqlite": sqlite_status,
        "last_bot_loop_time": _path_updated_at(status_path),
        "last_error_log": last_error_log,
        "port_8000": {
            "status": "serving",
            "message": "FastAPI web console is expected on http://127.0.0.1:8000",
        },
        "enabled_symbols": enabled_symbols,
        "configured_symbols": configured_symbols,
        "no_symbols_configured": not configured_symbols,
        "live_gate": asdict(live_gate),
        "api_key": {
            "configured": account_api["configured"],
            "api_key_configured": account_api["api_key_configured"],
            "api_secret_configured": account_api["api_secret_configured"],
            "value": "[hidden]",
        },
    }


def _read_recent_log_lines(path: Path, *, symbol: str | None = None, line_count: int = 100) -> list[str]:
    if not path.exists():
        return []

    tail_limit = line_count if symbol is None else max(line_count * 20, 1000)
    with path.open("r", encoding="utf-8") as handle:
        recent_lines = deque(handle, maxlen=tail_limit)

    cleaned_lines = [line.rstrip("\n") for line in recent_lines]
    if symbol is None:
        return cleaned_lines[-line_count:]

    filtered_lines = [line for line in cleaned_lines if symbol in line]
    return filtered_lines[-line_count:]


def _configured_symbol_names() -> tuple[str, ...]:
    try:
        settings = load_project_config()
    except Exception:
        return ()
    symbols = settings.get("symbols_config", {}).get("symbols", {})
    if not isinstance(symbols, dict):
        return ()
    return tuple(symbols.keys())


def _format_yaml_scalar(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    text = str(value)
    if text == "":
        return '""'
    if re.fullmatch(r"[A-Za-z0-9_./:-]+", text):
        return text
    return '"' + text.replace('"', '\\"') + '"'


def _dump_yaml(data: dict) -> str:
    lines: list[str] = []

    def write_value(value, indent: int, key: str | None = None):
        prefix = " " * indent
        if isinstance(value, dict):
            if key is not None:
                lines.append(f"{prefix}{key}:")
            for child_key, child_value in value.items():
                write_value(child_value, indent + (2 if key is not None else 0), str(child_key))
            return
        if isinstance(value, list):
            if key is not None:
                lines.append(f"{prefix}{key}:")
                item_indent = indent + 2
            else:
                item_indent = indent
            for item in value:
                item_prefix = " " * item_indent
                if isinstance(item, (dict, list)):
                    lines.append(f"{item_prefix}-")
                    write_value(item, item_indent + 2)
                else:
                    lines.append(f"{item_prefix}- {_format_yaml_scalar(item)}")
            return
        if key is None:
            lines.append(f"{prefix}{_format_yaml_scalar(value)}")
        else:
            lines.append(f"{prefix}{key}: {_format_yaml_scalar(value)}")

    for top_key, top_value in data.items():
        write_value(top_value, 0, str(top_key))
    return "\n".join(lines) + "\n"


def _write_symbols_config(symbols_config: dict) -> None:
    DEFAULT_SYMBOLS_PATH.write_text(_dump_yaml(symbols_config), encoding="utf-8")


def _write_settings_config(settings: dict) -> None:
    settings_to_save = {key: value for key, value in settings.items() if key != "symbols_config"}
    DEFAULT_SETTINGS_PATH.write_text(_dump_yaml(settings_to_save), encoding="utf-8")


def _enabled_symbols_from_config(symbols_config: dict) -> list[str]:
    symbols = symbols_config.get("symbols", {})
    if not isinstance(symbols, dict):
        return []
    return [
        symbol
        for symbol, symbol_config in symbols.items()
        if symbol_config.get("enabled", True) and not symbol_config.get("paused_by_loss", False)
    ]


def _sync_settings_enabled_symbols(settings: dict, symbols_config: dict) -> None:
    settings.setdefault("execution", {})["enabled_symbols"] = _enabled_symbols_from_config(symbols_config)


def _save_symbols_and_settings(settings: dict, symbols_config: dict) -> None:
    _sync_settings_enabled_symbols(settings, symbols_config)
    _write_symbols_config(symbols_config)
    _write_settings_config(settings)


def _log_symbol_management_action(settings: dict, *, symbol: str, action: str, reason: str, **extra) -> None:
    logging_config = settings.get("logging", {})
    logger = StructuredLogger(str(BASE_DIR / logging_config.get("system_log_file", "logs/system.log")))
    logger.log(
        symbol=symbol,
        action=action,
        reason=reason,
        mode=settings.get("app", {}).get("mode", "paper"),
        **extra,
    )


async def _read_form_data(request: Request) -> dict[str, str]:
    body = (await request.body()).decode("utf-8")
    parsed = parse_qs(body, keep_blank_values=True)
    return {key: values[-1] if values else "" for key, values in parsed.items()}


def _parse_form_bool(form: dict[str, str], field_name: str) -> bool:
    value = form.get(field_name, "").strip().lower()
    if value not in BOOLEAN_FORM_VALUES:
        raise ValueError(f"{field_name} must be true or false")
    return BOOLEAN_FORM_VALUES[value]


def _parse_positive_amount(form: dict[str, str], field_name: str) -> float:
    raw_value = form.get(field_name, "").strip()
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a number greater than 0") from exc
    if value <= 0:
        raise ValueError(f"{field_name} must be greater than 0")
    return value


def _parse_timeframe(form: dict[str, str], field_name: str) -> str:
    value = form.get(field_name, "").strip()
    if value not in VALID_SYMBOL_TIMEFRAMES:
        allowed = ", ".join(VALID_SYMBOL_TIMEFRAMES)
        raise ValueError(f"{field_name} must be one of: {allowed}")
    return value


def _symbol_config_from_form(form: dict[str, str]) -> dict:
    return {
        "enabled": _parse_form_bool(form, "enabled"),
        "trend_timeframe": _parse_timeframe(form, "trend_timeframe"),
        "signal_timeframe": _parse_timeframe(form, "signal_timeframe"),
        "order_amount": _parse_positive_amount(form, "order_amount"),
        "max_loss_amount": _parse_positive_amount(form, "max_loss_amount"),
        "paused_by_loss": _parse_form_bool(form, "paused_by_loss"),
    }


def _load_config_view() -> dict:
    try:
        settings = load_project_config()
    except Exception as exc:
        return {
            "config_error": str(exc),
            "settings_view": None,
            "symbols_view": None,
        }

    settings_view = {
        "app_mode": settings.get("app", {}).get("mode"),
        "exchange_name": settings.get("exchange", {}).get("name"),
        "exchange_base_url": settings.get("exchange", {}).get("base_url", "https://api.binance.com"),
        "exchange_request_timeout_seconds": settings.get("exchange", {}).get(
            "request_timeout_seconds",
            10,
        ),
        "default_symbol": settings.get("market", {}).get("default_symbol"),
        "default_symbols": settings.get("market", {}).get("default_symbols", []),
        "enabled_symbols": settings.get("execution", {}).get("enabled_symbols", []),
        "polling_interval_seconds": settings.get("market", {}).get("polling_interval_seconds"),
        "risk": settings.get("risk", {}),
    }
    symbols_view = settings.get("symbols_config", {})
    return {
        "config_error": None,
        "settings_view": settings_view,
        "symbols_view": symbols_view,
    }


def _load_symbols_view(message: str | None = None, error: str | None = None) -> dict:
    try:
        settings = load_project_config()
        symbols_config = settings.get("symbols_config", {})
        symbols = symbols_config.get("symbols", {})
        if not isinstance(symbols, dict):
            symbols = {}
    except Exception as exc:
        return {
            "config_error": str(exc),
            "symbols": {},
            "timeframes": VALID_SYMBOL_TIMEFRAMES,
            "message": message,
            "error": error,
        }

    return {
        "config_error": None,
        "symbols": symbols,
        "timeframes": VALID_SYMBOL_TIMEFRAMES,
        "message": message,
        "error": error,
    }


def _load_symbol_edit_context(
    request: Request,
    symbol: str,
    symbol_config: dict | None = None,
    error: str | None = None,
) -> dict:
    normalized_symbol = symbol.strip().upper()
    if symbol_config is None:
        settings = load_project_config()
        symbols = settings.get("symbols_config", {}).get("symbols", {})
        if not isinstance(symbols, dict) or normalized_symbol not in symbols:
            raise ValueError(f"{normalized_symbol} is not configured")
        symbol_config = symbols[normalized_symbol]

    return {
        "request": request,
        "project_name": "TraderBot Local Console",
        "symbol": normalized_symbol,
        "symbol_config": symbol_config,
        "timeframes": VALID_SYMBOL_TIMEFRAMES,
        "error": error,
    }


def _to_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_optional_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _futures_position_is_nonzero(position: dict[str, object]) -> bool:
    return (_to_optional_float(position.get("positionAmt")) or 0.0) != 0.0


def _futures_position_row(position: dict[str, object]) -> dict[str, object]:
    return {
        "symbol": position.get("symbol"),
        "position_side": position.get("positionSide"),
        "position_amount": _to_optional_float(position.get("positionAmt")),
        "entry_price": _to_optional_float(position.get("entryPrice")),
        "mark_price": _to_optional_float(position.get("markPrice")),
        "unrealized_pnl": _to_optional_float(
            position.get("unRealizedProfit", position.get("unrealizedProfit"))
        ),
        "liquidation_price": _to_optional_float(position.get("liquidationPrice")),
        "leverage": position.get("leverage"),
        "margin_type": position.get("marginType"),
    }


def _load_futures_view() -> dict:
    futures_credentials = load_futures_binance_readonly_credentials().public_status()
    futures_account = {
        "api_key_status": "configured" if futures_credentials["configured"] else "missing",
        "query_status": "not_configured",
        "wallet_balance": None,
        "available_balance": None,
        "margin_balance": None,
        "unrealized_pnl": None,
        "error": None,
    }
    futures_positions: list[dict[str, object]] = []
    futures_risk_controls = {
        "max_leverage": None,
        "max_margin_per_trade_usdt": None,
        "max_position_ratio": None,
        "min_liquidation_distance_pct": None,
        "max_funding_rate_abs": None,
        "max_consecutive_losing_trades": None,
    }
    try:
        futures_config = load_futures_config()
    except Exception as exc:
        return {
            "status": "public-data-only",
            "base_url": "n/a",
            "enabled_symbols": [],
            "futures_credentials": futures_credentials,
            "futures_account": futures_account,
            "futures_positions": futures_positions,
            "futures_risk_controls": futures_risk_controls,
            "rows": [],
            "warnings": [f"Futures config error: {exc}"],
            "config_error": str(exc),
        }

    enabled_symbols = list(futures_config.enabled_symbols)
    futures_risk_controls.update(
        {
            "max_leverage": futures_config.risk.max_leverage,
            "max_margin_per_trade_usdt": futures_config.risk.max_margin_per_trade_usdt,
            "max_position_ratio": futures_config.risk.max_position_ratio,
            "min_liquidation_distance_pct": futures_config.risk.min_liquidation_distance_pct,
            "max_funding_rate_abs": futures_config.risk.max_funding_rate_abs,
            "max_consecutive_losing_trades": futures_config.risk.max_consecutive_losing_trades,
        }
    )
    warnings = []
    if not futures_credentials["configured"]:
        warnings.append("Futures API key missing")
    context = {
        "status": "public-data-only",
        "base_url": futures_config.futures.base_url,
        "enabled_symbols": enabled_symbols,
        "futures_credentials": futures_credentials,
        "futures_account": futures_account,
        "futures_positions": futures_positions,
        "futures_risk_controls": futures_risk_controls,
        "rows": [],
        "warnings": warnings,
        "config_error": None,
    }

    client = BinanceFuturesClient(
        base_url=futures_config.futures.base_url,
        timeout=min(
            futures_config.futures.request_timeout_seconds,
            FUTURES_MARKET_DATA_TIMEOUT_SECONDS,
        ),
    )

    if futures_credentials["configured"]:
        try:
            balance_payload = client.get_futures_balance()
            if isinstance(balance_payload, list):
                usdt_balance = next(
                    (
                        balance
                        for balance in balance_payload
                        if isinstance(balance, dict) and balance.get("asset") == "USDT"
                    ),
                    {},
                )
                futures_account.update(
                    {
                        "query_status": "ok",
                        "wallet_balance": _to_optional_float(usdt_balance.get("walletBalance")),
                        "available_balance": _to_optional_float(usdt_balance.get("availableBalance")),
                        "margin_balance": _to_optional_float(usdt_balance.get("marginBalance")),
                        "unrealized_pnl": _to_optional_float(usdt_balance.get("unrealizedProfit")),
                        "error": None,
                    }
                )
            elif isinstance(balance_payload, dict) and balance_payload.get("error"):
                message = str(balance_payload.get("message") or balance_payload.get("error"))
                futures_account.update({"query_status": "error", "error": message})
                context["warnings"].append(message)
            else:
                message = "Futures balance response was not recognized"
                futures_account.update({"query_status": "error", "error": message})
                context["warnings"].append(message)
        except Exception as exc:
            message = f"Futures account query failed: {exc}"
            futures_account.update({"query_status": "error", "error": message})
            context["warnings"].append(message)

        try:
            positions_payload = client.get_futures_positions()
            if isinstance(positions_payload, list):
                futures_positions.extend(
                    _futures_position_row(position)
                    for position in positions_payload
                    if isinstance(position, dict) and _futures_position_is_nonzero(position)
                )
            elif isinstance(positions_payload, dict) and positions_payload.get("error"):
                message = str(positions_payload.get("message") or positions_payload.get("error"))
                context["warnings"].append(message)
            else:
                context["warnings"].append("Futures positions response was not recognized")
        except Exception as exc:
            context["warnings"].append(f"Futures positions query failed: {exc}")

    if not enabled_symbols:
        return context

    for symbol in enabled_symbols:
        row = {
            "symbol": symbol,
            "ticker_price": None,
            "mark_price": None,
            "funding_rate": None,
            "next_funding_time": None,
            "min_notional": None,
            "tick_size": None,
            "step_size": None,
            "warning": None,
            "funding_warning": None,
            "funding_rate_exceeds_max": None,
        }
        try:
            symbol_info = client.get_symbol_info(symbol)
            rules = parse_futures_symbol_rules(symbol_info)
            ticker_payload = client.get_ticker_price(symbol)
            mark_payload = client.get_mark_price(symbol)

            row.update(
                {
                    "ticker_price": _to_optional_float(ticker_payload.get("price")),
                    "mark_price": _to_optional_float(mark_payload.get("markPrice")),
                    "next_funding_time": mark_payload.get("nextFundingTime"),
                    "min_notional": rules.min_notional,
                    "tick_size": rules.tick_size,
                    "step_size": rules.step_size,
                }
            )
        except Exception as exc:
            message = f"{symbol}: {exc}"
            row["warning"] = message
            context["warnings"].append(message)
            context["rows"].append(row)
            continue

        try:
            funding_payload = client.get_funding_rate(symbol, limit=1)
            if isinstance(funding_payload, list) and funding_payload:
                row["funding_rate"] = _to_optional_float(funding_payload[0].get("fundingRate"))
                if row["funding_rate"] is not None:
                    row["funding_rate_exceeds_max"] = (
                        abs(row["funding_rate"]) > futures_config.risk.max_funding_rate_abs
                    )
        except Exception as exc:
            message = f"{symbol} funding rate unavailable: {exc}"
            row["funding_warning"] = message
            context["warnings"].append(message)

        context["rows"].append(row)

    return context


def _load_positions_view() -> dict:
    try:
        settings = load_project_config()
        execution_config = load_execution_runtime(settings)
        runtime_status = _read_runtime_status(execution_config.status_file)
        paper_state = _read_paper_state(execution_config.paper_state_file)
        sqlite_positions = StorageRepository().get_latest_position_snapshots(mode=execution_config.mode)
    except Exception as exc:
        return {
            "config_error": str(exc),
            "positions": [],
            "has_configured_symbols": False,
            "has_active_symbols": False,
        }

    symbols_config = settings.get("symbols_config", {}).get("symbols", {})
    if not isinstance(symbols_config, dict):
        symbols_config = {}

    paper_positions = paper_state.get("positions", {})
    if not isinstance(paper_positions, dict):
        paper_positions = {}

    runtime_positions = runtime_status.get("last_sync", {}).get("positions", [])
    runtime_position_symbols = {
        str(position.get("symbol"))
        for position in runtime_positions
        if isinstance(position, dict) and position.get("symbol")
    }
    symbols = sorted(set(symbols_config) | set(paper_positions) | runtime_position_symbols | set(sqlite_positions))

    rows = []
    for symbol in symbols:
        symbol_config = symbols_config.get(symbol, {})
        paper_position = paper_positions.get(symbol, {})
        sqlite_position = sqlite_positions.get(symbol, {})

        quantity = _to_float(paper_position.get("qty"), _to_float(sqlite_position.get("quantity")))
        avg_price = _to_float(paper_position.get("avg_price"), _to_float(sqlite_position.get("avg_price")))
        current_price = _to_float(sqlite_position.get("current_price"), avg_price)
        market_value = quantity * current_price if quantity > 0 else _to_float(sqlite_position.get("market_value"))
        unrealized_pnl = (
            (current_price - avg_price) * quantity
            if quantity > 0
            else _to_float(sqlite_position.get("unrealized_pnl"))
        )

        rows.append(
            {
                "symbol": symbol,
                "enabled": bool(symbol_config.get("enabled", False)),
                "paused_by_loss": bool(symbol_config.get("paused_by_loss", False)),
                "quantity": quantity,
                "avg_price": avg_price,
                "current_price": current_price,
                "market_value": market_value,
                "unrealized_pnl": unrealized_pnl,
                "order_amount": symbol_config.get("order_amount", "n/a"),
                "max_loss_amount": symbol_config.get("max_loss_amount", "n/a"),
            }
        )

    return {
        "config_error": None,
        "positions": rows,
        "has_configured_symbols": bool(symbols_config),
        "has_active_symbols": bool(execution_config.enabled_symbols),
    }


def _format_balance_row(balance: dict) -> dict:
    free = _to_float(balance.get("free"))
    locked = _to_float(balance.get("locked"))
    return {
        "asset": str(balance.get("asset", "")),
        "free": free,
        "locked": locked,
        "total": free + locked,
    }


def _load_account_view() -> dict:
    credentials = load_binance_readonly_credentials()
    credentials_status = credentials.public_status()
    context = {
        "credentials": credentials_status,
        "query_ok": False,
        "query_error": None,
        "usdt_balance": None,
        "nonzero_balances": [],
        "updated_at": None,
    }
    if not credentials.configured:
        context["query_error"] = "BINANCE_API_KEY and BINANCE_API_SECRET are required for read-only account queries."
        return context

    try:
        settings = load_project_config()
        execution_config = load_execution_runtime(settings)
        client = BinanceClient(
            base_url=execution_config.exchange.base_url,
            timeout=execution_config.exchange.request_timeout_seconds,
            error_log_file=execution_config.error_log_file,
            recv_window=execution_config.exchange.recv_window,
            credentials=credentials,
        )
        balances = client.get_account_balances()
    except BinancePrivateReadOnlyAPIError as exc:
        context["query_error"] = str(exc)
        return context
    except Exception as exc:
        context["query_error"] = f"Account query failed: {exc}"
        return context

    rows = [_format_balance_row(balance) for balance in balances]
    nonzero_rows = sorted(
        [row for row in rows if row["asset"] and row["total"] > 0],
        key=lambda row: row["asset"],
    )
    usdt_balance = next((row for row in rows if row["asset"] == "USDT"), None)
    context.update(
        {
            "query_ok": True,
            "query_error": None,
            "usdt_balance": usdt_balance or {"asset": "USDT", "free": 0.0, "locked": 0.0, "total": 0.0},
            "nonzero_balances": nonzero_rows,
            "updated_at": datetime.now(timezone.utc),
        }
    )
    return context


def _load_performance_view(symbol: str = "") -> dict:
    try:
        settings = load_project_config()
        execution_config = load_execution_runtime(settings)
        repository = StorageRepository()
        latest = repository.get_latest_equity_snapshot(mode=execution_config.mode)
        curve = repository.get_equity_curve(mode=execution_config.mode, limit=500)
        available_symbols = _configured_symbol_names()
    except Exception as exc:
        return {
            "config_error": str(exc),
            "latest": None,
            "cumulative_return": 0.0,
            "has_data": False,
            "available_symbols": (),
            "selected_symbol": "",
            "symbol_has_data": False,
        }

    selected_symbol = symbol.strip().upper()
    if selected_symbol not in available_symbols:
        selected_symbol = available_symbols[0] if available_symbols else ""
    if not available_symbols:
        return {
            "config_error": None,
            "latest": None,
            "cumulative_return": 0.0,
            "has_data": False,
            "available_symbols": available_symbols,
            "selected_symbol": "",
            "symbol_has_data": False,
        }
    symbol_curve = (
        repository.get_symbol_pnl_curve(symbol=selected_symbol, mode=execution_config.mode, limit=500)
        if selected_symbol
        else []
    )

    if latest is None:
        return {
            "config_error": None,
            "latest": None,
            "cumulative_return": 0.0,
            "has_data": False,
            "available_symbols": available_symbols,
            "selected_symbol": selected_symbol,
            "symbol_has_data": bool(symbol_curve),
        }

    first_equity = _to_float(curve[0].get("total_equity")) if curve else _to_float(latest.get("total_equity"))
    current_equity = _to_float(latest.get("total_equity"))
    cumulative_return = current_equity - first_equity
    return {
        "config_error": None,
        "latest": latest,
        "cumulative_return": cumulative_return,
        "has_data": True,
        "available_symbols": available_symbols,
        "selected_symbol": selected_symbol,
        "symbol_has_data": bool(symbol_curve),
    }


def _runtime_store():
    settings = load_project_config()
    execution_config = load_execution_runtime(settings)
    runtime_state = build_runtime_state(execution_config)
    store = RuntimeStore(
        execution_config.runtime_state_file,
        status_path=execution_config.status_file,
        initial_status=execution_config.robot_initial_status,
        mode=execution_config.mode,
        broker_name=runtime_state.broker_name,
    )
    logger = LogRouter(
        system_log=execution_config.system_log_file,
        trade_log=execution_config.trade_log_file,
        error_log=execution_config.error_log_file,
        mode=execution_config.mode,
    )
    return execution_config, store, logger


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    context = _dashboard_context()
    context["request"] = request
    return templates.TemplateResponse(request, "dashboard.html", context)


@app.get("/health", response_class=HTMLResponse)
def health_page(request: Request):
    context = _health_context()
    context.update(
        {
            "request": request,
            "project_name": "TraderBot Local Console",
        }
    )
    return templates.TemplateResponse(request, "health.html", context)


@app.get("/futures", response_class=HTMLResponse)
def futures_page(request: Request):
    context = _load_futures_view()
    context.update(
        {
            "request": request,
            "project_name": "TraderBot Local Console",
        }
    )
    return templates.TemplateResponse(request, "futures.html", context)


@app.get("/api/health")
def health_api():
    return JSONResponse(_health_context())


@app.post("/api/account_risk/reset")
def account_risk_reset_api(request: Request):
    settings = load_project_config()
    execution_config = load_execution_runtime(settings)
    state = reset_account_risk(
        state_file=_account_risk_state_file(execution_config),
        system_log_file=execution_config.system_log_file,
        mode=execution_config.mode,
    )
    accept_header = request.headers.get("accept", "")
    if "text/html" in accept_header:
        return RedirectResponse(url="/", status_code=303)
    return JSONResponse(account_risk_status_payload(state))


@app.get("/logs", response_class=HTMLResponse)
def logs_page(request: Request, log_type: str = "system", symbol: str = "all"):
    selected_type = log_type if log_type in LOG_FILE_MAP else "system"
    symbols = _configured_symbol_names()
    selected_symbol = symbol.strip().upper()
    if selected_symbol not in symbols:
        selected_symbol = "all"
    symbol_filter = None if selected_symbol == "all" else selected_symbol
    lines = _read_recent_log_lines(LOG_FILE_MAP[selected_type], symbol=symbol_filter, line_count=100)
    return templates.TemplateResponse(
        request,
        "logs.html",
        {
            "project_name": "TraderBot Local Console",
            "selected_log_type": selected_type,
            "available_log_types": tuple(LOG_FILE_MAP.keys()),
            "selected_symbol": selected_symbol,
            "available_symbols": symbols,
            "log_lines": lines,
            "log_exists": LOG_FILE_MAP[selected_type].exists(),
            "empty_message": "暂无日志" if symbol_filter is None else "该币种暂无日志",
        },
    )


@app.get("/api/equity_curve")
def equity_curve_api():
    settings = load_project_config()
    execution_config = load_execution_runtime(settings)
    rows = StorageRepository().get_equity_curve(mode=execution_config.mode, limit=500)
    return JSONResponse(
        {
            "data": [
                {
                    "timestamp": row.get("timestamp"),
                    "total_equity": _to_float(row.get("total_equity")),
                    "realized_pnl": _to_float(row.get("realized_pnl")),
                    "unrealized_pnl": _to_float(row.get("unrealized_pnl")),
                }
                for row in rows
            ]
        }
    )


@app.get("/api/symbol_pnl_curve")
def symbol_pnl_curve_api(symbol: str):
    settings = load_project_config()
    execution_config = load_execution_runtime(settings)
    selected_symbol = symbol.strip().upper()
    if selected_symbol not in _configured_symbol_names():
        return JSONResponse({"data": []})
    rows = StorageRepository().get_symbol_pnl_curve(
        symbol=selected_symbol,
        mode=execution_config.mode,
        limit=500,
    )
    return JSONResponse(
        {
            "data": [
                {
                    "timestamp": row.get("timestamp"),
                    "symbol": row.get("symbol"),
                    "realized_pnl": _to_float(row.get("realized_pnl")),
                    "unrealized_pnl": _to_float(row.get("unrealized_pnl")),
                    "total_pnl": _to_float(row.get("total_pnl")),
                }
                for row in rows
            ]
        }
    )


@app.get("/performance", response_class=HTMLResponse)
def performance_page(request: Request, symbol: str = ""):
    context = _load_performance_view(symbol=symbol)
    context.update(
        {
            "request": request,
            "project_name": "TraderBot Local Console",
        }
    )
    return templates.TemplateResponse(request, "performance.html", context)


@app.get("/positions", response_class=HTMLResponse)
def positions_page(request: Request):
    context = _load_positions_view()
    context.update(
        {
            "request": request,
            "project_name": "TraderBot Local Console",
        }
    )
    return templates.TemplateResponse(request, "positions.html", context)


@app.get("/account", response_class=HTMLResponse)
def account_page(request: Request):
    context = _load_account_view()
    context.update(
        {
            "request": request,
            "project_name": "TraderBot Local Console",
        }
    )
    return templates.TemplateResponse(request, "account.html", context)


@app.get("/config", response_class=HTMLResponse)
def config_page(request: Request):
    context = _load_config_view()
    context.update(
        {
            "project_name": "TraderBot Local Console",
        }
    )
    return templates.TemplateResponse(request, "config.html", context)


@app.get("/symbols", response_class=HTMLResponse)
def symbols_page(request: Request, message: str | None = None, error: str | None = None):
    context = _load_symbols_view(message=message, error=error)
    context.update(
        {
            "request": request,
            "project_name": "TraderBot Local Console",
        }
    )
    return templates.TemplateResponse(request, "symbols.html", context)


@app.post("/symbols/add")
async def add_symbol(request: Request):
    form = await _read_form_data(request)
    symbol = form.get("symbol", "").strip().upper()
    if not SYMBOL_PATTERN.fullmatch(symbol):
        return RedirectResponse(
            url="/symbols?error=Symbol%20must%20contain%20only%20A-Z%2C%20digits%2C%20and%20end%20with%20USDT",
            status_code=303,
        )

    settings = load_project_config()
    symbols_config = settings["symbols_config"]
    symbols = symbols_config.setdefault("symbols", {})
    if symbol in symbols:
        return RedirectResponse(url=f"/symbols?error={symbol}%20already%20exists", status_code=303)

    symbols[symbol] = {
        "enabled": True,
        "trend_timeframe": "4h",
        "signal_timeframe": "15m",
        "order_amount": 100.0,
        "max_loss_amount": 20.0,
        "paused_by_loss": False,
    }
    _save_symbols_and_settings(settings, symbols_config)
    _log_symbol_management_action(settings, symbol=symbol, action="symbol_add", reason="dashboard_symbols_add")
    return RedirectResponse(url=f"/symbols?message={symbol}%20added", status_code=303)


@app.get("/symbols/{symbol}/edit", response_class=HTMLResponse)
def edit_symbol_page(request: Request, symbol: str):
    normalized_symbol = symbol.strip().upper()
    try:
        context = _load_symbol_edit_context(request, normalized_symbol)
    except Exception as exc:
        return RedirectResponse(url=f"/symbols?error={quote(str(exc))}", status_code=303)
    return templates.TemplateResponse(request, "symbol_edit.html", context)


@app.post("/symbols/{symbol}/edit", response_class=HTMLResponse)
async def save_symbol_page(request: Request, symbol: str):
    normalized_symbol = symbol.strip().upper()
    form = await _read_form_data(request)
    try:
        settings = load_project_config()
        symbols_config = settings["symbols_config"]
        symbols = symbols_config.get("symbols", {})
        if normalized_symbol not in symbols:
            raise ValueError(f"{normalized_symbol} is not configured")

        updated_config = _symbol_config_from_form(form)
        symbols[normalized_symbol].update(updated_config)
        _save_symbols_and_settings(settings, symbols_config)
        _log_symbol_management_action(
            settings,
            symbol=normalized_symbol,
            action="symbol_update",
            reason="dashboard_symbols_edit",
            enabled=updated_config["enabled"],
            paused_by_loss=updated_config["paused_by_loss"],
            trend_timeframe=updated_config["trend_timeframe"],
            signal_timeframe=updated_config["signal_timeframe"],
            order_amount=updated_config["order_amount"],
            max_loss_amount=updated_config["max_loss_amount"],
        )
    except Exception as exc:
        submitted_config = {
            "enabled": form.get("enabled", "true").strip().lower() == "true",
            "trend_timeframe": form.get("trend_timeframe", "4h"),
            "signal_timeframe": form.get("signal_timeframe", "15m"),
            "order_amount": form.get("order_amount", ""),
            "max_loss_amount": form.get("max_loss_amount", ""),
            "paused_by_loss": form.get("paused_by_loss", "false").strip().lower() == "true",
        }
        context = _load_symbol_edit_context(
            request,
            normalized_symbol,
            symbol_config=submitted_config,
            error=str(exc),
        )
        return templates.TemplateResponse(request, "symbol_edit.html", context, status_code=400)

    return RedirectResponse(url=f"/symbols?message={normalized_symbol}%20saved", status_code=303)


@app.post("/symbols/{symbol}/toggle")
def toggle_symbol(symbol: str):
    normalized_symbol = symbol.strip().upper()
    settings = load_project_config()
    symbols_config = settings["symbols_config"]
    symbols = symbols_config.get("symbols", {})
    if normalized_symbol not in symbols:
        return RedirectResponse(url=f"/symbols?error={normalized_symbol}%20not%20found", status_code=303)

    symbol_config = symbols[normalized_symbol]
    symbol_config["enabled"] = not bool(symbol_config.get("enabled", True))
    _save_symbols_and_settings(settings, symbols_config)
    _log_symbol_management_action(
        settings,
        symbol=normalized_symbol,
        action="symbol_toggle",
        reason="dashboard_symbols_toggle",
        enabled=symbol_config["enabled"],
    )
    state = "enabled" if symbol_config["enabled"] else "disabled"
    return RedirectResponse(url=f"/symbols?message={normalized_symbol}%20{state}", status_code=303)


@app.post("/symbols/{symbol}/delete")
async def delete_symbol(request: Request, symbol: str):
    normalized_symbol = symbol.strip().upper()
    form = await _read_form_data(request)
    if form.get("confirm") != "yes":
        return RedirectResponse(url="/symbols?error=Delete%20confirmation%20missing", status_code=303)

    settings = load_project_config()
    symbols_config = settings["symbols_config"]
    symbols = symbols_config.get("symbols", {})
    if normalized_symbol not in symbols:
        return RedirectResponse(url=f"/symbols?error={normalized_symbol}%20not%20found", status_code=303)

    del symbols[normalized_symbol]
    symbol_files = symbols_config.get("symbol_files", {})
    if isinstance(symbol_files, dict):
        symbol_files.pop(normalized_symbol, None)
    _save_symbols_and_settings(settings, symbols_config)
    _log_symbol_management_action(
        settings,
        symbol=normalized_symbol,
        action="symbol_delete",
        reason="dashboard_symbols_delete",
    )
    return RedirectResponse(url=f"/symbols?message={normalized_symbol}%20deleted", status_code=303)


@app.post("/bot/start")
def bot_start():
    execution_config, runtime_store, logger = _runtime_store()
    if execution_config.mode == "paper":
        runtime_store.set_robot_status(RUNNING)
        runtime_store.set_conservative_mode(False)
        logger.log_system(symbol="-", action="bot_start", reason="dashboard_start")
    else:
        logger.log_system(symbol="-", action="bot_start_blocked", reason="start_allowed_only_in_paper")
    return RedirectResponse(url="/", status_code=303)


@app.post("/bot/stop")
def bot_stop():
    _, runtime_store, logger = _runtime_store()
    runtime_store.set_robot_status(STOPPED)
    logger.log_system(symbol="-", action="bot_stop", reason="dashboard_stop")
    return RedirectResponse(url="/", status_code=303)


@app.post("/bot/pause")
def bot_pause():
    _, runtime_store, logger = _runtime_store()
    runtime_store.set_robot_status(PAUSED)
    logger.log_system(symbol="-", action="bot_pause", reason="dashboard_pause")
    return RedirectResponse(url="/", status_code=303)


if __name__ == "__main__":
    uvicorn.run("web_app:app", host="127.0.0.1", port=8000, reload=False)
