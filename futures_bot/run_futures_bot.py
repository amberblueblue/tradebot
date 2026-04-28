from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.secrets import load_futures_binance_readonly_credentials  # noqa: E402
from futures_bot.config_loader import load_futures_config  # noqa: E402
from futures_bot.exchange.binance_futures_client import BinanceFuturesClient  # noqa: E402
from observability.event_logger import StructuredLogger  # noqa: E402


FUTURES_LOG_FILE = "logs/futures.log"


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _is_nonzero_position(position: dict[str, Any]) -> bool:
    return _to_float(position.get("positionAmt")) != 0.0


def _sum_unrealized_pnl(positions: list[dict[str, Any]]) -> float:
    return sum(
        _to_float(position.get("unRealizedProfit", position.get("unrealizedProfit")))
        for position in positions
    )


def _log_startup_sync(**payload: Any) -> None:
    StructuredLogger(FUTURES_LOG_FILE).log(
        action="futures_startup_readonly_sync",
        symbol="-",
        **payload,
    )


def run_startup_readonly_sync(config) -> dict[str, Any]:
    credentials = load_futures_binance_readonly_credentials()
    if not credentials.configured:
        summary = {
            "account_query_ok": False,
            "skipped": True,
            "reason": "futures_api_key_missing",
            "nonzero_positions_count": 0,
            "open_orders_count": 0,
            "total_unrealized_pnl": 0.0,
        }
        print("[futures_startup_sync] skipped: futures_api_key_missing")
        _log_startup_sync(**summary)
        return summary

    client = BinanceFuturesClient(
        base_url=config.futures.base_url,
        timeout=config.futures.request_timeout_seconds,
        credentials=credentials,
        log_file=FUTURES_LOG_FILE,
    )

    try:
        balance_payload = client.get_futures_balance()
        positions_payload = client.get_futures_positions()
        open_orders_payload = client.get_futures_open_orders()

        if not isinstance(balance_payload, list):
            raise RuntimeError("Futures balance response was not a list")
        if not isinstance(positions_payload, list):
            raise RuntimeError("Futures positions response was not a list")
        if not isinstance(open_orders_payload, list):
            raise RuntimeError("Futures open orders response was not a list")

        nonzero_positions = [
            position
            for position in positions_payload
            if isinstance(position, dict) and _is_nonzero_position(position)
        ]
        total_unrealized_pnl = _sum_unrealized_pnl(nonzero_positions)
        summary = {
            "account_query_ok": True,
            "skipped": False,
            "reason": None,
            "nonzero_positions_count": len(nonzero_positions),
            "open_orders_count": len(open_orders_payload),
            "total_unrealized_pnl": total_unrealized_pnl,
        }
        print("[futures_startup_sync] account_query_ok")
        print(f"[futures_startup_sync] nonzero_positions_count={len(nonzero_positions)}")
        print(f"[futures_startup_sync] open_orders_count={len(open_orders_payload)}")
        print(f"[futures_startup_sync] total_unrealized_pnl={total_unrealized_pnl}")
        if nonzero_positions:
            print("[futures_startup_sync] warning: existing_nonzero_positions_readonly_only")
        if open_orders_payload:
            print("[futures_startup_sync] warning: existing_open_orders_readonly_only")
        _log_startup_sync(**summary)
        return summary
    except Exception as exc:
        summary = {
            "account_query_ok": False,
            "skipped": False,
            "reason": str(exc),
            "nonzero_positions_count": 0,
            "open_orders_count": 0,
            "total_unrealized_pnl": 0.0,
        }
        print(f"[futures_startup_sync] account_query_failed: {exc}")
        _log_startup_sync(**summary)
        return summary


def main() -> int:
    config = load_futures_config()
    enabled_symbols = config.enabled_symbols

    print(f"futures settings path: {config.settings_path.resolve()}")
    print(f"futures symbols path: {config.symbols_path.resolve()}")
    print(f"app.mode: {config.app.mode}")
    print(f"enabled futures symbols: {', '.join(enabled_symbols) or '-'}")
    print(f"base_url: {config.futures.base_url}")
    print("stage: public-data-only / no trading")
    run_startup_readonly_sync(config)

    if not enabled_symbols:
        print("[futures_idle] no_enabled_symbols")
        return 0

    print("[futures_idle] trading_disabled")
    return 0


if __name__ == "__main__":
    sys.exit(main())
