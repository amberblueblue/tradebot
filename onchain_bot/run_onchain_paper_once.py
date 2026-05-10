from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from onchain_bot.config_loader import load_onchain_symbols_config  # noqa: E402
from onchain_bot.executable_check import check_onchain_executable  # noqa: E402
from onchain_bot.paper_broker import (  # noqa: E402
    close_paper_position,
    load_paper_state,
    open_paper_position,
)
from onchain_bot.quote_cache import get_cached_quote  # noqa: E402
from onchain_bot.signal_reader import read_signal_for_mapping  # noqa: E402


def _signal_action(signal: dict[str, Any] | None) -> str:
    if not signal:
        return "error"
    action = signal.get("action")
    return str(action) if action is not None else "error"


def _has_position(symbol: str) -> bool:
    state = load_paper_state()
    return symbol.strip().upper() in state.get("positions", {})


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
            cached_quote = get_cached_quote(symbol)
            readiness = check_onchain_executable(
                mapping=mapping,
                futures_signal=futures_signal,
                quote_result=cached_quote,
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
                if cached_quote is None or not bool(cached_quote.get("ok")):
                    actions.append(_skip_action(symbol, "quote_not_ok", signal_action=signal_action))
                    continue
                actions.append(open_paper_position(symbol, mapping, cached_quote))
                continue

            if signal_action.startswith("CLOSE"):
                if not has_position:
                    actions.append(_skip_action(symbol, "position_not_found", signal_action=signal_action))
                    continue
                if cached_quote is None or not bool(cached_quote.get("ok")):
                    actions.append(_skip_action(symbol, "quote_not_ok", signal_action=signal_action))
                    continue
                actions.append(close_paper_position(symbol, cached_quote))
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
