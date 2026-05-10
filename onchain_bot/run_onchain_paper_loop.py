from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from onchain_bot.config_loader import load_onchain_settings_config, load_onchain_symbols_config  # noqa: E402
from onchain_bot.executable_check import quote_is_stale  # noqa: E402
from onchain_bot.paper_state import load_paper_state  # noqa: E402
from onchain_bot.quote_cache import get_cached_quote, update_quote_cache  # noqa: E402
from onchain_bot.run_onchain_paper_once import run_once  # noqa: E402
from onchain_bot.session_filter import get_execution_session_status  # noqa: E402
from onchain_bot.signal_reader import read_signal_for_mapping  # noqa: E402
from onchain_bot.status_onchain import build_quote_payload  # noqa: E402
from runtime.safety import check_onchain_paper_allowed  # noqa: E402


LOG_PREFIX = "[onchain_paper_loop]"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Onchain paper trading loop.")
    parser.add_argument(
        "--max-loops",
        type=int,
        default=None,
        help="Optional loop limit for manual verification. Default runs forever.",
    )
    return parser.parse_args(argv)


def _signal_action(signal: dict[str, Any] | None) -> str:
    if not signal:
        return "error"
    action = signal.get("action")
    return str(action) if action is not None else "error"


def _quote_status(cached_quote: dict[str, Any] | None, *, stale_seconds: int) -> str:
    if cached_quote is None:
        return "not_tested"
    if not bool(cached_quote.get("ok")):
        return "error"
    if quote_is_stale(cached_quote, ttl_seconds=stale_seconds):
        return "stale"
    return "ok"


def _refresh_quote_if_needed(
    *,
    symbol: str,
    signal_action: str,
    stale_seconds: int,
    default_amount_usdt: float,
    auto_refresh_enabled: bool,
    amount_display: float | int | str | None = None,
    direction: str = "buy",
) -> dict[str, Any]:
    normalized_direction = direction.strip().lower()
    cached_quote = get_cached_quote(symbol, normalized_direction)
    status = _quote_status(cached_quote, stale_seconds=stale_seconds)
    if (
        not auto_refresh_enabled
        or (signal_action != "LONG" and not signal_action.startswith("CLOSE"))
        or status == "ok"
    ):
        return {
            "quote_status": status,
            "quote_refreshed": False,
            "quote_refresh_error": None,
        }

    quote_amount = amount_display if amount_display is not None else default_amount_usdt
    quote_result = build_quote_payload(symbol, str(quote_amount), direction=normalized_direction)
    cached_quote = update_quote_cache(symbol, quote_result, direction=normalized_direction)
    if not bool(cached_quote.get("ok")):
        return {
            "quote_status": "error",
            "quote_refreshed": True,
            "quote_refresh_error": cached_quote.get("error") or "quote_refresh_failed",
        }
    return {
        "quote_status": "ok",
        "quote_refreshed": True,
        "quote_refresh_error": None,
    }


def run_loop_iteration() -> dict[str, Any]:
    settings = load_onchain_settings_config()
    if settings.app_mode != "paper":
        return {
            "ok": False,
            "reason": "onchain_live_not_supported_yet",
            "app_mode": settings.app_mode,
            "actions": [],
        }
    if settings.safety_allow_live_trading or settings.safety_live_execute_enabled:
        return {
            "ok": False,
            "reason": "onchain_live_not_supported_yet",
            "app_mode": settings.app_mode,
            "actions": [],
        }
    safety_decision = check_onchain_paper_allowed()
    if not safety_decision.allowed:
        symbols = load_onchain_symbols_config()
        paper_state = load_paper_state()
        positions = paper_state.get("positions", {})
        closed_trades = paper_state.get("closed_trades", [])
        actions = [
            {
                "ok": False,
                "action": "skipped",
                "symbol": symbol,
                "reason": "onchain_safety_blocked",
                "safety_reason": safety_decision.reason,
            }
            for symbol in symbols
        ]
        return {
            "ok": True,
            "loop_started": datetime.now(timezone.utc).isoformat(),
            "enabled_symbols_count": len([mapping for mapping in symbols.values() if mapping.enabled]),
            "symbols": [
                {
                    "symbol": action["symbol"],
                    "futures_signal": None,
                    "quote_status": "not_refreshed",
                    "quote_refreshed": False,
                    "quote_refresh_error": None,
                    "action_taken": "skipped",
                    "reason": "onchain_safety_blocked",
                    "ok": False,
                }
                for action in actions
            ],
            "actions": actions,
            "positions_count": len(positions) if isinstance(positions, dict) else 0,
            "closed_trades_count": len(closed_trades) if isinstance(closed_trades, list) else 0,
            "errors": [],
            "polling_interval_seconds": settings.polling_interval_seconds,
        }

    symbols = load_onchain_symbols_config()
    paper_state = load_paper_state()
    positions = paper_state.get("positions", {})
    if not isinstance(positions, dict):
        positions = {}
    enabled_symbols = {
        symbol: mapping
        for symbol, mapping in symbols.items()
        if mapping.enabled
    }
    preflight: dict[str, dict[str, Any]] = {}
    for symbol, mapping in enabled_symbols.items():
        session_status = get_execution_session_status(mapping.execution_session_filter)
        if not session_status["session_allowed"]:
            preflight[symbol] = {
                "symbol": symbol,
                "futures_signal": None,
                "quote_status": "not_refreshed",
                "quote_refreshed": False,
                "quote_refresh_error": None,
                "execution_session_filter": session_status["execution_session_filter"],
                "session_allowed": session_status["session_allowed"],
                "session_name": session_status["session_name"],
                "session_time_now": session_status["session_time_now"],
            }
            continue
        futures_signal = read_signal_for_mapping(mapping)
        action = _signal_action(futures_signal)
        direction = "sell" if action.startswith("CLOSE") else "buy"
        amount_display = None
        if direction == "sell":
            position = positions.get(symbol)
            if isinstance(position, dict):
                amount_display = position.get("entry_token_amount")
            else:
                amount_display = None
        quote_info = _refresh_quote_if_needed(
            symbol=symbol,
            signal_action=action,
            stale_seconds=settings.quote_stale_seconds,
            default_amount_usdt=settings.quote_default_amount_usdt,
            auto_refresh_enabled=settings.quote_auto_refresh_enabled and (direction == "buy" or amount_display is not None),
            amount_display=amount_display,
            direction=direction,
        )
        preflight[symbol] = {
            "symbol": symbol,
            "futures_signal": action,
            "execution_session_filter": session_status["execution_session_filter"],
            "session_allowed": session_status["session_allowed"],
            "session_name": session_status["session_name"],
            "session_time_now": session_status["session_time_now"],
            **quote_info,
        }

    run_result = run_once()
    actions_by_symbol = {
        str(action.get("symbol")): action
        for action in run_result.get("actions", [])
        if isinstance(action, dict)
    }
    symbol_summaries = []
    for symbol, info in preflight.items():
        action_result = actions_by_symbol.get(symbol, {})
        reason = action_result.get("reason")
        if not info.get("session_allowed", True):
            reason = "outside_us_regular_session"
        if info.get("quote_refresh_error"):
            reason = "quote_refresh_failed"
        symbol_summaries.append(
            {
                **info,
                "action_taken": action_result.get("action", "skipped"),
                "reason": reason,
                "ok": action_result.get("ok", False),
            }
        )

    return {
        "ok": True,
        "loop_started": datetime.now(timezone.utc).isoformat(),
        "enabled_symbols_count": len(enabled_symbols),
        "symbols": symbol_summaries,
        "actions": run_result.get("actions", []),
        "positions_count": run_result.get("positions_count", 0),
        "closed_trades_count": run_result.get("closed_trades_count", 0),
        "errors": run_result.get("errors", []),
        "polling_interval_seconds": settings.polling_interval_seconds,
    }


def _print_summary(payload: dict[str, Any]) -> None:
    print(f"{LOG_PREFIX} {json.dumps(payload, ensure_ascii=False, sort_keys=True)}", flush=True)


def main() -> int:
    args = parse_args(sys.argv[1:])
    loop_count = 0
    while True:
        payload = run_loop_iteration()
        _print_summary(payload)
        if not payload.get("ok"):
            return 1
        loop_count += 1
        if args.max_loops is not None and loop_count >= args.max_loops:
            return 0
        time.sleep(int(payload.get("polling_interval_seconds") or 60))


if __name__ == "__main__":
    raise SystemExit(main())
