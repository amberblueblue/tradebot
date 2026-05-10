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
from types import SimpleNamespace
from urllib.parse import parse_qs, quote, urlencode

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from config.loader import (
    DEFAULT_SETTINGS_PATH,
    DEFAULT_SYMBOLS_PATH,
    VALID_SYMBOL_TIMEFRAMES,
    get_effective_spot_symbol_config,
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
from futures_bot.config_loader import (
    ALLOWED_MARKET_SESSION_FILTERS,
    ALLOWED_FUTURES_STRATEGIES,
    ALLOWED_FUTURES_TIMEFRAMES,
    DEFAULT_FUTURES_SETTINGS_PATH,
    get_effective_futures_symbol_config,
    load_futures_config,
    load_yaml_mapping,
    load_futures_symbols_config,
    save_futures_symbols_config,
)
from futures_bot.exchange.binance_futures_client import BinanceFuturesClient
from futures_bot.exchange.futures_rules import parse_futures_symbol_rules
from futures_bot.execution.futures_paper_broker import FuturesPaperBroker
from futures_bot.strategy.session_filter import (
    filter_klines_by_session,
    kline_open_time_local,
    kline_open_time_utc,
)
from onchain_bot.config_loader import (
    load_onchain_symbols_config,
    save_onchain_symbols_config,
)
from onchain_bot.executable_check import check_onchain_executable
from onchain_bot.paper_pnl import update_paper_positions_with_latest_quotes
from onchain_bot.paper_state import get_closed_trades, get_positions
from onchain_bot.quote_cache import get_cached_quote, update_quote_cache
from onchain_bot.run_onchain_paper_once import run_once as run_onchain_paper_once
from onchain_bot.signal_reader import read_signal_for_mapping
from onchain_bot.status_onchain import build_quote_payload
from observability.event_logger import LogRouter, StructuredLogger
from runtime.bot_state import ERROR, PAUSED, RUNNING, STOPPED
from runtime.safety import (
    RuntimeSafetyConfig,
    load_runtime_safety_config,
    safety_status_payload,
    save_runtime_safety_config,
)
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
    "futures": BASE_DIR / "logs" / "futures.log",
}
FUTURES_STRATEGY_SIGNALS_PATH = BASE_DIR / "data" / "futures_strategy_signals.json"
FUTURES_LOOP_STATE_PATH = BASE_DIR / "data" / "futures_loop_state.json"
FUTURES_PAPER_STATE_PATH = BASE_DIR / "data" / "futures_paper_state.json"
SYMBOL_PATTERN = re.compile(r"^[A-Z0-9]+USDT$")
BOOLEAN_FORM_VALUES = {"true": True, "false": False}
FUTURES_TIMEFRAME_OPTIONS = ("5m", "15m", "1h", "4h", "1d")
FUTURES_KLINE_INTERVAL_OPTIONS = ("5m", "15m", "1h", "4h", "1d")
FUTURES_RISK_SETTING_FIELDS = (
    ("risk.max_leverage", "最大杠杆", "number", "0.0001", ""),
    ("risk.max_margin_per_trade_usdt", "单笔最大保证金 USDT", "number", "0.0001", ""),
    ("risk.max_single_order_usdt", "单笔最大下单金额 USDT", "number", "0.0001", ""),
    ("risk.max_position_ratio", "最大仓位占比", "number", "0.0001", ""),
    ("risk.min_liquidation_distance_pct", "最小爆仓距离 %", "number", "0.0001", ""),
    ("risk.max_funding_rate_abs", "最大资金费率", "number", "0.000001", ""),
    ("risk.max_consecutive_losing_trades", "最大连续亏损次数", "number", "1", ""),
    ("risk.paper_test_max_funding_rate_abs", "测试资金费率", "number", "0.000001", "仅 Paper 测试"),
    ("risk.stop_loss_pct", "止损百分比", "number", "0.0001", ""),
    ("risk.partial1_sell_pct", "第一次分批止盈比例", "number", "0.0001", ""),
    ("risk.partial2_sell_pct", "第二次分批止盈比例", "number", "0.0001", ""),
    ("risk.big_candle_multiplier", "大K线倍数", "number", "0.0001", ""),
    ("risk.big_candle_body_lookback", "大K线实体均值回看数量", "number", "1", ""),
    ("risk.profit_giveback_ratio", "利润回吐比例", "number", "0.0001", ""),
    ("risk.profit_protection_trigger_pct", "利润保护触发百分比", "number", "0.0001", ""),
)
FUTURES_STRATEGY_SETTING_FIELDS = (
    ("strategy.trend_long.ema_fast", "trend_long 快速 EMA", "number", "1", ""),
    ("strategy.trend_long.ema_slow", "trend_long 慢速 EMA", "number", "1", ""),
    ("strategy.trend_long.macd_fast", "trend_long MACD 快线周期", "number", "1", ""),
    ("strategy.trend_long.macd_slow", "trend_long MACD 慢线周期", "number", "1", ""),
    ("strategy.trend_long.macd_signal", "trend_long MACD 信号线周期", "number", "1", ""),
    ("strategy.trend_long.rsi_period", "trend_long RSI 周期", "number", "1", ""),
    ("strategy.trend_long.min_rsi", "trend_long 最小 RSI", "number", "0.0001", ""),
    ("strategy.trend_long.max_rsi", "trend_long 最大 RSI", "number", "0.0001", ""),
    ("strategy.trend_long.rsi_overheat", "RSI过热阈值", "number", "0.0001", ""),
    (
        "strategy.trend_long.max_hold_bars",
        "最大持仓趋势K线数",
        "number",
        "1",
        "该值按 trend_timeframe 计算，不按 signal_timeframe 计算。例如 trend_timeframe=4h 且 max_hold_bars=60，约等于最长持仓10天。",
    ),
    ("strategy.trend_long.min_expected_return", "最低预期收益", "number", "0.0001", ""),
    (
        "strategy.trend_long.time_stop_profit_exempt_pct",
        "时间止损盈利豁免百分比",
        "number",
        "0.0001",
        "如果持仓超过最大持仓趋势K线数，但盈利超过该阈值，则不按时间止损卖出，继续由技术止盈和利润回吐规则管理。",
    ),
    ("strategy.trend_long_test.ema_fast", "trend_long_test 快速 EMA", "number", "1", ""),
    ("strategy.trend_long_test.macd_fast", "trend_long_test MACD 快线周期", "number", "1", ""),
    ("strategy.trend_long_test.macd_slow", "trend_long_test MACD 慢线周期", "number", "1", ""),
    ("strategy.trend_long_test.macd_signal", "trend_long_test MACD 信号线周期", "number", "1", ""),
    ("strategy.trend_long_test.rsi_period", "trend_long_test RSI 周期", "number", "1", ""),
)
FUTURES_OTHER_SETTING_FIELDS = (
    ("app.mode", "运行模式", "text", "1", ""),
    ("app.polling_interval_seconds", "轮询间隔秒数", "number", "1", ""),
    ("futures.base_url", "Futures API 地址", "text", "1", ""),
    ("futures.request_timeout_seconds", "请求超时秒数", "number", "1", ""),
    ("futures.rules_cache_ttl_seconds", "交易规则缓存秒数", "number", "1", ""),
    ("safety.allow_live_trading", "允许实盘交易", "bool", "1", ""),
    ("safety.live_execute_enabled", "启用实盘执行", "bool", "1", ""),
)
SPOT_SETTING_LABELS = {
    "app.mode": "运行模式",
    "exchange.name": "交易所名称",
    "exchange.base_url": "交易所 API 地址",
    "exchange.recv_window": "请求窗口",
    "exchange.request_timeout_seconds": "请求超时秒数",
    "binance.rules_cache_ttl_seconds": "交易规则缓存秒数",
    "market.default_symbol": "默认标的",
    "market.default_symbols": "默认标的列表",
    "market.timeframe.entry": "入场周期",
    "market.timeframe.trend": "趋势周期",
    "market.polling_interval_seconds": "轮询间隔秒数",
    "backtest.initial_capital": "回测初始资金",
    "backtest.report_file": "回测报告文件",
    "backtest.log_file": "回测日志文件",
    "paper.initial_cash": "Paper 初始现金",
    "paper.state_file": "Paper 状态文件",
    "paper.trade_log_file": "Paper 交易日志",
    "execution.enabled_symbols": "启用标的列表",
    "execution.fixed_order_quote_amount": "固定下单金额",
    "execution.cash_usage_pct": "现金使用比例",
    "execution.max_positions": "最大持仓数量",
    "execution.stop_loss_pct": "止损百分比",
    "execution.take_profit_pct": "止盈百分比",
    "execution.max_consecutive_errors": "最大连续错误次数",
    "execution.runtime_state_file": "运行状态文件",
    "execution.robot_initial_status": "机器人初始状态",
    "execution.status_file": "状态文件",
    "safety.allow_live_trading": "允许实盘交易",
    "safety.live_execute_enabled": "启用实盘执行",
    "safety.require_manual_confirm": "需要人工确认",
    "safety.real_order_method_enabled": "真实下单方法开关",
    "safety.max_consecutive_errors": "安全连续错误上限",
    "logging.level": "日志级别",
    "logging.system_log_file": "系统日志文件",
    "logging.trade_log_file": "交易日志文件",
    "logging.error_log_file": "错误日志文件",
    "live.enabled": "实盘模式启用",
    "feature_engine.atr_period": "ATR 周期",
    "feature_engine.macd_fast": "MACD 快线",
    "feature_engine.macd_slow": "MACD 慢线",
    "feature_engine.macd_signal": "MACD 信号线",
    "feature_engine.rsi_period": "RSI 周期",
    "feature_engine.swing_atr_multiplier": "摆动 ATR 倍数",
    "strategy.ema_slope_lookback": "EMA 斜率回看",
    "strategy.macd_decay_bars": "MACD 衰减根数",
    "strategy.rsi_overheat": "RSI 过热阈值",
    "strategy.entry_cooldown_bars": "入场冷却根数",
    "strategy.max_hold_bars": "最大持仓趋势K线数",
    "strategy.min_expected_return": "最小预期收益",
    "strategy.time_stop_profit_exempt_pct": "时间止损盈利豁免百分比",
    "risk.max_single_order_usdt": "单笔最大下单 USDT",
    "risk.max_consecutive_losing_trades": "连续亏损限制",
    "risk.stop_loss_pct": "止损百分比",
    "risk.partial1_sell_pct": "第一档止盈卖出比例",
    "risk.partial2_sell_pct": "第二档止盈卖出比例",
    "risk.big_candle_multiplier": "大 K 线倍数",
    "risk.big_candle_body_lookback": "大 K 线实体回看",
    "risk.profit_giveback_ratio": "利润回撤比例",
    "risk.profit_protection_trigger_pct": "利润保护触发百分比",
}
SYMBOL_STRATEGY_FIELD_SPECS = (
    ("ema_fast", "快速 EMA", "number", "1"),
    ("ema_slow", "慢速 EMA", "number", "1"),
    ("macd_fast", "MACD 快线周期", "number", "1"),
    ("macd_slow", "MACD 慢线周期", "number", "1"),
    ("macd_signal", "MACD 信号线周期", "number", "1"),
    ("rsi_period", "RSI 周期", "number", "1"),
    ("min_rsi", "最小 RSI", "number", "0.0001"),
    ("max_rsi", "最大 RSI", "number", "0.0001"),
    ("rsi_overheat", "RSI 过热阈值", "number", "0.0001"),
    ("max_hold_bars", "最大持仓趋势K线数", "number", "1"),
    ("min_expected_return", "最低预期收益", "number", "0.0001"),
    ("time_stop_profit_exempt_pct", "时间止损盈利豁免百分比", "number", "0.0001"),
    ("ema_slope_lookback", "EMA 斜率回看", "number", "1"),
    ("macd_decay_bars", "MACD 衰减K线数", "number", "1"),
    ("entry_cooldown_bars", "开仓冷却K线数", "number", "1"),
)
SYMBOL_RISK_FIELD_SPECS = (
    ("stop_loss_pct", "止损百分比", "number", "0.0001"),
    ("take_profit_pct", "止盈百分比", "number", "0.0001"),
    ("partial1_sell_pct", "第一档止盈卖出比例", "number", "0.0001"),
    ("partial2_sell_pct", "第二档止盈卖出比例", "number", "0.0001"),
    ("big_candle_multiplier", "大K线倍数", "number", "0.0001"),
    ("big_candle_body_lookback", "大K线实体回看", "number", "1"),
    ("profit_giveback_ratio", "利润回撤比例", "number", "0.0001"),
    ("profit_protection_trigger_pct", "利润保护触发百分比", "number", "0.0001"),
    ("max_single_order_usdt", "单笔最大下单 USDT", "number", "0.0001"),
    ("max_loss_amount", "单币最大亏损", "number", "0.0001"),
    ("max_leverage", "最大杠杆", "number", "0.0001"),
    ("max_margin_per_trade_usdt", "单笔最大保证金 USDT", "number", "0.0001"),
    ("max_position_ratio", "最大仓位占比", "number", "0.0001"),
    ("max_funding_rate_abs", "最大资金费率", "number", "0.000001"),
)
SPOT_SYMBOL_STRATEGY_FIELD_SPECS = SYMBOL_STRATEGY_FIELD_SPECS
SPOT_SYMBOL_RISK_FIELD_SPECS = SYMBOL_RISK_FIELD_SPECS
FUTURES_SYMBOL_STRATEGY_FIELD_SPECS = SYMBOL_STRATEGY_FIELD_SPECS
FUTURES_SYMBOL_RISK_FIELD_SPECS = SYMBOL_RISK_FIELD_SPECS
FIELD_HELP_TEXT = {
    "strategy.max_hold_bars": (
        "该值按 trend_timeframe 计算，不按 signal_timeframe 计算。"
        "例如 trend_timeframe=4h 且 max_hold_bars=60，约等于最长持仓10天。"
    ),
    "strategy.time_stop_profit_exempt_pct": (
        "如果持仓超过最大持仓趋势K线数，但盈利超过该阈值，则不按时间止损卖出，"
        "继续由技术止盈和利润回吐规则管理。"
    ),
}
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
    safety_status = safety_status_payload(account_equity=None)
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
        "safety_status": safety_status,
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


def _read_futures_log_lines(*, symbol: str | None = None, line_count: int = 100) -> tuple[list[str], bool]:
    futures_log = LOG_FILE_MAP["futures"]
    if futures_log.exists():
        return _read_recent_log_lines(futures_log, symbol=symbol, line_count=line_count), True

    system_lines = _read_recent_log_lines(
        LOG_FILE_MAP["system"],
        symbol=symbol,
        line_count=max(line_count * 5, 500),
    )
    futures_lines = [line for line in system_lines if "futures" in line.lower()]
    return futures_lines[-line_count:], LOG_FILE_MAP["system"].exists()


def _configured_symbol_names() -> tuple[str, ...]:
    try:
        settings = load_project_config()
    except Exception:
        return ()
    symbols = settings.get("symbols_config", {}).get("symbols", {})
    if not isinstance(symbols, dict):
        return ()
    return tuple(symbols.keys())


def _configured_futures_symbol_names() -> tuple[str, ...]:
    try:
        return tuple(load_futures_symbols_config().keys())
    except Exception:
        return ()


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
    _write_symbols_config(symbols_config)


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


def _parse_non_negative_number(form: dict[str, str], field_name: str) -> float:
    raw_value = form.get(field_name, "").strip()
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a number greater than or equal to 0") from exc
    if value < 0:
        raise ValueError(f"{field_name} must be greater than or equal to 0")
    return value


def _parse_positive_int(form: dict[str, str], field_name: str) -> int:
    raw_value = form.get(field_name, "").strip()
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an integer greater than 0") from exc
    if value <= 0:
        raise ValueError(f"{field_name} must be greater than 0")
    return value


def _parse_optional_symbol_number(form: dict[str, str], field_name: str, *, integer: bool = False):
    raw_value = form.get(field_name, "").strip()
    if raw_value == "":
        return None
    try:
        value = int(raw_value) if integer else float(raw_value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a number greater than 0") from exc
    if value <= 0:
        raise ValueError(f"{field_name} must be greater than 0")
    return value


def _parse_optional_non_negative_symbol_number(form: dict[str, str], field_name: str):
    raw_value = form.get(field_name, "").strip()
    if raw_value == "":
        return None
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a number greater than or equal to 0") from exc
    if value < 0:
        raise ValueError(f"{field_name} must be greater than or equal to 0")
    return value


def _symbol_override_fields(
    *,
    specs: tuple[tuple[str, str, str, str], ...],
    section: str,
    override: dict[str, object],
    effective: dict[str, object],
) -> list[dict[str, object]]:
    fields = []
    for key, label, input_type, step in specs:
        source = "本标的" if key in override else "全局"
        fields.append(
            {
                "key": key,
                "name": f"{section}.{key}",
                "label": label,
                "input_type": input_type,
                "step": step,
                "value": override.get(key, ""),
                "effective_value": effective.get(key),
                "source": source,
                "help": FIELD_HELP_TEXT.get(f"{section}.{key}", ""),
            }
        )
    return fields


def _parse_symbol_override_section(
    form: dict[str, str],
    *,
    section: str,
    specs: tuple[tuple[str, str, str, str], ...],
    non_negative_keys: set[str] | None = None,
) -> dict[str, object]:
    non_negative_keys = non_negative_keys or set()
    overrides: dict[str, object] = {}
    for key, _, _, step in specs:
        field_name = f"{section}.{key}"
        if key in non_negative_keys:
            parsed = _parse_optional_non_negative_symbol_number(form, field_name)
        else:
            parsed = _parse_optional_symbol_number(form, field_name, integer=step == "1")
        if parsed is not None:
            overrides[key] = parsed
    return overrides


def _get_path_value(payload: dict[str, object], dotted_path: str):
    current: object = payload
    for part in dotted_path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _set_path_value(payload: dict[str, object], dotted_path: str, value) -> None:
    current = payload
    parts = dotted_path.split(".")
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = value


def _flatten_editable_settings(payload: dict[str, object], prefix: str = "") -> list[dict[str, object]]:
    fields: list[dict[str, object]] = []
    for key, value in payload.items():
        if key == "symbols_config":
            continue
        dotted_path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            fields.extend(_flatten_editable_settings(value, dotted_path))
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            value_type = "bool" if isinstance(value, bool) else "number" if isinstance(value, (int, float)) else "text"
            fields.append(
                {
                    "path": dotted_path,
                    "label": SPOT_SETTING_LABELS.get(dotted_path, dotted_path),
                    "value": value,
                    "value_type": value_type,
                    "input_type": "number" if value_type == "number" else "text",
                    "step": "1" if isinstance(value, int) and not isinstance(value, bool) else "0.0001",
                    "help": FIELD_HELP_TEXT.get(dotted_path, ""),
                }
            )
            continue
        if isinstance(value, list) and all(not isinstance(item, (dict, list)) for item in value):
            fields.append(
                {
                    "path": dotted_path,
                    "label": SPOT_SETTING_LABELS.get(dotted_path, dotted_path),
                    "value": ", ".join(str(item) for item in value),
                    "value_type": "list",
                    "input_type": "text",
                    "step": "1",
                    "help": FIELD_HELP_TEXT.get(dotted_path, ""),
                }
            )
    return fields


def _spot_config_group_for_path(path: str) -> str:
    parts = path.split(".")
    lower_path = path.lower()
    if (
        parts[0] in {"risk", "safety"}
        or "loss" in lower_path
        or "max_loss" in lower_path
        or "consecutive_loss" in lower_path
    ):
        return "risk"
    if (
        parts[0] in {"strategy", "feature_engine"}
        or "indicator" in lower_path
        or "ema" in lower_path
        or "macd" in lower_path
        or "rsi" in lower_path
        or "timeframe" in lower_path
    ):
        return "strategy"
    return "other"


def _group_spot_config_fields(fields: list[dict[str, object]]) -> dict[str, list[dict[str, object]]]:
    groups = {"risk": [], "strategy": [], "other": []}
    for field in fields:
        groups[_spot_config_group_for_path(str(field["path"]))].append(field)
    return groups


def _coerce_spot_setting_value(raw_value: str, current_value):
    value = raw_value.strip()
    if isinstance(current_value, bool):
        normalized = value.lower()
        if normalized not in BOOLEAN_FORM_VALUES:
            raise ValueError("布尔值必须是 true 或 false")
        return BOOLEAN_FORM_VALUES[normalized]
    if isinstance(current_value, int) and not isinstance(current_value, bool):
        if value == "":
            raise ValueError("必填字段不能为空")
        return int(value)
    if isinstance(current_value, float):
        if value == "":
            raise ValueError("必填字段不能为空")
        return float(value)
    if isinstance(current_value, list):
        if value == "":
            return []
        return [item.strip() for item in value.split(",") if item.strip()]
    if value == "":
        raise ValueError("必填字段不能为空")
    return value


def _spot_config_view(message: str | None = None, error: str | None = None) -> dict[str, object]:
    try:
        settings = load_project_config()
        settings_to_edit = {key: value for key, value in settings.items() if key != "symbols_config"}
        fields = _flatten_editable_settings(settings_to_edit)
    except Exception as exc:
        return {
            "config_error": str(exc),
            "groups": {"risk": [], "strategy": [], "other": []},
            "message": message,
            "error": error,
        }
    return {
        "config_error": None,
        "groups": _group_spot_config_fields(fields),
        "message": message,
        "error": error,
    }


def _futures_config_fields(
    raw_settings: dict[str, object],
    field_specs: tuple[tuple[str, str, str, str, str], ...],
) -> list[dict[str, object]]:
    fields = []
    for path, label, input_type, step, help_text in field_specs:
        value = _get_path_value(raw_settings, path)
        value_type = "bool" if isinstance(value, bool) else "number" if input_type == "number" else "text"
        fields.append(
            {
                "path": path,
                "label": label,
                "value": value,
                "input_type": input_type,
                "value_type": value_type,
                "step": step,
                "help": help_text,
            }
        )
    return fields


def _futures_config_view(message: str | None = None, error: str | None = None) -> dict[str, object]:
    try:
        raw_settings = load_yaml_mapping(DEFAULT_FUTURES_SETTINGS_PATH)
    except Exception as exc:
        return {
            "config_error": str(exc),
            "groups": {"risk": [], "strategy": [], "other": []},
            "message": message,
            "error": error,
        }
    return {
        "config_error": None,
        "groups": {
            "risk": _futures_config_fields(raw_settings, FUTURES_RISK_SETTING_FIELDS),
            "strategy": _futures_config_fields(raw_settings, FUTURES_STRATEGY_SETTING_FIELDS),
            "other": _futures_config_fields(raw_settings, FUTURES_OTHER_SETTING_FIELDS),
        },
        "message": message,
        "error": error,
    }


def _parse_timeframe(form: dict[str, str], field_name: str) -> str:
    value = form.get(field_name, "").strip()
    if value not in VALID_SYMBOL_TIMEFRAMES:
        allowed = ", ".join(VALID_SYMBOL_TIMEFRAMES)
        raise ValueError(f"{field_name} must be one of: {allowed}")
    return value


def _symbol_config_from_form(form: dict[str, str]) -> dict:
    config = {
        "enabled": _parse_form_bool(form, "enabled"),
        "trend_timeframe": _parse_timeframe(form, "trend_timeframe"),
        "signal_timeframe": _parse_timeframe(form, "signal_timeframe"),
        "order_amount": _parse_positive_amount(form, "order_amount"),
        "max_loss_amount": _parse_positive_amount(form, "max_loss_amount"),
        "paused_by_loss": _parse_form_bool(form, "paused_by_loss"),
    }
    strategy_overrides = _parse_symbol_override_section(
        form,
        section="strategy",
        specs=SPOT_SYMBOL_STRATEGY_FIELD_SPECS,
    )
    risk_overrides = _parse_symbol_override_section(
        form,
        section="risk",
        specs=SPOT_SYMBOL_RISK_FIELD_SPECS,
    )
    if strategy_overrides:
        config["strategy"] = strategy_overrides
    else:
        config.pop("strategy", None)
    if risk_overrides:
        config["risk"] = risk_overrides
    else:
        config.pop("risk", None)
    return config


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
    else:
        settings = load_project_config()

    effective_symbol_config = get_effective_spot_symbol_config(
        normalized_symbol,
        {
            **settings,
            "symbols_config": {
                **settings.get("symbols_config", {}),
                "symbols": {
                    **settings.get("symbols_config", {}).get("symbols", {}),
                    normalized_symbol: symbol_config,
                },
            },
        },
    )
    symbol_override = effective_symbol_config["symbol_override"]
    effective_config = effective_symbol_config["effective_config"]

    return {
        "request": request,
        "project_name": "TraderBot Local Console",
        "symbol": normalized_symbol,
        "symbol_config": symbol_config,
        "spot_symbol_edit_config": {
            "symbol": normalized_symbol,
            "enabled": bool(symbol_config.get("enabled", True)),
            "strategy_name": symbol_config.get("strategy_name", "trend_long"),
            "leverage": symbol_config.get("leverage", ""),
            "margin_amount": symbol_config.get("margin_amount", ""),
            "order_amount": symbol_config.get("order_amount", ""),
            "max_loss_amount": symbol_config.get("max_loss_amount", ""),
            "trend_timeframe": symbol_config.get("trend_timeframe", "4h"),
            "signal_timeframe": symbol_config.get("signal_timeframe", "15m"),
            "market_session_filter": symbol_config.get("market_session_filter", ""),
            "paused_by_loss": bool(symbol_config.get("paused_by_loss", False)),
        },
        "timeframes": VALID_SYMBOL_TIMEFRAMES,
        "spot_symbol_strategy_fields": _symbol_override_fields(
            specs=SPOT_SYMBOL_STRATEGY_FIELD_SPECS,
            section="strategy",
            override=symbol_override["strategy"],
            effective=effective_config["strategy"],
        ),
        "spot_symbol_risk_fields": _symbol_override_fields(
            specs=SPOT_SYMBOL_RISK_FIELD_SPECS,
            section="risk",
            override=symbol_override["risk"],
            effective=effective_config["risk"],
        ),
        "error": error,
    }


def _render_spot_symbol_edit_page(
    request: Request,
    symbol: str,
    symbol_config: dict[str, object] | None = None,
    *,
    error: str | None = None,
    status_code: int = 200,
):
    context = _load_symbols_view()
    edit_context = _load_symbol_edit_context(
        request,
        symbol,
        symbol_config=symbol_config,
        error=error,
    )
    context.update(edit_context)
    context["error"] = error
    return templates.TemplateResponse(
        request,
        "symbols.html",
        context,
        status_code=status_code,
    )


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


def _futures_paper_position_row(position: dict[str, object]) -> dict[str, object]:
    return {
        "symbol": position.get("symbol"),
        "side": position.get("side"),
        "entry_price": _to_optional_float(position.get("entry_price")),
        "mark_price": _to_optional_float(position.get("mark_price")),
        "position_amt": _to_optional_float(position.get("position_amt")),
        "unrealized_pnl": _to_optional_float(position.get("unrealized_pnl")),
        "leverage": _to_optional_float(position.get("leverage")),
        "margin": _to_optional_float(position.get("margin")),
        "current_return": _to_optional_float(position.get("current_return")),
        "max_unrealized_return": _to_optional_float(position.get("max_unrealized_return")),
        "partial1_done": bool(position.get("partial1_done", False)),
        "partial2_done": bool(position.get("partial2_done", False)),
        "holding_bars": position.get("holding_bars"),
    }


def _load_futures_paper_positions() -> list[dict[str, object]]:
    try:
        broker = FuturesPaperBroker()
    except Exception:
        return []
    return [
        _futures_paper_position_row(position.to_dict())
        for position in broker.get_positions()
    ]


def _futures_paper_trade_row(trade: dict[str, object]) -> dict[str, object]:
    return {
        "timestamp": trade.get("timestamp"),
        "symbol": trade.get("symbol"),
        "side": trade.get("side"),
        "entry_price": _to_optional_float(trade.get("entry_price")),
        "exit_price": _to_optional_float(trade.get("exit_price")),
        "position_amt": _to_optional_float(trade.get("position_amt")),
        "margin": _to_optional_float(trade.get("margin")),
        "leverage": _to_optional_float(trade.get("leverage")),
        "realized_pnl": _to_optional_float(trade.get("realized_pnl")),
        "close_type": trade.get("close_type", "full"),
        "sell_pct": _to_optional_float(trade.get("sell_pct")),
    }


def _load_futures_paper_trade_history() -> list[dict[str, object]]:
    try:
        broker = FuturesPaperBroker()
    except Exception:
        return []
    return [
        _futures_paper_trade_row(trade)
        for trade in reversed(broker.get_closed_trades()[-20:])
        if isinstance(trade, dict)
    ]


def _load_futures_paper_state() -> dict[str, object]:
    if not FUTURES_PAPER_STATE_PATH.exists():
        return {"positions": [], "closed_trades": [], "updated_at": None}
    try:
        payload = json.loads(FUTURES_PAPER_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"positions": [], "closed_trades": [], "updated_at": None}
    if not isinstance(payload, dict):
        return {"positions": [], "closed_trades": [], "updated_at": None}
    positions = payload.get("positions", [])
    closed_trades = payload.get("closed_trades", [])
    return {
        "positions": positions if isinstance(positions, list) else [],
        "closed_trades": closed_trades if isinstance(closed_trades, list) else [],
        "updated_at": payload.get("updated_at"),
    }


def _futures_paper_performance_rows() -> list[dict[str, object]]:
    state = _load_futures_paper_state()
    closed_trades = [
        trade
        for trade in state.get("closed_trades", [])
        if isinstance(trade, dict)
    ]
    closed_trades.sort(key=lambda trade: str(trade.get("timestamp") or ""))

    rows: list[dict[str, object]] = []
    cumulative_realized_pnl = 0.0
    for trade in closed_trades:
        timestamp = trade.get("timestamp")
        realized_pnl = _to_optional_float(trade.get("realized_pnl")) or 0.0
        cumulative_realized_pnl += realized_pnl
        rows.append(
            {
                "timestamp": timestamp,
                "cumulative_realized_pnl": cumulative_realized_pnl,
                "unrealized_pnl": 0.0,
                "total_pnl": cumulative_realized_pnl,
            }
        )

    unrealized_pnl = sum(
        _to_optional_float(position.get("unrealized_pnl")) or 0.0
        for position in state.get("positions", [])
        if isinstance(position, dict)
    )
    if unrealized_pnl or state.get("positions"):
        timestamp = state.get("updated_at") or datetime.now(timezone.utc).isoformat()
        rows.append(
            {
                "timestamp": timestamp,
                "cumulative_realized_pnl": cumulative_realized_pnl,
                "unrealized_pnl": unrealized_pnl,
                "total_pnl": cumulative_realized_pnl + unrealized_pnl,
            }
        )

    return rows


def _futures_strategy_signal_row(signal: dict[str, object]) -> dict[str, object]:
    return {
        "symbol": signal.get("symbol"),
        "strategy": signal.get("strategy"),
        "action": signal.get("action"),
        "reason": signal.get("reason"),
        "trend_timeframe": signal.get("trend_timeframe"),
        "signal_timeframe": signal.get("signal_timeframe"),
        "mark_price": _to_optional_float(signal.get("mark_price")),
        "funding_rate": _to_optional_float(signal.get("funding_rate")),
    }


def _load_futures_strategy_signals() -> list[dict[str, object]]:
    if not FUTURES_STRATEGY_SIGNALS_PATH.exists():
        return []
    try:
        payload = json.loads(FUTURES_STRATEGY_SIGNALS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []
    return [
        _futures_strategy_signal_row(signal)
        for signal in payload.values()
        if isinstance(signal, dict)
    ]


def _futures_loop_signal_row(signal: dict[str, object]) -> dict[str, object]:
    return {
        "symbol": signal.get("symbol"),
        "strategy": signal.get("strategy"),
        "action": signal.get("action"),
        "reason": signal.get("reason"),
        "updated_at": signal.get("updated_at"),
        "trend_timeframe": signal.get("trend_timeframe"),
        "signal_timeframe": signal.get("signal_timeframe"),
        "mark_price": _to_optional_float(signal.get("mark_price")),
        "funding_rate": _to_optional_float(signal.get("funding_rate")),
        "paper_action": signal.get("paper_action"),
        "error": signal.get("error"),
    }


def _load_futures_loop_state() -> dict[str, object]:
    if not FUTURES_LOOP_STATE_PATH.exists():
        return {
            "last_loop_at": None,
            "signals": [],
        }
    try:
        payload = json.loads(FUTURES_LOOP_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "last_loop_at": None,
            "signals": [],
        }
    if not isinstance(payload, dict):
        return {
            "last_loop_at": None,
            "signals": [],
        }
    signals = payload.get("signals", {})
    if not isinstance(signals, dict):
        signals = {}
    return {
        "last_loop_at": payload.get("last_loop_at"),
        "signals": [
            _futures_loop_signal_row(signal)
            for signal in signals.values()
            if isinstance(signal, dict)
        ],
    }


def _futures_symbol_config_row(symbol_config) -> dict[str, object]:
    return {
        "symbol": symbol_config.symbol,
        "enabled": symbol_config.enabled,
        "strategy": symbol_config.strategy,
        "leverage": symbol_config.leverage,
        "margin_amount": symbol_config.margin_amount,
        "trend_timeframe": symbol_config.trend_timeframe,
        "signal_timeframe": symbol_config.signal_timeframe,
        "market_session_filter": symbol_config.market_session_filter,
    }


def _futures_symbol_config_mapping(symbol_config) -> dict[str, object]:
    return {
        "enabled": symbol_config.enabled,
        "strategy_name": symbol_config.strategy,
        "leverage": symbol_config.leverage,
        "margin_amount": symbol_config.margin_amount,
        "trend_timeframe": symbol_config.trend_timeframe,
        "signal_timeframe": symbol_config.signal_timeframe,
        "market_session_filter": symbol_config.market_session_filter,
        "strategy": dict(symbol_config.strategy_overrides or {}),
        "risk": dict(symbol_config.risk_overrides or {}),
    }


def _load_futures_symbol_mappings() -> dict[str, dict[str, object]]:
    return {
        symbol: _futures_symbol_config_mapping(symbol_config)
        for symbol, symbol_config in load_futures_symbols_config().items()
    }


def _log_futures_symbol_action(action: str, symbol: str, **payload: object) -> None:
    StructuredLogger(str(LOG_FILE_MAP["system"])).log(
        action=action,
        symbol=symbol,
        mode="futures",
        **payload,
    )


def _futures_kline_row(kline) -> dict[str, object] | None:
    if not isinstance(kline, (list, tuple)) or len(kline) < 6:
        return None
    try:
        return {
            "open_time": kline_open_time_utc(kline[0]),
            "open_time_local": kline_open_time_local(kline[0]),
            "open": float(kline[1]),
            "high": float(kline[2]),
            "low": float(kline[3]),
            "close": float(kline[4]),
            "volume": float(kline[5]),
        }
    except (TypeError, ValueError, OSError):
        return None


def _log_settings_action(action: str, *, mode: str, **payload: object) -> None:
    StructuredLogger(str(LOG_FILE_MAP["system"])).log(
        action=action,
        symbol="-",
        mode=mode,
        **payload,
    )


def _futures_symbols_redirect(
    *,
    message: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    params = {}
    if message:
        params["futures_symbol_message"] = message
    if error:
        params["futures_symbol_error"] = error
    query_string = f"?{urlencode(params)}" if params else ""
    return RedirectResponse(url=f"/futures{query_string}", status_code=303)


def _futures_symbol_form_defaults() -> dict[str, object]:
    return {
        "strategy": "trend_long",
        "leverage": 1,
        "margin_amount": 10,
        "trend_timeframe": "4h",
        "signal_timeframe": "15m",
        "market_session_filter": "none",
        "enabled": True,
    }


def _parse_futures_symbol_number(value: str, field_name: str) -> tuple[float | None, str | None]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None, f"{field_name}_must_be_number"
    if parsed <= 0:
        return None, f"{field_name}_must_be_greater_than_zero"
    return parsed, None


def _parse_futures_symbol_config_from_form(form: dict[str, str], risk_config) -> dict[str, object]:
    enabled = _parse_form_bool(form, "enabled")
    strategy = form.get("strategy_name", form.get("strategy", "")).strip()
    if strategy not in ALLOWED_FUTURES_STRATEGIES:
        raise ValueError("strategy must be trend_long")

    trend_timeframe = form.get("trend_timeframe", "").strip()
    if trend_timeframe not in ALLOWED_FUTURES_TIMEFRAMES:
        raise ValueError("trend_timeframe must be one of: 5m, 15m, 1h, 4h, 1d")

    signal_timeframe = form.get("signal_timeframe", "").strip()
    if signal_timeframe not in ALLOWED_FUTURES_TIMEFRAMES:
        raise ValueError("signal_timeframe must be one of: 5m, 15m, 1h, 4h, 1d")

    market_session_filter = form.get("market_session_filter", "none").strip()
    if market_session_filter not in ALLOWED_MARKET_SESSION_FILTERS:
        raise ValueError("market_session_filter must be one of: none, us_regular")

    leverage = _parse_positive_amount(form, "leverage")
    if leverage > risk_config.max_leverage:
        raise ValueError(f"leverage must be less than or equal to {risk_config.max_leverage}")

    margin_amount = _parse_positive_amount(form, "margin_amount")
    if margin_amount > risk_config.max_margin_per_trade_usdt:
        raise ValueError(
            "margin_amount must be less than or equal to "
            f"{risk_config.max_margin_per_trade_usdt}"
        )

    config = {
        "enabled": enabled,
        "strategy_name": strategy,
        "leverage": leverage,
        "margin_amount": margin_amount,
        "trend_timeframe": trend_timeframe,
        "signal_timeframe": signal_timeframe,
        "market_session_filter": market_session_filter,
    }
    strategy_overrides = _parse_symbol_override_section(
        form,
        section="strategy",
        specs=FUTURES_SYMBOL_STRATEGY_FIELD_SPECS,
        non_negative_keys={"min_expected_return", "time_stop_profit_exempt_pct"},
    )
    risk_overrides = _parse_symbol_override_section(
        form,
        section="risk",
        specs=FUTURES_SYMBOL_RISK_FIELD_SPECS,
        non_negative_keys={"max_funding_rate_abs"},
    )
    if strategy_overrides:
        config["strategy"] = strategy_overrides
    if risk_overrides:
        config["risk"] = risk_overrides
    return config


def _parse_futures_risk_settings_from_form(form: dict[str, str]) -> dict[str, object]:
    max_leverage = _parse_positive_amount(form, "risk.max_leverage")
    max_margin = _parse_positive_amount(form, "risk.max_margin_per_trade_usdt")
    max_position_ratio = _parse_positive_amount(form, "risk.max_position_ratio")
    if max_position_ratio > 1:
        raise ValueError("最大仓位占比必须小于或等于 1")
    min_liquidation_distance = _parse_positive_amount(form, "risk.min_liquidation_distance_pct")
    max_funding = _parse_non_negative_number(form, "risk.max_funding_rate_abs")
    paper_test_max_funding = _parse_non_negative_number(
        form,
        "risk.paper_test_max_funding_rate_abs",
    )
    max_losing_trades = _parse_positive_int(form, "risk.max_consecutive_losing_trades")
    return {
        "max_leverage": max_leverage,
        "max_margin_per_trade_usdt": max_margin,
        "max_position_ratio": max_position_ratio,
        "min_liquidation_distance_pct": min_liquidation_distance,
        "max_funding_rate_abs": max_funding,
        "max_consecutive_losing_trades": max_losing_trades,
        "paper_test_max_funding_rate_abs": paper_test_max_funding,
    }


def _coerce_futures_setting_value(path: str, raw_value: str):
    value = raw_value.strip()
    if path == "app.mode":
        if value not in {"paper", "live"}:
            raise ValueError("运行模式必须是 paper 或 live")
        return value
    if path in {"safety.allow_live_trading", "safety.live_execute_enabled"}:
        normalized = value.lower()
        if normalized not in BOOLEAN_FORM_VALUES:
            raise ValueError(f"{path} must be true or false")
        return BOOLEAN_FORM_VALUES[normalized]
    if value == "":
        raise ValueError(f"{path} 不能为空")
    if path in {
        "app.polling_interval_seconds",
        "futures.request_timeout_seconds",
        "futures.rules_cache_ttl_seconds",
        "strategy.trend_long.ema_fast",
        "strategy.trend_long.ema_slow",
        "strategy.trend_long.macd_fast",
        "strategy.trend_long.macd_slow",
        "strategy.trend_long.macd_signal",
        "strategy.trend_long.rsi_period",
        "strategy.trend_long.max_hold_bars",
        "strategy.trend_long_test.ema_fast",
        "strategy.trend_long_test.macd_fast",
        "strategy.trend_long_test.macd_slow",
        "strategy.trend_long_test.macd_signal",
        "strategy.trend_long_test.rsi_period",
    }:
        number = int(value)
        if number <= 0:
            raise ValueError(f"{path} must be greater than 0")
        return number
    if path.startswith("strategy."):
        number = float(value)
        if path in {
            "strategy.trend_long.min_expected_return",
            "strategy.trend_long.time_stop_profit_exempt_pct",
        }:
            if number < 0:
                raise ValueError(f"{path} must be greater than or equal to 0")
            return number
        if number <= 0:
            raise ValueError(f"{path} must be greater than 0")
        return number
    if path.startswith("risk."):
        if path in {
            "risk.max_funding_rate_abs",
            "risk.paper_test_max_funding_rate_abs",
        }:
            number = float(value)
            if number < 0:
                raise ValueError(f"{path} must be greater than or equal to 0")
            return number
        if path == "risk.max_consecutive_losing_trades":
            number = int(value)
            if number <= 0:
                raise ValueError(f"{path} must be greater than 0")
            return number
        if path == "risk.big_candle_body_lookback":
            number = int(value)
            if number <= 0:
                raise ValueError(f"{path} must be greater than 0")
            return number
        number = float(value)
        if number <= 0:
            raise ValueError(f"{path} must be greater than 0")
        if path == "risk.max_position_ratio" and number > 1:
            raise ValueError("最大仓位占比必须小于或等于 1")
        if path in {"risk.partial1_sell_pct", "risk.partial2_sell_pct"} and number > 100:
            raise ValueError("分批止盈比例必须小于或等于 100")
        if path == "risk.profit_giveback_ratio" and number > 1:
            raise ValueError("利润回吐比例必须小于或等于 1")
        return number
    return value


def _all_futures_setting_paths() -> tuple[str, ...]:
    return tuple(
        field[0]
        for field in (
            FUTURES_RISK_SETTING_FIELDS
            + FUTURES_STRATEGY_SETTING_FIELDS
            + FUTURES_OTHER_SETTING_FIELDS
        )
    )


def _render_futures_symbol_edit_page(
    request: Request,
    symbol: str,
    symbol_config: dict[str, object],
    *,
    error: str | None = None,
    status_code: int = 200,
):
    context = _load_futures_view()
    try:
        futures_config = load_futures_config()
        effective_symbol_config = get_effective_futures_symbol_config(symbol, futures_config)
        symbol_override = {
            "strategy": dict(symbol_config.get("strategy", effective_symbol_config["symbol_override"]["strategy"])),
            "risk": dict(symbol_config.get("risk", effective_symbol_config["symbol_override"]["risk"])),
        }
        effective_config = effective_symbol_config["effective_config"]
    except Exception:
        symbol_override = {
            "strategy": dict(symbol_config.get("strategy", {})),
            "risk": dict(symbol_config.get("risk", {})),
        }
        effective_config = {"strategy": {}, "risk": {}}
    strategy_name = symbol_config.get("strategy_name", symbol_config.get("strategy", "trend_long"))
    if isinstance(strategy_name, dict):
        strategy_name = "trend_long"
    context.update(
        {
            "request": request,
            "project_name": "TraderBot Local Console",
            "futures_symbol_message": None,
            "futures_symbol_error": None,
            "futures_symbol_edit_config": {
                "symbol": symbol,
                **symbol_config,
                "strategy_name": strategy_name,
            },
            "futures_symbol_edit_error": error,
            "futures_active_tab": "symbols",
            "futures_allowed_session_filters": sorted(ALLOWED_MARKET_SESSION_FILTERS),
            "futures_symbol_strategy_fields": _symbol_override_fields(
                specs=FUTURES_SYMBOL_STRATEGY_FIELD_SPECS,
                section="strategy",
                override=symbol_override["strategy"],
                effective=effective_config["strategy"],
            ),
            "futures_symbol_risk_fields": _symbol_override_fields(
                specs=FUTURES_SYMBOL_RISK_FIELD_SPECS,
                section="risk",
                override=symbol_override["risk"],
                effective=effective_config["risk"],
            ),
        }
    )
    return templates.TemplateResponse(
        request,
        "futures.html",
        context,
        status_code=status_code,
    )


def _load_futures_view() -> dict:
    futures_credentials = load_futures_binance_readonly_credentials().public_status()
    try:
        spot_settings = load_project_config()
        spot_execution_config = load_execution_runtime(spot_settings)
        is_live_mode = spot_execution_config.mode == "live"
    except Exception:
        is_live_mode = False
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
    futures_paper_positions = _load_futures_paper_positions()
    futures_paper_trade_history = _load_futures_paper_trade_history()
    futures_paper_performance = _futures_paper_performance_rows()
    futures_strategy_signals = _load_futures_strategy_signals()
    futures_loop_state = _load_futures_loop_state()
    futures_symbol_configs: list[dict[str, object]] = []
    futures_config_view = _futures_config_view()
    futures_risk_controls = {
        "max_leverage": None,
        "max_margin_per_trade_usdt": None,
        "max_single_order_usdt": None,
        "max_position_ratio": None,
        "min_liquidation_distance_pct": None,
        "max_funding_rate_abs": None,
        "paper_test_max_funding_rate_abs": None,
        "max_consecutive_losing_trades": None,
        "stop_loss_pct": None,
        "partial1_sell_pct": None,
        "partial2_sell_pct": None,
        "big_candle_multiplier": None,
        "big_candle_body_lookback": None,
        "profit_giveback_ratio": None,
        "profit_protection_trigger_pct": None,
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
            "futures_paper_positions": futures_paper_positions,
            "futures_paper_trade_history": futures_paper_trade_history,
            "futures_paper_performance": futures_paper_performance,
            "futures_strategy_signals": futures_strategy_signals,
            "futures_loop_state": futures_loop_state,
            "futures_symbol_configs": futures_symbol_configs,
            "futures_config_view": futures_config_view,
            "futures_risk_controls": futures_risk_controls,
            "rows": [],
            "warnings": [f"Futures config error: {exc}"],
            "config_error": str(exc),
            "futures_symbol_form_defaults": _futures_symbol_form_defaults(),
            "futures_allowed_strategies": sorted(ALLOWED_FUTURES_STRATEGIES),
            "futures_allowed_timeframes": FUTURES_TIMEFRAME_OPTIONS,
            "futures_allowed_session_filters": sorted(ALLOWED_MARKET_SESSION_FILTERS),
            "futures_kline_intervals": FUTURES_KLINE_INTERVAL_OPTIONS,
            "futures_symbol_edit_config": None,
            "futures_symbol_edit_error": None,
            "is_live_mode": is_live_mode,
        }

    enabled_symbols = list(futures_config.enabled_symbols)
    futures_symbol_configs = [
        _futures_symbol_config_row(symbol_config)
        for symbol_config in futures_config.symbols.values()
    ]
    futures_risk_controls.update(
        {
            "max_leverage": futures_config.risk.max_leverage,
            "max_margin_per_trade_usdt": futures_config.risk.max_margin_per_trade_usdt,
            "max_single_order_usdt": futures_config.risk.max_single_order_usdt,
            "max_position_ratio": futures_config.risk.max_position_ratio,
            "min_liquidation_distance_pct": futures_config.risk.min_liquidation_distance_pct,
            "max_funding_rate_abs": futures_config.risk.max_funding_rate_abs,
            "paper_test_max_funding_rate_abs": futures_config.risk.paper_test_max_funding_rate_abs,
            "max_consecutive_losing_trades": futures_config.risk.max_consecutive_losing_trades,
            "stop_loss_pct": futures_config.risk.stop_loss_pct,
            "partial1_sell_pct": futures_config.risk.partial1_sell_pct,
            "partial2_sell_pct": futures_config.risk.partial2_sell_pct,
            "big_candle_multiplier": futures_config.risk.big_candle_multiplier,
            "big_candle_body_lookback": futures_config.risk.big_candle_body_lookback,
            "profit_giveback_ratio": futures_config.risk.profit_giveback_ratio,
            "profit_protection_trigger_pct": futures_config.risk.profit_protection_trigger_pct,
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
        "futures_paper_positions": futures_paper_positions,
        "futures_paper_trade_history": futures_paper_trade_history,
        "futures_paper_performance": futures_paper_performance,
        "futures_strategy_signals": futures_strategy_signals,
        "futures_loop_state": futures_loop_state,
        "futures_symbol_configs": futures_symbol_configs,
        "futures_config_view": futures_config_view,
        "futures_risk_controls": futures_risk_controls,
        "rows": [],
        "warnings": warnings,
        "config_error": None,
        "futures_symbol_form_defaults": _futures_symbol_form_defaults(),
        "futures_allowed_strategies": sorted(ALLOWED_FUTURES_STRATEGIES),
        "futures_allowed_timeframes": FUTURES_TIMEFRAME_OPTIONS,
        "futures_allowed_session_filters": sorted(ALLOWED_MARKET_SESSION_FILTERS),
        "futures_kline_intervals": FUTURES_KLINE_INTERVAL_OPTIONS,
        "futures_symbol_edit_config": None,
        "futures_symbol_edit_error": None,
        "is_live_mode": is_live_mode,
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
    symbols = sorted(set(paper_positions) | runtime_position_symbols | set(sqlite_positions))

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


def _load_spot_view(symbol: str = "") -> dict:
    try:
        settings = load_project_config()
        execution_config = load_execution_runtime(settings)
        is_live_mode = execution_config.mode == "live"
    except Exception:
        is_live_mode = False
    return {
        "spot_positions": _load_positions_view(),
        "spot_performance": _load_performance_view(symbol=symbol),
        "spot_account": _load_account_view(),
        "spot_symbols": _load_symbols_view(),
        "spot_config": _spot_config_view(),
        "is_live_mode": is_live_mode,
    }


def _parse_safety_bool(form: dict[str, str], key: str) -> bool:
    return form.get(key, "false").strip().lower() == "true"


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


def _onchain_form_defaults() -> dict[str, object]:
    return {
        "symbol": "",
        "enabled": False,
        "signal_source": "futures",
        "source_symbol": "",
        "execution_session_filter": "us_regular",
        "chain_name": "ethereum",
        "chain_id": "1",
        "token_name": "",
        "token_symbol": "",
        "token_address": "",
        "token_decimals": 18,
        "quote_token_symbol": "USDC",
        "quote_token_address": "",
        "quote_token_decimals": 6,
        "max_trade_usdt": 20,
        "max_slippage_pct": 1.0,
        "max_gas_usdt": 5,
    }


def _onchain_symbol_rows() -> tuple[list[dict[str, object]], str | None]:
    try:
        symbols = load_onchain_symbols_config()
    except Exception as exc:
        return [], str(exc)
    rows = []
    for symbol_config in symbols.values():
        row = symbol_config.to_dict()
        futures_signal = read_signal_for_mapping(symbol_config)
        cached_buy_quote = get_cached_quote(symbol_config.symbol, "buy")
        cached_sell_quote = get_cached_quote(symbol_config.symbol, "sell")
        action = str(futures_signal.get("action") or "")
        cached_quote = cached_sell_quote if action.startswith("CLOSE") else cached_buy_quote
        executable_check = check_onchain_executable(
            mapping=symbol_config,
            futures_signal=futures_signal,
            quote_result=cached_quote,
            buy_quote_result=cached_buy_quote,
            sell_quote_result=cached_sell_quote,
        )
        row.update(
            {
                "futures_signal": futures_signal,
                "futures_signal_action": futures_signal.get("action", "error"),
                "futures_signal_reason": futures_signal.get("reason"),
                "futures_signal_time": futures_signal.get("updated_at"),
                "cached_quote": cached_quote,
                "cached_buy_quote": cached_buy_quote,
                "cached_sell_quote": cached_sell_quote,
                "quote_status": "not_tested" if cached_quote is None else "ok" if cached_quote.get("ok") else "error",
                "buy_quote_status": "not_tested" if cached_buy_quote is None else "ok" if cached_buy_quote.get("ok") else "error",
                "sell_quote_status": "not_tested" if cached_sell_quote is None else "ok" if cached_sell_quote.get("ok") else "error",
                "cached_quote_time": cached_quote.get("quoted_at") if cached_quote else None,
                "cached_quote_error": cached_quote.get("error") if cached_quote else None,
                "cached_quote_amount_usdt": cached_quote.get("amount_usdt") if cached_quote else None,
                "executable": executable_check["executable"],
                "executable_reasons": executable_check["reasons"],
                "session_allowed": executable_check["session_allowed"],
                "session_name": executable_check["session_name"],
                "session_time_now": executable_check["session_time_now"],
            }
        )
        rows.append(row)
    return rows, None


def _minutes_since(timestamp: object) -> float | None:
    if not isinstance(timestamp, str) or not timestamp:
        return None
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    minutes = (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds() / 60
    return round(max(minutes, 0.0), 2)


def _onchain_paper_rows() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    try:
        mappings = load_onchain_symbols_config()
    except Exception:
        mappings = {}

    positions = []
    for position in get_positions():
        row = dict(position)
        mapping = mappings.get(str(row.get("symbol", "")).upper())
        buy_quote = get_cached_quote(str(row.get("symbol", "")), "buy")
        sell_quote = get_cached_quote(str(row.get("symbol", "")), "sell")
        row["chain_name"] = row.get("chain_name") or (mapping.chain_name if mapping else None)
        row["latest_quote_price"] = row.get("latest_quote_price") or row.get("last_quote_price")
        row["buy_quote_status"] = "not_tested" if buy_quote is None else "ok" if buy_quote.get("ok") else "error"
        row["sell_quote_status"] = "not_tested" if sell_quote is None else "ok" if sell_quote.get("ok") else "error"
        row["estimated_exit_quote_amount"] = None
        row["estimated_exit_pnl"] = None
        if sell_quote and sell_quote.get("ok"):
            parsed_quote = sell_quote.get("parsed_quote")
            if isinstance(parsed_quote, dict):
                row["estimated_exit_quote_amount"] = parsed_quote.get("to_amount_display")
                row["estimated_exit_pnl"] = row.get("unrealized_pnl")
        row["holding_minutes"] = _minutes_since(row.get("entry_time"))
        positions.append(row)

    closed_trades = []
    for trade in reversed(get_closed_trades()):
        row = dict(trade)
        row["holding_minutes"] = _minutes_since(row.get("entry_time"))
        exit_time = row.get("exit_time")
        if isinstance(exit_time, str):
            entry_time = row.get("entry_time")
            try:
                entry_dt = datetime.fromisoformat(str(entry_time).replace("Z", "+00:00"))
                exit_dt = datetime.fromisoformat(exit_time.replace("Z", "+00:00"))
                if entry_dt.tzinfo is None:
                    entry_dt = entry_dt.replace(tzinfo=timezone.utc)
                if exit_dt.tzinfo is None:
                    exit_dt = exit_dt.replace(tzinfo=timezone.utc)
                row["holding_minutes"] = round(
                    max((exit_dt.astimezone(timezone.utc) - entry_dt.astimezone(timezone.utc)).total_seconds(), 0.0) / 60,
                    2,
                )
            except (TypeError, ValueError):
                pass
        closed_trades.append(row)
    return positions, closed_trades


def _onchain_view(
    *,
    message: str | None = None,
    error: str | None = None,
    edit_config: dict[str, object] | None = None,
    edit_error: str | None = None,
    quote_symbol: str | None = None,
    quote_amount_usdt: str = "10",
    quote_direction: str = "buy",
    quote_result: dict[str, object] | None = None,
    quote_error: str | None = None,
    paper_run_result: dict[str, object] | None = None,
) -> dict[str, object]:
    symbols, config_error = _onchain_symbol_rows()
    paper_positions, paper_closed_trades = _onchain_paper_rows()
    if quote_symbol and quote_result:
        quote_status = "ok" if quote_result.get("ok") else "error"
        for symbol in symbols:
            if symbol.get("symbol") == quote_symbol:
                symbol["quote_status"] = quote_status
                symbol["cached_quote_time"] = quote_result.get("quoted_at") or symbol.get("cached_quote_time")
                symbol["cached_quote_error"] = quote_result.get("error") or quote_result.get("message")
                symbol["cached_quote_amount_usdt"] = quote_result.get("amount_usdt")
                if quote_result.get("direction") == "sell":
                    symbol["sell_quote_status"] = quote_status
                else:
                    symbol["buy_quote_status"] = quote_status
                executable_check = check_onchain_executable(
                    mapping=SimpleNamespace(**symbol),
                    futures_signal=symbol.get("futures_signal"),
                    quote_result=quote_result,
                    buy_quote_result=quote_result if quote_result.get("direction") == "buy" else symbol.get("cached_buy_quote"),
                    sell_quote_result=quote_result if quote_result.get("direction") == "sell" else symbol.get("cached_sell_quote"),
                )
                symbol["executable"] = executable_check["executable"]
                symbol["executable_reasons"] = executable_check["reasons"]
                break
    session_warning = any(
        row.get("execution_session_filter") == "us_regular" and not row.get("session_allowed", True)
        for row in symbols
    )
    return {
        "symbols": symbols,
        "symbols_count": len(symbols),
        "onchain_session_warning": session_warning,
        "paper_positions": paper_positions,
        "paper_closed_trades": paper_closed_trades,
        "config_error": config_error,
        "message": message,
        "error": error,
        "edit_config": edit_config,
        "edit_error": edit_error,
        "form_defaults": _onchain_form_defaults(),
        "quote_symbol": quote_symbol,
        "quote_amount_usdt": quote_amount_usdt,
        "quote_direction": quote_direction,
        "quote_result": quote_result,
        "quote_result_json": json.dumps(quote_result, indent=2, ensure_ascii=False) if quote_result else "",
        "quote_error": quote_error,
        "paper_run_result": paper_run_result,
    }


def _onchain_redirect(
    *,
    message: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    params = {}
    if message:
        params["message"] = message
    if error:
        params["error"] = error
    suffix = f"?{urlencode(params)}" if params else ""
    return RedirectResponse(url=f"/onchain{suffix}", status_code=303)


def _parse_onchain_bool(form: dict[str, str], key: str) -> bool:
    return form.get(key, "false").strip().lower() == "true"


def _parse_onchain_positive_int(form: dict[str, str], key: str) -> int:
    value = form.get(key, "").strip()
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer greater than 0") from exc
    if parsed <= 0:
        raise ValueError(f"{key} must be greater than 0")
    return parsed


def _parse_onchain_number(
    form: dict[str, str],
    key: str,
    *,
    allow_zero: bool = False,
) -> float:
    value = form.get(key, "").strip()
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(f"{key} must be a number") from exc
    if allow_zero:
        if parsed < 0:
            raise ValueError(f"{key} must be greater than or equal to 0")
    elif parsed <= 0:
        raise ValueError(f"{key} must be greater than 0")
    return parsed


def _parse_onchain_address(form: dict[str, str], key: str) -> str:
    value = form.get(key, "").strip()
    if not value.startswith("0x"):
        raise ValueError(f"{key} must start with 0x")
    return value


def _onchain_config_from_form(form: dict[str, str]) -> dict[str, object]:
    symbol = form.get("symbol", "").strip().upper()
    if not SYMBOL_PATTERN.fullmatch(symbol):
        raise ValueError("信号标的必须大写并以 USDT 结尾")
    token_symbol = form.get("token_symbol", "").strip().upper()
    quote_token_symbol = form.get("quote_token_symbol", "").strip().upper()
    return {
        "enabled": _parse_onchain_bool(form, "enabled"),
        "signal_source": "futures",
        "source_symbol": symbol,
        "execution_session_filter": form.get("execution_session_filter", "us_regular").strip() or "us_regular",
        "chain_name": form.get("chain_name", "").strip(),
        "chain_id": form.get("chain_id", "").strip(),
        "token_name": form.get("token_name", "").strip(),
        "token_symbol": token_symbol,
        "token_address": _parse_onchain_address(form, "token_address"),
        "token_decimals": _parse_onchain_positive_int(form, "token_decimals"),
        "quote_token_symbol": quote_token_symbol,
        "quote_token_address": _parse_onchain_address(form, "quote_token_address"),
        "quote_token_decimals": _parse_onchain_positive_int(form, "quote_token_decimals"),
        "max_trade_usdt": _parse_onchain_number(form, "max_trade_usdt"),
        "max_slippage_pct": _parse_onchain_number(form, "max_slippage_pct", allow_zero=True),
        "max_gas_usdt": _parse_onchain_number(form, "max_gas_usdt", allow_zero=True),
    }


def _load_onchain_symbol_mappings() -> dict[str, dict[str, object]]:
    return {
        symbol: symbol_config.to_dict()
        for symbol, symbol_config in load_onchain_symbols_config().items()
    }


def _submitted_onchain_config(form: dict[str, str]) -> dict[str, object]:
    defaults = _onchain_form_defaults()
    submitted = dict(defaults)
    for key in defaults:
        if key in form:
            submitted[key] = form.get(key, "")
    submitted["enabled"] = _parse_onchain_bool(form, "enabled")
    return submitted


def _ensure_onchain_symbol(symbol: str) -> str:
    normalized_symbol = symbol.strip().upper()
    symbols = _load_onchain_symbol_mappings()
    if normalized_symbol not in symbols:
        raise ValueError(f"标的不存在：{normalized_symbol}")
    return normalized_symbol


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


@app.get("/onchain", response_class=HTMLResponse)
def onchain_page(request: Request):
    context = _onchain_view(
        message=request.query_params.get("message"),
        error=request.query_params.get("error"),
    )
    context.update(
        {
            "request": request,
            "project_name": "TraderBot Local Console",
        }
    )
    return templates.TemplateResponse(request, "onchain.html", context)


@app.post("/onchain/symbols/add")
async def onchain_symbol_add(request: Request):
    form = await _read_form_data(request)
    try:
        symbol = form.get("symbol", "").strip().upper()
        updated_symbols = _load_onchain_symbol_mappings()
        if symbol in updated_symbols:
            return _onchain_redirect(error=f"{symbol} already exists")
        updated_symbols[symbol] = _onchain_config_from_form(form)
        save_onchain_symbols_config(updated_symbols)
    except Exception as exc:
        return _onchain_redirect(error=str(exc))
    return _onchain_redirect(message=f"{symbol} added")


@app.get("/onchain/symbols/{symbol}/edit", response_class=HTMLResponse)
def onchain_symbol_edit_page(request: Request, symbol: str):
    normalized_symbol = symbol.strip().upper()
    try:
        symbols = _load_onchain_symbol_mappings()
        if normalized_symbol not in symbols:
            raise ValueError(f"标的不存在：{normalized_symbol}")
        edit_config = dict(symbols[normalized_symbol])
    except Exception as exc:
        context = _onchain_view(error=str(exc))
        context.update(
            {
                "request": request,
                "project_name": "TraderBot Local Console",
            }
        )
        return templates.TemplateResponse(request, "onchain.html", context, status_code=404)

    context = _onchain_view(edit_config=edit_config)
    context.update(
        {
            "request": request,
            "project_name": "TraderBot Local Console",
        }
    )
    return templates.TemplateResponse(request, "onchain.html", context)


@app.post("/onchain/symbols/{symbol}/edit", response_class=HTMLResponse)
async def onchain_symbol_edit_save(request: Request, symbol: str):
    normalized_symbol = symbol.strip().upper()
    form = await _read_form_data(request)
    submitted_config = _submitted_onchain_config(form)
    try:
        updated_symbols = _load_onchain_symbol_mappings()
        if normalized_symbol not in updated_symbols:
            raise ValueError(f"标的不存在：{normalized_symbol}")
        new_symbol = form.get("symbol", "").strip().upper()
        if new_symbol != normalized_symbol and new_symbol in updated_symbols:
            raise ValueError(f"{new_symbol} already exists")
        parsed_config = _onchain_config_from_form(form)
        updated_symbols.pop(normalized_symbol)
        updated_symbols[new_symbol] = parsed_config
        save_onchain_symbols_config(updated_symbols)
    except Exception as exc:
        context = _onchain_view(edit_config=submitted_config, edit_error=str(exc))
        context.update(
            {
                "request": request,
                "project_name": "TraderBot Local Console",
            }
        )
        return templates.TemplateResponse(request, "onchain.html", context, status_code=400)
    return _onchain_redirect(message=f"{new_symbol} saved")


@app.post("/onchain/symbols/{symbol}/toggle")
def onchain_symbol_toggle(symbol: str):
    normalized_symbol = symbol.strip().upper()
    try:
        updated_symbols = _load_onchain_symbol_mappings()
        if normalized_symbol not in updated_symbols:
            return _onchain_redirect(error=f"{normalized_symbol} not found")
        updated_symbols[normalized_symbol]["enabled"] = not bool(updated_symbols[normalized_symbol]["enabled"])
        save_onchain_symbols_config(updated_symbols)
        state = "enabled" if updated_symbols[normalized_symbol]["enabled"] else "disabled"
    except Exception as exc:
        return _onchain_redirect(error=str(exc))
    return _onchain_redirect(message=f"{normalized_symbol} {state}")


@app.post("/onchain/symbols/{symbol}/delete")
def onchain_symbol_delete(symbol: str):
    normalized_symbol = symbol.strip().upper()
    try:
        updated_symbols = _load_onchain_symbol_mappings()
        if normalized_symbol not in updated_symbols:
            return _onchain_redirect(error=f"{normalized_symbol} not found")
        updated_symbols.pop(normalized_symbol)
        save_onchain_symbols_config(updated_symbols)
    except Exception as exc:
        return _onchain_redirect(error=str(exc))
    return _onchain_redirect(message=f"{normalized_symbol} deleted")


@app.post("/onchain/paper/refresh")
def onchain_paper_refresh():
    result = update_paper_positions_with_latest_quotes()
    if not result.get("ok"):
        return _onchain_redirect(error=f"Paper 状态刷新失败：{result.get('message') or result.get('error')}")
    return _onchain_redirect(message=f"Paper 状态已刷新，更新 {len(result.get('updated_symbols', []))} 个持仓")


@app.post("/onchain/paper/run-once", response_class=HTMLResponse)
def onchain_paper_run_once(request: Request):
    status_code = 200
    try:
        paper_run_result = run_onchain_paper_once()
        message = "Onchain Paper 已运行一次"
        error = None
    except Exception as exc:
        paper_run_result = {
            "actions": [],
            "positions_count": 0,
            "closed_trades_count": 0,
            "errors": [{"error": str(exc)}],
        }
        message = None
        error = f"Onchain Paper 运行失败：{exc}"
        status_code = 400
    context = _onchain_view(
        message=message,
        error=error,
        paper_run_result=paper_run_result,
    )
    context.update(
        {
            "request": request,
            "project_name": "TraderBot Local Console",
        }
    )
    return templates.TemplateResponse(request, "onchain.html", context, status_code=status_code)


@app.get("/onchain/quote/{symbol}", response_class=HTMLResponse)
def onchain_quote_page(request: Request, symbol: str):
    try:
        normalized_symbol = _ensure_onchain_symbol(symbol)
        context = _onchain_view(quote_symbol=normalized_symbol, quote_amount_usdt="10")
        status_code = 200
    except Exception as exc:
        normalized_symbol = symbol.strip().upper()
        context = _onchain_view(quote_symbol=normalized_symbol, quote_error=str(exc))
        status_code = 404
    context.update(
        {
            "request": request,
            "project_name": "TraderBot Local Console",
        }
    )
    return templates.TemplateResponse(request, "onchain.html", context, status_code=status_code)


@app.post("/onchain/quote/{symbol}", response_class=HTMLResponse)
async def onchain_quote_submit(request: Request, symbol: str):
    normalized_symbol = symbol.strip().upper()
    form = await _read_form_data(request)
    amount_usdt = form.get("amount_usdt", "10").strip() or "10"
    quote_direction = form.get("quote_direction", "buy").strip().lower() or "buy"
    quote_result = None
    quote_error = None
    status_code = 200
    try:
        normalized_symbol = _ensure_onchain_symbol(symbol)
        quote_result = build_quote_payload(normalized_symbol, amount_usdt, direction=quote_direction)
        cached_quote = update_quote_cache(normalized_symbol, quote_result, direction=quote_direction)
        quote_result = dict(quote_result)
        quote_result["quoted_at"] = cached_quote.get("quoted_at")
    except Exception as exc:
        quote_error = str(exc)
        status_code = 400
    context = _onchain_view(
        quote_symbol=normalized_symbol,
        quote_amount_usdt=amount_usdt,
        quote_direction=quote_direction,
        quote_result=quote_result,
        quote_error=quote_error,
    )
    context.update(
        {
            "request": request,
            "project_name": "TraderBot Local Console",
        }
    )
    return templates.TemplateResponse(request, "onchain.html", context, status_code=status_code)


@app.get("/futures", response_class=HTMLResponse)
def futures_page(request: Request):
    context = _load_futures_view()
    futures_symbol_message = request.query_params.get("futures_symbol_message")
    futures_symbol_error = request.query_params.get("futures_symbol_error")
    futures_config_message = request.query_params.get("futures_config_message")
    futures_config_error = request.query_params.get("futures_config_error")
    futures_active_tab = "positions"
    if futures_symbol_message or futures_symbol_error or context.get("futures_symbol_edit_config"):
        futures_active_tab = "symbols"
    if futures_config_message or futures_config_error:
        futures_active_tab = "config"
    context.update(
        {
            "request": request,
            "project_name": "TraderBot Local Console",
            "futures_symbol_message": futures_symbol_message,
            "futures_symbol_error": futures_symbol_error,
            "futures_active_tab": futures_active_tab,
        }
    )
    context["futures_config_view"]["message"] = futures_config_message
    context["futures_config_view"]["error"] = futures_config_error
    return templates.TemplateResponse(request, "futures.html", context)


@app.get("/spot", response_class=HTMLResponse)
def spot_page(request: Request, symbol: str = ""):
    context = _load_spot_view(symbol=symbol)
    spot_config_message = request.query_params.get("spot_config_message")
    spot_config_error = request.query_params.get("spot_config_error")
    context.update(
        {
            "request": request,
            "spot_active_tab": "config" if spot_config_message or spot_config_error else "positions",
        }
    )
    context["spot_config"]["message"] = spot_config_message
    context["spot_config"]["error"] = spot_config_error
    return templates.TemplateResponse(request, "spot.html", context)


@app.post("/safety")
async def safety_save(request: Request):
    form = await _read_form_data(request)
    settings = load_project_config()
    execution_config = load_execution_runtime(settings)
    current = load_runtime_safety_config()
    spot_enabled = _parse_safety_bool(form, "spot_trading_enabled")
    futures_enabled = _parse_safety_bool(form, "futures_trading_enabled")
    if execution_config.mode == "live":
        if (
            (spot_enabled and not current.spot_trading_enabled)
            or (futures_enabled and not current.futures_trading_enabled)
        ) and form.get("live_confirm", "") != "YES":
            return RedirectResponse(url="/", status_code=303)
    save_runtime_safety_config(
        RuntimeSafetyConfig(
            global_kill_switch=_parse_safety_bool(form, "global_kill_switch"),
            spot_trading_enabled=spot_enabled,
            futures_trading_enabled=futures_enabled,
            daily_loss_limit_pct=float(form.get("daily_loss_limit_pct", current.daily_loss_limit_pct)),
            max_consecutive_errors=int(float(form.get("max_consecutive_errors", current.max_consecutive_errors))),
            max_open_trades_per_hour=int(float(form.get("max_open_trades_per_hour", current.max_open_trades_per_hour))),
        )
    )
    return RedirectResponse(url="/", status_code=303)


@app.post("/spot/config")
async def spot_config_save(request: Request):
    form = await _read_form_data(request)
    try:
        settings = load_project_config()
        updated_settings = {key: value for key, value in settings.items() if key != "symbols_config"}
        fields = _flatten_editable_settings(updated_settings)
        for field in fields:
            path = str(field["path"])
            form_key = f"setting__{path}"
            if form_key not in form:
                continue
            current_value = _get_path_value(updated_settings, path)
            _set_path_value(
                updated_settings,
                path,
                _coerce_spot_setting_value(form[form_key], current_value),
            )
        _write_settings_config(updated_settings)
        _log_settings_action("spot_settings_update", mode="spot")
    except Exception as exc:
        return RedirectResponse(
            url=f"/spot?spot_config_error={quote(str(exc))}",
            status_code=303,
        )
    return RedirectResponse(
        url="/spot?spot_config_message=Spot%20%E9%85%8D%E7%BD%AE%E5%B7%B2%E6%9B%B4%E6%96%B0",
        status_code=303,
    )


@app.post("/futures/config")
async def futures_config_save(request: Request):
    form = await _read_form_data(request)
    try:
        settings = load_yaml_mapping(DEFAULT_FUTURES_SETTINGS_PATH)
        updated_fields: list[str] = []
        for path in _all_futures_setting_paths():
            if path not in form:
                continue
            _set_path_value(
                settings,
                path,
                _coerce_futures_setting_value(path, form[path]),
            )
            updated_fields.append(path)
        risk = settings.get("risk", {})
        if isinstance(risk, dict):
            partial1_sell_pct = float(risk.get("partial1_sell_pct", 0))
            partial2_sell_pct = float(risk.get("partial2_sell_pct", 0))
            if partial1_sell_pct + partial2_sell_pct > 100:
                raise ValueError("第一次分批止盈比例 + 第二次分批止盈比例 必须小于或等于 100")
        DEFAULT_FUTURES_SETTINGS_PATH.write_text(_dump_yaml(settings), encoding="utf-8")
        _log_settings_action(
            "futures_settings_update",
            mode="futures",
            updated_fields=tuple(updated_fields),
        )
    except Exception as exc:
        return RedirectResponse(
            url=f"/futures?futures_config_error={quote(str(exc))}",
            status_code=303,
        )
    return RedirectResponse(
        url="/futures?futures_config_message=Futures%20%E9%85%8D%E7%BD%AE%E5%B7%B2%E6%9B%B4%E6%96%B0",
        status_code=303,
    )


@app.get("/futures/symbols/{symbol}/edit", response_class=HTMLResponse)
def futures_symbol_edit_page(request: Request, symbol: str):
    normalized_symbol = symbol.strip().upper()
    if not SYMBOL_PATTERN.fullmatch(normalized_symbol):
        return _futures_symbols_redirect(error="invalid_futures_symbol")

    try:
        symbol_configs = _load_futures_symbol_mappings()
    except Exception as exc:
        return _futures_symbols_redirect(error=f"load_failed: {exc}")

    if normalized_symbol not in symbol_configs:
        context = _load_futures_view()
        context.update(
            {
                "request": request,
                "project_name": "TraderBot Local Console",
                "futures_symbol_error": f"标的不存在：{normalized_symbol}",
                "futures_active_tab": "symbols",
            }
        )
        return templates.TemplateResponse(request, "futures.html", context, status_code=404)

    return _render_futures_symbol_edit_page(
        request,
        normalized_symbol,
        symbol_configs[normalized_symbol],
    )


@app.post("/futures/symbols/{symbol}/edit", response_class=HTMLResponse)
async def futures_symbol_edit_save(request: Request, symbol: str):
    normalized_symbol = symbol.strip().upper()
    if not SYMBOL_PATTERN.fullmatch(normalized_symbol):
        return _futures_symbols_redirect(error="invalid_futures_symbol")

    form = await _read_form_data(request)
    submitted_config = {
        "enabled": form.get("enabled", "true").strip().lower() == "true",
        "strategy_name": form.get("strategy_name", form.get("strategy", "trend_long")),
        "leverage": form.get("leverage", ""),
        "margin_amount": form.get("margin_amount", ""),
        "trend_timeframe": form.get("trend_timeframe", "4h"),
        "signal_timeframe": form.get("signal_timeframe", "15m"),
        "market_session_filter": form.get("market_session_filter", "none"),
        "strategy": {
            key: form.get(f"strategy.{key}", "").strip()
            for key, _, _, _ in FUTURES_SYMBOL_STRATEGY_FIELD_SPECS
            if form.get(f"strategy.{key}", "").strip() != ""
        },
        "risk": {
            key: form.get(f"risk.{key}", "").strip()
            for key, _, _, _ in FUTURES_SYMBOL_RISK_FIELD_SPECS
            if form.get(f"risk.{key}", "").strip() != ""
        },
    }

    try:
        futures_config = load_futures_config()
        updated_symbols = {
            config_symbol: _futures_symbol_config_mapping(symbol_config)
            for config_symbol, symbol_config in futures_config.symbols.items()
        }
        if normalized_symbol not in updated_symbols:
            raise ValueError("futures_symbol_not_found")

        updated_config = _parse_futures_symbol_config_from_form(form, futures_config.risk)
        updated_symbols[normalized_symbol] = updated_config
        result = save_futures_symbols_config(updated_symbols)
        if not result.get("ok"):
            raise ValueError(str(result.get("error")))

        _log_futures_symbol_action(
            "futures_symbol_update",
            normalized_symbol,
            reason="frontend_edit",
            enabled=updated_config["enabled"],
            strategy=updated_config["strategy_name"],
            leverage=updated_config["leverage"],
            margin_amount=updated_config["margin_amount"],
            trend_timeframe=updated_config["trend_timeframe"],
            signal_timeframe=updated_config["signal_timeframe"],
            market_session_filter=updated_config["market_session_filter"],
        )
    except Exception as exc:
        return _render_futures_symbol_edit_page(
            request,
            normalized_symbol,
            submitted_config,
            error=str(exc),
            status_code=400,
        )

    return _futures_symbols_redirect(message=f"{normalized_symbol} saved")


@app.post("/futures/symbols/add")
async def futures_symbol_add(request: Request):
    form_payload = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
    form = {key: values[-1] if values else "" for key, values in form_payload.items()}
    symbol = str(form.get("symbol", ""))
    strategy = str(form.get("strategy", "trend_long"))
    leverage = str(form.get("leverage", "1"))
    margin_amount = str(form.get("margin_amount", "10"))
    trend_timeframe = str(form.get("trend_timeframe", "4h"))
    signal_timeframe = str(form.get("signal_timeframe", "15m"))
    market_session_filter = str(form.get("market_session_filter", "none"))
    enabled = str(form.get("enabled", "")).lower() in {"1", "true", "yes", "on"}

    normalized_symbol = symbol.strip().upper()
    if not SYMBOL_PATTERN.fullmatch(normalized_symbol):
        return _futures_symbols_redirect(error="symbol_must_be_uppercase_usdt")
    if strategy not in ALLOWED_FUTURES_STRATEGIES:
        return _futures_symbols_redirect(error="strategy_must_be_trend_long")
    if trend_timeframe not in ALLOWED_FUTURES_TIMEFRAMES:
        return _futures_symbols_redirect(error="invalid_trend_timeframe")
    if signal_timeframe not in ALLOWED_FUTURES_TIMEFRAMES:
        return _futures_symbols_redirect(error="invalid_signal_timeframe")
    if market_session_filter not in ALLOWED_MARKET_SESSION_FILTERS:
        return _futures_symbols_redirect(error="invalid_market_session_filter")

    parsed_leverage, leverage_error = _parse_futures_symbol_number(leverage, "leverage")
    if leverage_error:
        return _futures_symbols_redirect(error=leverage_error)
    parsed_margin, margin_error = _parse_futures_symbol_number(margin_amount, "margin_amount")
    if margin_error:
        return _futures_symbols_redirect(error=margin_error)

    try:
        updated_symbols = _load_futures_symbol_mappings()
    except Exception as exc:
        return _futures_symbols_redirect(error=f"load_failed: {exc}")

    if normalized_symbol in updated_symbols:
        return _futures_symbols_redirect(error="futures_symbol_already_exists")

    updated_symbols[normalized_symbol] = {
        "enabled": enabled,
        "strategy_name": strategy,
        "leverage": parsed_leverage,
        "margin_amount": parsed_margin,
        "trend_timeframe": trend_timeframe,
        "signal_timeframe": signal_timeframe,
        "market_session_filter": market_session_filter,
    }
    result = save_futures_symbols_config(updated_symbols)
    if not result.get("ok"):
        return _futures_symbols_redirect(error=f"save_failed: {result.get('error')}")

    _log_futures_symbol_action(
        "futures_symbol_add",
        normalized_symbol,
        reason="frontend_add",
        enabled=enabled,
        strategy=strategy,
        leverage=parsed_leverage,
        margin_amount=parsed_margin,
        trend_timeframe=trend_timeframe,
        signal_timeframe=signal_timeframe,
        market_session_filter=market_session_filter,
    )
    return _futures_symbols_redirect(message=f"{normalized_symbol} added")


@app.post("/futures/symbols/{symbol}/toggle")
def futures_symbol_toggle(symbol: str):
    normalized_symbol = symbol.upper()
    if not SYMBOL_PATTERN.fullmatch(normalized_symbol):
        return _futures_symbols_redirect(error="invalid_futures_symbol")

    try:
        updated_symbols = _load_futures_symbol_mappings()
    except Exception as exc:
        return _futures_symbols_redirect(error=f"load_failed: {exc}")

    if normalized_symbol not in updated_symbols:
        return _futures_symbols_redirect(error="futures_symbol_not_found")

    updated_symbols[normalized_symbol]["enabled"] = not bool(
        updated_symbols[normalized_symbol]["enabled"]
    )
    result = save_futures_symbols_config(updated_symbols)
    if not result.get("ok"):
        return _futures_symbols_redirect(error=f"save_failed: {result.get('error')}")
    state = "enabled" if updated_symbols[normalized_symbol]["enabled"] else "disabled"
    _log_futures_symbol_action(
        "futures_symbol_toggle",
        normalized_symbol,
        reason=f"frontend_{state}",
        enabled=updated_symbols[normalized_symbol]["enabled"],
    )
    return _futures_symbols_redirect(message=f"{normalized_symbol} {state}")


@app.post("/futures/symbols/{symbol}/delete")
def futures_symbol_delete(symbol: str):
    normalized_symbol = symbol.upper()
    if not SYMBOL_PATTERN.fullmatch(normalized_symbol):
        return _futures_symbols_redirect(error="invalid_futures_symbol")

    try:
        updated_symbols = _load_futures_symbol_mappings()
    except Exception as exc:
        return _futures_symbols_redirect(error=f"load_failed: {exc}")

    if normalized_symbol not in updated_symbols:
        return _futures_symbols_redirect(error="futures_symbol_not_found")

    removed_symbol = updated_symbols.pop(normalized_symbol)
    result = save_futures_symbols_config(updated_symbols)
    if not result.get("ok"):
        return _futures_symbols_redirect(error=f"save_failed: {result.get('error')}")
    _log_futures_symbol_action(
        "futures_symbol_delete",
        normalized_symbol,
        reason="frontend_delete",
        removed_symbol=removed_symbol,
    )
    return _futures_symbols_redirect(message=f"{normalized_symbol} deleted")


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


@app.get("/api/futures/klines")
def futures_klines_api(
    symbol: str,
    interval: str = "5m",
    limit: int = 40,
):
    normalized_symbol = symbol.strip().upper()
    if not SYMBOL_PATTERN.fullmatch(normalized_symbol):
        return JSONResponse({"error": "invalid_symbol"}, status_code=400)
    if interval not in FUTURES_KLINE_INTERVAL_OPTIONS:
        return JSONResponse({"error": "invalid_interval"}, status_code=400)
    if limit < 1 or limit > 1000:
        return JSONResponse({"error": "limit_must_be_between_1_and_1000"}, status_code=400)

    try:
        config = load_futures_config()
    except Exception as exc:
        return JSONResponse(
            {"error": "futures_config_error", "message": str(exc)},
            status_code=500,
        )
    if normalized_symbol not in config.symbols:
        return JSONResponse({"error": "symbol_not_configured"}, status_code=404)
    symbol_config = config.symbols[normalized_symbol]

    client = BinanceFuturesClient(
        base_url=config.futures.base_url,
        timeout=config.futures.request_timeout_seconds,
    )
    try:
        query_limit = 1000 if symbol_config.market_session_filter == "us_regular" else limit
        raw_klines = client.get_klines(normalized_symbol, interval, limit=query_limit)
    except Exception as exc:
        return JSONResponse(
            {"error": "futures_klines_query_failed", "message": str(exc)},
            status_code=502,
        )
    if not isinstance(raw_klines, list):
        return JSONResponse(
            {"error": "unexpected_futures_klines_payload"},
            status_code=502,
        )

    filtered_klines = filter_klines_by_session(
        raw_klines,
        symbol_config.market_session_filter,
    )
    display_klines = filtered_klines[-limit:]
    rows = [
        row
        for row in (_futures_kline_row(kline) for kline in display_klines)
        if row is not None
    ]
    return JSONResponse(
        {
            "symbol": normalized_symbol,
            "interval": interval,
            "limit": limit,
            "market_session_filter": symbol_config.market_session_filter,
            "raw_bars_count": len(raw_klines),
            "filtered_bars_count": len(filtered_klines),
            "filtered_out_bars": max(len(raw_klines) - len(filtered_klines), 0),
            "klines": rows,
        }
    )


@app.get("/logs", response_class=HTMLResponse)
def logs_page(request: Request, log_type: str = "system", symbol: str = "all"):
    selected_type = log_type if log_type in LOG_FILE_MAP else "system"
    symbols = (
        _configured_futures_symbol_names()
        if selected_type == "futures"
        else _configured_symbol_names()
    )
    selected_symbol = symbol.strip().upper()
    if selected_symbol not in symbols:
        selected_symbol = "all"
    symbol_filter = None if selected_symbol == "all" else selected_symbol
    if selected_type == "futures":
        lines, log_exists = _read_futures_log_lines(symbol=symbol_filter, line_count=100)
        empty_message = "No futures logs yet"
    else:
        lines = _read_recent_log_lines(LOG_FILE_MAP[selected_type], symbol=symbol_filter, line_count=100)
        log_exists = LOG_FILE_MAP[selected_type].exists()
        empty_message = "暂无日志" if symbol_filter is None else "该币种暂无日志"
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
            "log_exists": log_exists,
            "empty_message": empty_message,
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


@app.get("/api/futures/paper_performance")
def futures_paper_performance_api():
    return JSONResponse({"data": _futures_paper_performance_rows()})


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
        return _render_spot_symbol_edit_page(request, normalized_symbol)
    except Exception as exc:
        context = _load_symbols_view(error=f"标的不存在或无法读取：{exc}")
        context.update({"request": request, "project_name": "TraderBot Local Console"})
        return templates.TemplateResponse(request, "symbols.html", context, status_code=404)


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
        if "strategy" not in updated_config:
            symbols[normalized_symbol].pop("strategy", None)
        if "risk" not in updated_config:
            symbols[normalized_symbol].pop("risk", None)
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
            "strategy_name": form.get("strategy_name", "trend_long"),
            "leverage": form.get("leverage", ""),
            "margin_amount": form.get("margin_amount", ""),
            "trend_timeframe": form.get("trend_timeframe", "4h"),
            "signal_timeframe": form.get("signal_timeframe", "15m"),
            "order_amount": form.get("order_amount", ""),
            "max_loss_amount": form.get("max_loss_amount", ""),
            "market_session_filter": form.get("market_session_filter", ""),
            "paused_by_loss": form.get("paused_by_loss", "false").strip().lower() == "true",
            "strategy": {
                key: form.get(f"strategy.{key}", "").strip()
                for key, _, _, _ in SPOT_SYMBOL_STRATEGY_FIELD_SPECS
                if form.get(f"strategy.{key}", "").strip() != ""
            },
            "risk": {
                key: form.get(f"risk.{key}", "").strip()
                for key, _, _, _ in SPOT_SYMBOL_RISK_FIELD_SPECS
                if form.get(f"risk.{key}", "").strip() != ""
            },
        }
        return _render_spot_symbol_edit_page(
            request,
            normalized_symbol,
            symbol_config=submitted_config,
            error=str(exc),
            status_code=400,
        )

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
