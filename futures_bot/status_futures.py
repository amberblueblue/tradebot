from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from futures_bot.config_loader import load_futures_config  # noqa: E402
from futures_bot.exchange.binance_futures_client import BinanceFuturesClient  # noqa: E402
from futures_bot.exchange.futures_rules import parse_futures_symbol_rules  # noqa: E402


def build_status_payload() -> dict[str, object]:
    config = load_futures_config()
    enabled_symbols = list(config.enabled_symbols)

    return {
        "mode": config.app.mode,
        "base_url": config.futures.base_url,
        "enabled_symbols": enabled_symbols,
        "symbols_count": len(enabled_symbols),
        "live_allowed": config.safety.allow_live_trading,
        "public_data_only": True,
    }


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_market_data_payload(symbol: str) -> dict[str, object]:
    symbol = symbol.upper()
    client = BinanceFuturesClient()

    try:
        symbol_info = client.get_symbol_info(symbol)
        rules = parse_futures_symbol_rules(symbol_info)
        ticker_payload = client.get_ticker_price(symbol)
        mark_payload = client.get_mark_price(symbol)
    except Exception as exc:
        return {
            "symbol": symbol,
            "error": "futures_public_market_data_error",
            "message": str(exc),
            "public_api_ok": False,
        }

    funding_rate = None
    funding_rate_error = None
    try:
        funding_payload = client.get_funding_rate(symbol, limit=1)
        if isinstance(funding_payload, list) and funding_payload:
            funding_rate = _float_or_none(funding_payload[0].get("fundingRate"))
    except Exception as exc:
        funding_rate_error = str(exc)

    payload: dict[str, object] = {
        "symbol": symbol,
        "ticker_price": _float_or_none(ticker_payload.get("price")),
        "mark_price": _float_or_none(mark_payload.get("markPrice")),
        "funding_rate": funding_rate,
        "next_funding_time": mark_payload.get("nextFundingTime"),
        "rules": {
            "tick_size": rules.tick_size,
            "step_size": rules.step_size,
            "min_qty": rules.min_qty,
            "min_notional": rules.min_notional,
        },
        "public_api_ok": True,
    }
    if funding_rate_error is not None:
        payload["funding_rate_error"] = funding_rate_error
    return payload


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Show Futures Bot status.")
    parser.add_argument(
        "--market-data",
        metavar="SYMBOL",
        help="Fetch public Binance USD-M Futures market data for a symbol.",
    )
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args(sys.argv[1:])
    try:
        payload = (
            build_market_data_payload(args.market_data)
            if args.market_data
            else build_status_payload()
        )
    except Exception as exc:
        payload = {
            "error": "futures_config_error",
            "message": str(exc),
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 1

    print(json.dumps(payload, indent=2, sort_keys=True))
    if payload.get("public_api_ok") is False:
        return 1
    if "error" in payload:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
