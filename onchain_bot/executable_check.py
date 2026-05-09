from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
QUOTE_TTL_SECONDS = 10 * 60


def _signal_action(signal: dict[str, Any] | None) -> str:
    if not signal:
        return "error"
    action = signal.get("action")
    return str(action) if action is not None else "error"


def quote_is_stale(
    quote_result: dict[str, Any] | None,
    *,
    ttl_seconds: int = QUOTE_TTL_SECONDS,
) -> bool:
    if not quote_result:
        return False
    quoted_at = quote_result.get("quoted_at")
    if not isinstance(quoted_at, str) or not quoted_at:
        return True
    try:
        timestamp = datetime.fromisoformat(quoted_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    age_seconds = (datetime.now(timezone.utc) - timestamp.astimezone(timezone.utc)).total_seconds()
    return age_seconds > ttl_seconds


def check_onchain_executable(
    *,
    mapping: Any,
    futures_signal: dict[str, Any] | None,
    quote_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    reasons: list[str] = []

    if not bool(getattr(mapping, "enabled", False)):
        reasons.append("mapping_disabled")
    if getattr(mapping, "signal_source", "") != "futures":
        reasons.append("signal_source_not_futures")
    if not getattr(mapping, "source_symbol", ""):
        reasons.append("missing_source_symbol")
    if getattr(mapping, "token_address", "") == ZERO_ADDRESS:
        reasons.append("missing_token_address")
    if getattr(mapping, "quote_token_address", "") == ZERO_ADDRESS:
        reasons.append("missing_quote_token_address")
    if float(getattr(mapping, "max_trade_usdt", 0.0)) <= 0:
        reasons.append("invalid_max_trade_usdt")
    if float(getattr(mapping, "max_slippage_pct", -1.0)) < 0:
        reasons.append("invalid_max_slippage_pct")

    action = _signal_action(futures_signal)
    if action == "HOLD":
        reasons.append("signal_hold")
    elif action == "LONG":
        if quote_result is None:
            reasons.append("quote_not_available")
        elif not bool(quote_result.get("ok", False)):
            reasons.append("quote_error")
        elif quote_is_stale(quote_result):
            reasons.append("quote_stale")
    elif action.startswith("CLOSE"):
        reasons.append("close_quote_not_implemented")
    else:
        reasons.append("signal_not_executable")

    if futures_signal and not bool(futures_signal.get("ok", False)):
        reasons.append("signal_error")

    return {
        "executable": not reasons,
        "reasons": reasons,
    }
