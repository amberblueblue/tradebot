from __future__ import annotations

from typing import Any


ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


def _signal_action(signal: dict[str, Any] | None) -> str:
    if not signal:
        return "error"
    action = signal.get("action")
    return str(action) if action is not None else "error"


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
    elif action != "LONG" and not action.startswith("CLOSE"):
        reasons.append("signal_not_executable")

    if futures_signal and not bool(futures_signal.get("ok", False)):
        reasons.append("signal_error")

    if quote_result is not None and not bool(quote_result.get("ok", False)):
        reasons.append("quote_error")

    return {
        "executable": not reasons,
        "reasons": reasons,
    }
