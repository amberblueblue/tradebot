from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from collections import deque
from urllib.parse import parse_qs, quote

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from config.loader import (
    DEFAULT_SETTINGS_PATH,
    DEFAULT_SYMBOLS_PATH,
    VALID_SYMBOL_TIMEFRAMES,
    load_execution_runtime,
    load_project_config,
)
from observability.event_logger import LogRouter, StructuredLogger
from runtime.bot_state import PAUSED, RUNNING, STOPPED
from runtime.state import RuntimeStore, build_runtime_state


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


app = FastAPI(title="TraderBot Console")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _read_runtime_status(status_file: str) -> dict:
    path = Path(status_file)
    if not path.exists():
        return {
            "robot_status": "unknown",
            "conservative_mode": False,
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
        }
    return json.loads(path.read_text(encoding="utf-8"))


def _dashboard_context() -> dict:
    settings = load_project_config()
    execution_config = load_execution_runtime(settings)
    runtime_status = _read_runtime_status(execution_config.status_file)
    return {
        "project_name": "TraderBot Local Console",
        "mode": execution_config.mode,
        "bot_status": runtime_status.get("robot_status", "unknown"),
        "enabled_symbols": list(execution_config.enabled_symbols),
        "current_time": datetime.now(timezone.utc),
        "runtime_status": runtime_status,
        "settings": settings,
    }


def _read_last_lines(path: Path, line_count: int = 100) -> list[str]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        return [line.rstrip("\n") for line in deque(handle, maxlen=line_count)]


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
