from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from onchain_bot.config_loader import load_onchain_settings_config
from onchain_bot.paper_state import normalize_paper_state


DEFAULT_TRADE_LIMITS = {
    "max_trade_usdc": 50.0,
    "max_trade_usdt": 50.0,
    "max_open_positions": 3,
    "max_opens_per_day": 5,
    "max_closes_per_day": 5,
    "min_trade_interval_seconds": 300,
}


def _now_utc(now: datetime | None = None) -> datetime:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _action_kind(action: str) -> str:
    normalized = str(action or "").strip().upper()
    if normalized in {"LONG", "OPEN", "PAPER_OPEN"}:
        return "open"
    if normalized.startswith("CLOSE") or normalized in {"SELL", "PAPER_CLOSE"}:
        return "close"
    return normalized.lower() or "unknown"


def effective_trade_limits(mapping: Any) -> dict[str, float | int]:
    try:
        settings = load_onchain_settings_config()
        global_limits: dict[str, float | int] = {
            "max_trade_usdc": settings.risk_max_trade_usdc,
            "max_trade_usdt": settings.risk_max_trade_usdc,
            "max_open_positions": settings.risk_max_open_positions,
            "max_opens_per_day": settings.risk_max_opens_per_day,
            "max_closes_per_day": settings.risk_max_closes_per_day,
            "min_trade_interval_seconds": settings.risk_min_trade_interval_seconds,
        }
    except Exception:
        global_limits = {}

    limits: dict[str, float | int] = {**DEFAULT_TRADE_LIMITS, **global_limits}
    symbol_risk = getattr(mapping, "risk", None)
    if isinstance(symbol_risk, dict):
        for key in ("max_trade_usdc", "max_trade_usdt", "min_trade_interval_seconds"):
            if key in symbol_risk and symbol_risk[key] is not None:
                limits[key] = float(symbol_risk[key])
    return limits


def check_onchain_trade_limits(
    symbol: str,
    action: str,
    state: dict[str, Any],
    mapping: Any,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = _now_utc(now)
    normalized_symbol = symbol.strip().upper()
    action_kind = _action_kind(action)
    state = normalize_paper_state(state, now=current)
    positions = state.get("positions", {})
    daily_stats = state.get("daily_stats", {})
    last_trade_times = state.get("last_trade_times", {})
    limits = effective_trade_limits(mapping)

    failures: list[str] = []
    has_position = normalized_symbol in positions
    current_positions_count = len(positions) if isinstance(positions, dict) else 0
    trade_amount = float(getattr(mapping, "max_trade_usdc", getattr(mapping, "max_trade_usdt", 0.0)) or 0.0)

    if action_kind == "open":
        if trade_amount > float(limits["max_trade_usdc"]):
            failures.append("trade_amount_exceeds_max")
        if has_position:
            failures.append("position_exists")
        if current_positions_count >= int(limits["max_open_positions"]):
            failures.append("max_open_positions_reached")
        if int(daily_stats.get("opens_count", 0)) >= int(limits["max_opens_per_day"]):
            failures.append("max_opens_per_day_reached")
    elif action_kind == "close":
        if not has_position:
            failures.append("position_not_found")
        if int(daily_stats.get("closes_count", 0)) >= int(limits["max_closes_per_day"]):
            failures.append("max_closes_per_day_reached")

    last_trade_time = _parse_timestamp(last_trade_times.get(normalized_symbol) if isinstance(last_trade_times, dict) else None)
    seconds_since_last_trade = None
    if last_trade_time is not None:
        seconds_since_last_trade = (current - last_trade_time).total_seconds()
        if seconds_since_last_trade < float(limits["min_trade_interval_seconds"]):
            failures.append("trade_interval_too_short")

    failures = list(dict.fromkeys(failures))
    return {
        "ok": not failures,
        "reason": "ok" if not failures else failures[0],
        "failures": failures,
        "details": {
            "symbol": normalized_symbol,
            "action": action_kind,
            "trade_amount_usdc": trade_amount,
            "trade_amount_usdt": trade_amount,
            "current_positions_count": current_positions_count,
            "daily_stats": daily_stats,
            "last_trade_time": last_trade_times.get(normalized_symbol) if isinstance(last_trade_times, dict) else None,
            "seconds_since_last_trade": seconds_since_last_trade,
            "limits": limits,
        },
    }
