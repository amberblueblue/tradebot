from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from onchain_bot.config_loader import load_onchain_settings_config, load_onchain_symbols_config  # noqa: E402
from onchain_bot.live_executor import prepare_live_swap  # noqa: E402
from onchain_bot.paper_state import load_paper_state  # noqa: E402
from onchain_bot.signal_reader import read_signal_for_mapping  # noqa: E402


def _signal_action(signal: dict[str, Any] | None) -> str:
    if not signal:
        return "error"
    return str(signal.get("action") or "error")


def _skip(symbol: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {
        "ok": False,
        "symbol": symbol,
        "action": "skipped",
        "reason": reason,
        **extra,
    }


def run_onchain_live_once() -> dict[str, Any]:
    settings = load_onchain_settings_config()
    symbols = load_onchain_symbols_config()
    state = load_paper_state()
    positions = state.get("positions", {})
    if not isinstance(positions, dict):
        positions = {}

    actions: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for symbol, mapping in symbols.items():
        try:
            if not mapping.enabled:
                actions.append(_skip(symbol, "mapping_disabled"))
                continue
            futures_signal = read_signal_for_mapping(mapping)
            action = _signal_action(futures_signal)
            if action == "LONG":
                preview = prepare_live_swap(symbol, "buy", settings.live_default_order_amount_usdc)
                actions.append(
                    {
                        "ok": bool(preview.get("ok")),
                        "symbol": symbol,
                        "action": "auto_live_check_buy",
                        "reason": "ok" if preview.get("ok") else "live_check_failed",
                        "failures": preview.get("failures", []),
                        "tx_preview": preview.get("tx_preview"),
                        "preview": preview,
                    }
                )
                continue
            if action.startswith("CLOSE"):
                position = positions.get(symbol)
                if not isinstance(position, dict):
                    actions.append(_skip(symbol, "position_not_found", signal_action=action))
                    continue
                token_amount = position.get("entry_token_amount")
                if not token_amount:
                    actions.append(_skip(symbol, "missing_position_token_amount", signal_action=action))
                    continue
                preview = prepare_live_swap(symbol, "sell", token_amount)
                actions.append(
                    {
                        "ok": bool(preview.get("ok")),
                        "symbol": symbol,
                        "action": "auto_live_check_sell",
                        "reason": "ok" if preview.get("ok") else "live_check_failed",
                        "failures": preview.get("failures", []),
                        "tx_preview": preview.get("tx_preview"),
                        "preview": preview,
                    }
                )
                continue
            if action == "HOLD":
                actions.append(_skip(symbol, "signal_hold", signal_action=action))
                continue
            actions.append(_skip(symbol, "signal_not_executable", signal_action=action))
        except Exception as exc:
            errors.append({"symbol": symbol, "error": str(exc)})
            actions.append(_skip(symbol, "run_error", error=str(exc)))

    return {
        "ok": not errors,
        "mode": "auto_live_check_only",
        "auto_live_enabled": settings.live_auto_live_enabled,
        "default_order_amount_usdc": settings.live_default_order_amount_usdc,
        "default_order_amount_usdt": settings.live_default_order_amount_usdc,
        "actions": actions,
        "errors": errors,
        "signing": "not_implemented",
        "broadcast": "not_implemented",
    }


def main() -> int:
    print(json.dumps(run_onchain_live_once(), indent=2, ensure_ascii=False, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
