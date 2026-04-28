from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.secrets import load_futures_binance_readonly_credentials  # noqa: E402
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


def _is_missing_key_payload(payload: Any) -> bool:
    return isinstance(payload, dict) and payload.get("error") == "futures_api_key_missing"


def build_account_status_payload() -> dict[str, object]:
    credentials = load_futures_binance_readonly_credentials()
    credential_status = credentials.public_status()
    if not credentials.configured:
        return {
            "api_key_configured": credentials.api_key_configured,
            "api_secret_configured": credentials.api_secret_configured,
            "account_query_ok": False,
            "error": "Futures API key missing",
        }

    try:
        payload = BinanceFuturesClient(credentials=credentials).get_futures_balance()
    except Exception as exc:
        return {
            "api_key_configured": credentials.api_key_configured,
            "api_secret_configured": credentials.api_secret_configured,
            "account_query_ok": False,
            "error": str(exc),
        }

    if _is_missing_key_payload(payload):
        return {
            "api_key_configured": bool(credential_status["api_key_configured"]),
            "api_secret_configured": bool(credential_status["api_secret_configured"]),
            "account_query_ok": False,
            "error": payload.get("message", "Futures API key missing"),
        }

    return {
        "api_key_configured": credentials.api_key_configured,
        "api_secret_configured": credentials.api_secret_configured,
        "account_query_ok": True,
        "error": None,
    }


def _balance_row(balance: dict[str, Any]) -> dict[str, Any]:
    return {
        "asset": balance.get("asset"),
        "walletBalance": balance.get("walletBalance"),
        "availableBalance": balance.get("availableBalance"),
        "marginBalance": balance.get("marginBalance"),
        "unrealizedProfit": balance.get("unrealizedProfit"),
    }


def build_balance_payload() -> dict[str, object]:
    payload = BinanceFuturesClient().get_futures_balance()
    if _is_missing_key_payload(payload):
        return {
            "balance": [],
            "query_ok": False,
            "error": payload.get("error"),
            "message": payload.get("message"),
        }
    if not isinstance(payload, list):
        return {
            "balance": [],
            "query_ok": False,
            "error": "unexpected_futures_balance_payload",
            "message": "Futures balance response was not a list",
        }
    return {
        "balance": [_balance_row(item) for item in payload if isinstance(item, dict)],
        "query_ok": True,
        "error": None,
    }


def _position_is_nonzero(position: dict[str, Any]) -> bool:
    return (_float_or_none(position.get("positionAmt")) or 0.0) != 0.0


def _position_row(position: dict[str, Any]) -> dict[str, Any]:
    return {
        "symbol": position.get("symbol"),
        "positionAmt": position.get("positionAmt"),
        "entryPrice": position.get("entryPrice"),
        "markPrice": position.get("markPrice"),
        "unrealizedProfit": position.get("unRealizedProfit", position.get("unrealizedProfit")),
        "liquidationPrice": position.get("liquidationPrice"),
        "leverage": position.get("leverage"),
        "marginType": position.get("marginType"),
        "positionSide": position.get("positionSide"),
    }


def build_positions_payload() -> dict[str, object]:
    payload = BinanceFuturesClient().get_futures_positions()
    if _is_missing_key_payload(payload):
        return {
            "positions": [],
            "query_ok": False,
            "error": payload.get("error"),
            "message": payload.get("message"),
        }
    if not isinstance(payload, list):
        return {
            "positions": [],
            "query_ok": False,
            "error": "unexpected_futures_positions_payload",
            "message": "Futures positions response was not a list",
        }
    return {
        "positions": [
            _position_row(item)
            for item in payload
            if isinstance(item, dict) and _position_is_nonzero(item)
        ],
        "query_ok": True,
        "error": None,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Show Futures Bot status.")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--account-status",
        action="store_true",
        help="Show Futures read-only API key status and account query health.",
    )
    mode_group.add_argument(
        "--balance",
        action="store_true",
        help="Show Futures read-only balances.",
    )
    mode_group.add_argument(
        "--positions",
        action="store_true",
        help="Show non-zero Futures read-only positions.",
    )
    mode_group.add_argument(
        "--market-data",
        metavar="SYMBOL",
        help="Fetch public Binance USD-M Futures market data for a symbol.",
    )
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args(sys.argv[1:])
    try:
        if args.account_status:
            payload = build_account_status_payload()
        elif args.balance:
            payload = build_balance_payload()
        elif args.positions:
            payload = build_positions_payload()
        elif args.market_data:
            payload = build_market_data_payload(args.market_data)
        else:
            payload = build_status_payload()
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
    if "error" in payload and payload.get("error") not in {
        None,
        "futures_api_key_missing",
        "Futures API key missing",
    }:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
