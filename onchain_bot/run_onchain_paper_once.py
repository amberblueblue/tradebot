from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from onchain_bot.config_loader import load_onchain_symbols_config  # noqa: E402
from onchain_bot.executable_check import check_onchain_executable, quote_is_stale  # noqa: E402
from onchain_bot.paper_broker import (  # noqa: E402
    close_paper_position,
    load_paper_state,
    open_paper_position,
)
from onchain_bot.quote_cache import get_cached_quote, update_quote_cache  # noqa: E402
from onchain_bot.signal_reader import read_signal_for_mapping  # noqa: E402
from onchain_bot.status_onchain import build_quote_payload  # noqa: E402


def _signal_action(signal: dict[str, Any] | None) -> str:
    if not signal:
        return "error"
    action = signal.get("action")
    return str(action) if action is not None else "error"


def _has_position(symbol: str) -> bool:
    state = load_paper_state()
    return symbol.strip().upper() in state.get("positions", {})


def _position(symbol: str) -> dict[str, Any] | None:
    state = load_paper_state()
    position = state.get("positions", {}).get(symbol.strip().upper())
    return position if isinstance(position, dict) else None


def _skip_action(symbol: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {
        "ok": False,
        "action": "skipped",
        "symbol": symbol,
        "reason": reason,
        **extra,
    }


def run_once() -> dict[str, Any]:
    symbols = load_onchain_symbols_config()
    actions: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for symbol, mapping in symbols.items():
        try:
            if not mapping.enabled:
                actions.append(_skip_action(symbol, "mapping_disabled"))
                continue

            futures_signal = read_signal_for_mapping(mapping)
            signal_action = _signal_action(futures_signal)
            cached_buy_quote = get_cached_quote(symbol, "buy")
            cached_sell_quote = get_cached_quote(symbol, "sell")
            readiness_quote = cached_sell_quote if signal_action.startswith("CLOSE") else cached_buy_quote
            readiness = check_onchain_executable(
                mapping=mapping,
                futures_signal=futures_signal,
                quote_result=readiness_quote,
                buy_quote_result=cached_buy_quote,
                sell_quote_result=cached_sell_quote,
            )
            has_position = _has_position(symbol)

            if signal_action == "LONG":
                if has_position:
                    actions.append(_skip_action(symbol, "position_exists", signal_action=signal_action))
                    continue
                if not readiness.get("executable"):
                    actions.append(
                        _skip_action(
                            symbol,
                            "not_executable",
                            signal_action=signal_action,
                            readiness_reasons=readiness.get("reasons", []),
                        )
                    )
                    continue
                if cached_buy_quote is None or not bool(cached_buy_quote.get("ok")):
                    actions.append(_skip_action(symbol, "quote_not_ok", signal_action=signal_action))
                    continue
                actions.append(open_paper_position(symbol, mapping, cached_buy_quote))
                continue

            if signal_action.startswith("CLOSE"):
                if not has_position:
                    actions.append(_skip_action(symbol, "position_not_found", signal_action=signal_action))
                    continue
                if cached_sell_quote is None or not bool(cached_sell_quote.get("ok")) or quote_is_stale(cached_sell_quote):
                    position = _position(symbol)
                    entry_token_amount = position.get("entry_token_amount") if position else None
                    if entry_token_amount:
                        sell_quote = build_quote_payload(symbol, str(entry_token_amount), direction="sell")
                        cached_sell_quote = update_quote_cache(symbol, sell_quote, direction="sell")
                if cached_sell_quote is None or not bool(cached_sell_quote.get("ok")):
                    actions.append(_skip_action(symbol, "sell_quote_not_available", signal_action=signal_action))
                    continue
                actions.append(close_paper_position(symbol, cached_sell_quote))
                continue

            if signal_action == "HOLD":
                actions.append(_skip_action(symbol, "signal_hold", signal_action=signal_action))
                continue

            actions.append(
                _skip_action(
                    symbol,
                    "signal_not_executable",
                    signal_action=signal_action,
                    signal_error=futures_signal.get("error") if futures_signal else None,
                )
            )
        except Exception as exc:
            error = {
                "symbol": symbol,
                "error": str(exc),
            }
            errors.append(error)
            actions.append(_skip_action(symbol, "run_error", error=str(exc)))

    state = load_paper_state()
    return {
        "actions": actions,
        "positions_count": len(state.get("positions", {})),
        "closed_trades_count": len(state.get("closed_trades", [])),
        "errors": errors,
    }


def main() -> int:
    print(json.dumps(run_once(), indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
