from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from onchain_bot.risk import check_onchain_quote_risk
from onchain_bot.session_filter import get_execution_session_status


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
    buy_quote_result: dict[str, Any] | None = None,
    sell_quote_result: dict[str, Any] | None = None,
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
    session_status = get_execution_session_status(getattr(mapping, "execution_session_filter", "us_regular"))
    if not session_status["session_allowed"]:
        reasons.append("outside_us_regular_session")

    action = _signal_action(futures_signal)
    risk_quote = quote_result
    risk_direction = "buy"
    if action == "HOLD":
        reasons.append("signal_hold")
    elif action == "LONG":
        buy_quote = buy_quote_result or quote_result
        risk_quote = buy_quote
        risk_direction = "buy"
        if buy_quote is None:
            reasons.append("buy_quote_not_available")
        elif not bool(buy_quote.get("ok", False)):
            reasons.append("quote_error")
        elif quote_is_stale(buy_quote):
            reasons.append("quote_stale")
    elif action.startswith("CLOSE"):
        sell_quote = sell_quote_result or quote_result
        risk_quote = sell_quote
        risk_direction = "sell"
        if sell_quote is None:
            reasons.append("sell_quote_not_available")
        elif not bool(sell_quote.get("ok", False)):
            reasons.append("quote_error")
        elif quote_is_stale(sell_quote):
            reasons.append("quote_stale")
    else:
        reasons.append("signal_not_executable")

    if futures_signal and not bool(futures_signal.get("ok", False)):
        reasons.append("signal_error")
    risk_result = check_onchain_quote_risk(
        getattr(mapping, "symbol", ""),
        mapping,
        risk_quote,
        risk_direction,
    )
    if action in {"LONG"} or action.startswith("CLOSE"):
        if not risk_result["ok"]:
            reasons.append("risk_failed")

    return {
        "executable": not reasons,
        "reasons": reasons,
        "risk_ok": risk_result["ok"],
        "risk_reason": risk_result["reason"],
        "risk_failures": risk_result["failures"],
        "risk_details": risk_result["details"],
        **session_status,
    }
