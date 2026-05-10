from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from config.loader import dump_yaml, _load_yaml_file


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RUNTIME_SAFETY_PATH = PROJECT_ROOT / "config" / "runtime_safety.yaml"
DEFAULT_SAFETY_STATE_PATH = PROJECT_ROOT / "runtime" / "safety_state.json"
LIVE_CONFIRM_ENV_VAR = "TRADEBOT_CONFIRM_LIVE"
LIVE_CONFIRM_VALUE = "YES"
OPEN_ACTIONS = {"BUY", "LONG"}
CLOSE_ACTIONS = {"SELL", "CLOSE", "CLOSE_FULL", "CLOSE_PARTIAL_30", "CLOSE_PARTIAL_50"}


@dataclass(frozen=True)
class RuntimeSafetyConfig:
    global_kill_switch: bool = False
    spot_trading_enabled: bool = False
    futures_trading_enabled: bool = False
    onchain_paper_enabled: bool = True
    onchain_trading_enabled: bool = False
    onchain_kill_switch: bool = False
    daily_loss_limit_pct: float = 5.0
    max_consecutive_errors: int = 5
    max_open_trades_per_hour: int = 5


@dataclass(frozen=True)
class SafetyDecision:
    allowed: bool
    reason: str = "ok"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _today_key(now: datetime | None = None) -> str:
    return (now or _utc_now()).date().isoformat()


def _default_config_mapping() -> dict[str, Any]:
    return {
        "global_kill_switch": False,
        "daily_loss_limit_pct": 5.0,
        "max_consecutive_errors": 5,
        "max_open_trades_per_hour": 5,
        "spot": {"trading_enabled": False},
        "futures": {"trading_enabled": False},
        "onchain": {
            "paper_enabled": True,
            "trading_enabled": False,
            "kill_switch": False,
        },
    }


def ensure_runtime_safety_config(path: Path = DEFAULT_RUNTIME_SAFETY_PATH) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_yaml(_default_config_mapping()), encoding="utf-8")


def load_runtime_safety_config(path: Path = DEFAULT_RUNTIME_SAFETY_PATH) -> RuntimeSafetyConfig:
    ensure_runtime_safety_config(path)
    payload = _load_yaml_file(path)
    spot = payload.get("spot", {})
    futures = payload.get("futures", {})
    onchain = payload.get("onchain", {})
    if not isinstance(spot, dict):
        spot = {}
    if not isinstance(futures, dict):
        futures = {}
    if not isinstance(onchain, dict):
        onchain = {}
    return RuntimeSafetyConfig(
        global_kill_switch=bool(payload.get("global_kill_switch", False)),
        spot_trading_enabled=bool(spot.get("trading_enabled", False)),
        futures_trading_enabled=bool(futures.get("trading_enabled", False)),
        onchain_paper_enabled=bool(onchain.get("paper_enabled", True)),
        onchain_trading_enabled=False,
        onchain_kill_switch=bool(onchain.get("kill_switch", False)),
        daily_loss_limit_pct=float(payload.get("daily_loss_limit_pct", 5.0) or 5.0),
        max_consecutive_errors=int(payload.get("max_consecutive_errors", 5) or 5),
        max_open_trades_per_hour=int(payload.get("max_open_trades_per_hour", 5) or 5),
    )


def save_runtime_safety_config(
    config: RuntimeSafetyConfig,
    path: Path = DEFAULT_RUNTIME_SAFETY_PATH,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        dump_yaml(
            {
                "global_kill_switch": config.global_kill_switch,
                "daily_loss_limit_pct": config.daily_loss_limit_pct,
                "max_consecutive_errors": config.max_consecutive_errors,
                "max_open_trades_per_hour": config.max_open_trades_per_hour,
                "spot": {"trading_enabled": config.spot_trading_enabled},
                "futures": {"trading_enabled": config.futures_trading_enabled},
                "onchain": {
                    "paper_enabled": config.onchain_paper_enabled,
                    "trading_enabled": False,
                    "kill_switch": config.onchain_kill_switch,
                },
            }
        ),
        encoding="utf-8",
    )


def load_safety_state(path: Path = DEFAULT_SAFETY_STATE_PATH) -> dict[str, Any]:
    if not path.exists():
        return _default_state()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    state = _default_state()
    state.update(payload)
    if state.get("today") != _today_key():
        state["today"] = _today_key()
        state["today_realized_pnl"] = 0.0
        state["open_trades"] = []
    state["open_trades"] = _recent_open_trades(state.get("open_trades", []))
    return state


def save_safety_state(state: dict[str, Any], path: Path = DEFAULT_SAFETY_STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = _utc_now().isoformat()
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def _default_state() -> dict[str, Any]:
    return {
        "consecutive_errors": 0,
        "last_error": None,
        "today": _today_key(),
        "today_realized_pnl": 0.0,
        "open_trades": [],
        "kill_switch_reason": None,
        "updated_at": None,
    }


def _recent_open_trades(open_trades: Any) -> list[dict[str, Any]]:
    if not isinstance(open_trades, list):
        return []
    cutoff = _utc_now() - timedelta(hours=1)
    recent: list[dict[str, Any]] = []
    for item in open_trades:
        if not isinstance(item, dict):
            continue
        timestamp = str(item.get("timestamp", ""))
        try:
            opened_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError:
            continue
        if opened_at >= cutoff:
            recent.append(item)
    return recent


def set_global_kill_switch(reason: str, *, enabled: bool = True) -> None:
    config = load_runtime_safety_config()
    save_runtime_safety_config(
        RuntimeSafetyConfig(
            global_kill_switch=enabled,
            spot_trading_enabled=config.spot_trading_enabled,
            futures_trading_enabled=config.futures_trading_enabled,
            onchain_paper_enabled=config.onchain_paper_enabled,
            onchain_trading_enabled=False,
            onchain_kill_switch=config.onchain_kill_switch,
            daily_loss_limit_pct=config.daily_loss_limit_pct,
            max_consecutive_errors=config.max_consecutive_errors,
            max_open_trades_per_hour=config.max_open_trades_per_hour,
        )
    )
    state = load_safety_state()
    state["kill_switch_reason"] = reason if enabled else None
    save_safety_state(state)
    print(f"[SAFETY] kill switch {'enabled' if enabled else 'disabled'} reason={reason}")


def record_safety_error(reason: str) -> int:
    config = load_runtime_safety_config()
    state = load_safety_state()
    state["consecutive_errors"] = int(state.get("consecutive_errors", 0) or 0) + 1
    state["last_error"] = reason
    save_safety_state(state)
    if state["consecutive_errors"] >= config.max_consecutive_errors:
        set_global_kill_switch("max_consecutive_errors_reached")
        print("[SAFETY] too many errors reason=max_consecutive_errors_reached")
    return state["consecutive_errors"]


def reset_safety_errors() -> None:
    state = load_safety_state()
    if int(state.get("consecutive_errors", 0) or 0) == 0 and state.get("last_error") is None:
        return
    state["consecutive_errors"] = 0
    state["last_error"] = None
    save_safety_state(state)


def record_open_trade(module: str, symbol: str) -> None:
    state = load_safety_state()
    open_trades = _recent_open_trades(state.get("open_trades", []))
    open_trades.append({"timestamp": _utc_now().isoformat(), "module": module, "symbol": symbol})
    state["open_trades"] = open_trades
    save_safety_state(state)


def record_realized_pnl(realized_pnl: float, *, account_equity: float | None = None) -> None:
    config = load_runtime_safety_config()
    state = load_safety_state()
    state["today_realized_pnl"] = float(state.get("today_realized_pnl", 0.0) or 0.0) + float(realized_pnl)
    save_safety_state(state)
    equity = float(account_equity or 0.0)
    if equity <= 0:
        return
    if state["today_realized_pnl"] <= -(config.daily_loss_limit_pct / 100.0) * equity:
        set_global_kill_switch("daily_loss_limit_triggered")
        print("[SAFETY] daily loss limit triggered")


def check_new_entry_allowed(
    module: str,
    *,
    app_mode: str,
    account_equity: float | None = None,
) -> SafetyDecision:
    config = load_runtime_safety_config()
    state = load_safety_state()
    if config.global_kill_switch:
        return SafetyDecision(False, "global_kill_switch_enabled")
    if module == "spot" and not config.spot_trading_enabled:
        return SafetyDecision(False, "spot_trading_disabled")
    if module == "futures" and not config.futures_trading_enabled:
        return SafetyDecision(False, "futures_trading_disabled")
    if app_mode == "live" and os.environ.get(LIVE_CONFIRM_ENV_VAR) != LIVE_CONFIRM_VALUE:
        print("[SAFETY] live trading blocked reason=live_trading_not_confirmed")
        return SafetyDecision(False, "live_trading_not_confirmed")
    equity = float(account_equity or 0.0)
    if equity > 0 and float(state.get("today_realized_pnl", 0.0) or 0.0) <= -(config.daily_loss_limit_pct / 100.0) * equity:
        set_global_kill_switch("daily_loss_limit_triggered")
        print("[SAFETY] daily loss limit triggered")
        return SafetyDecision(False, "daily_loss_limit_triggered")
    if len(_recent_open_trades(state.get("open_trades", []))) >= config.max_open_trades_per_hour:
        print("[SAFETY] too many open trades reason=too_many_open_trades")
        return SafetyDecision(False, "too_many_open_trades")
    return SafetyDecision(True)


def check_onchain_paper_allowed() -> SafetyDecision:
    config = load_runtime_safety_config()
    if config.global_kill_switch:
        return SafetyDecision(False, "global_kill_switch_enabled")
    if config.onchain_kill_switch:
        return SafetyDecision(False, "onchain_kill_switch_enabled")
    if not config.onchain_paper_enabled:
        return SafetyDecision(False, "onchain_paper_disabled")
    if config.onchain_trading_enabled:
        return SafetyDecision(False, "onchain_live_not_supported_yet")
    return SafetyDecision(True)


def check_market_data_safe(mark_price: float | None, klines: list[Any] | None = None) -> SafetyDecision:
    if mark_price is None or mark_price <= 0:
        print("[SAFETY] abnormal market data reason=invalid_mark_price")
        return SafetyDecision(False, "abnormal_market_data")
    for kline in klines or []:
        if not isinstance(kline, (list, tuple)) or len(kline) < 5:
            continue
        try:
            open_price = float(kline[1])
            high_price = float(kline[2])
            low_price = float(kline[3])
        except (TypeError, ValueError):
            continue
        baseline = open_price if open_price > 0 else mark_price
        if baseline > 0 and (high_price - low_price) / baseline > 0.5:
            print("[SAFETY] abnormal market data reason=abnormal_kline_volatility")
            return SafetyDecision(False, "abnormal_market_data")
    return SafetyDecision(True)


def safety_status_payload(account_equity: float | None = None) -> dict[str, Any]:
    config = load_runtime_safety_config()
    state = load_safety_state()
    daily_loss_limit = None
    if account_equity and account_equity > 0:
        daily_loss_limit = -(config.daily_loss_limit_pct / 100.0) * float(account_equity)
    return {
        "global_kill_switch": config.global_kill_switch,
        "spot_trading_enabled": config.spot_trading_enabled,
        "futures_trading_enabled": config.futures_trading_enabled,
        "onchain_paper_enabled": config.onchain_paper_enabled,
        "onchain_trading_enabled": config.onchain_trading_enabled,
        "onchain_kill_switch": config.onchain_kill_switch,
        "consecutive_errors": int(state.get("consecutive_errors", 0) or 0),
        "today_realized_pnl": float(state.get("today_realized_pnl", 0.0) or 0.0),
        "daily_loss_limit_pct": config.daily_loss_limit_pct,
        "daily_loss_limit": daily_loss_limit,
        "open_trades_last_hour": len(_recent_open_trades(state.get("open_trades", []))),
        "max_open_trades_per_hour": config.max_open_trades_per_hour,
        "max_consecutive_errors": config.max_consecutive_errors,
        "kill_switch_reason": state.get("kill_switch_reason"),
        "status": "kill_switch" if config.global_kill_switch else "warning" if state.get("consecutive_errors") else "ok",
    }
