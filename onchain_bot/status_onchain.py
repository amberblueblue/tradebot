from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from onchain_bot.config_loader import load_onchain_symbols_config, onchain_symbols_payload  # noqa: E402
from onchain_bot.executable_check import check_onchain_executable, quote_is_stale  # noqa: E402
from onchain_bot.okx_dex_client import OkxDexQuoteClient  # noqa: E402
from onchain_bot.quote_parser import parse_okx_quote  # noqa: E402
from onchain_bot.quote_cache import get_cached_quote, load_quote_cache  # noqa: E402
from onchain_bot.signal_reader import read_signal_for_mapping  # noqa: E402


SUPPORTED_QUOTE_TOKENS = {"USDC", "USDT"}


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Show Onchain Bot read-only status.")
    parser.add_argument(
        "--symbols",
        action="store_true",
        help="Show configured onchain symbol mappings.",
    )
    parser.add_argument(
        "--quote",
        metavar="SYMBOL",
        help="Run an OKX DEX quote-only query for a mapped symbol.",
    )
    parser.add_argument(
        "--readiness",
        action="store_true",
        help="Show onchain mapping readiness with cached Futures signals.",
    )
    parser.add_argument(
        "--quote-cache",
        action="store_true",
        help="Show cached onchain quote results.",
    )
    parser.add_argument(
        "--amount-usdt",
        type=str,
        help="Quote token amount, currently intended for USDC/USDT stablecoin quotes.",
    )
    return parser.parse_args(argv)


def _parse_amount(amount_usdt: str) -> Decimal:
    try:
        amount = Decimal(amount_usdt)
    except InvalidOperation as exc:
        raise ValueError("--amount-usdt must be a number greater than 0") from exc
    if amount <= 0:
        raise ValueError("--amount-usdt must be greater than 0")
    return amount


def _amount_to_base_units(amount_usdt: str, decimals: int) -> int:
    amount = _parse_amount(amount_usdt)
    scale = Decimal(10) ** decimals
    return int(amount * scale)


def build_quote_payload(symbol: str, amount_usdt: str) -> dict[str, Any]:
    normalized_symbol = symbol.strip().upper()
    symbols = load_onchain_symbols_config()
    symbol_config = symbols.get(normalized_symbol)
    if symbol_config is None:
        return {
            "ok": False,
            "symbol": normalized_symbol,
            "chain_id": None,
            "token_symbol": None,
            "token_address": None,
            "quote_token_symbol": None,
            "quote_token_address": None,
            "amount_usdt": amount_usdt,
            "quote": None,
            "error": "onchain_symbol_not_configured",
        }

    amount_value = float(_parse_amount(amount_usdt))
    if symbol_config.quote_token_symbol.upper() not in SUPPORTED_QUOTE_TOKENS:
        return {
            "ok": False,
            "symbol": normalized_symbol,
            "chain_id": symbol_config.chain_id,
            "token_symbol": symbol_config.token_symbol,
            "token_address": symbol_config.token_address,
            "quote_token_symbol": symbol_config.quote_token_symbol,
            "quote_token_address": symbol_config.quote_token_address,
            "amount_usdt": amount_value,
            "quote": None,
            "error": "unsupported_quote_token",
            "message": "Current quote-only mode supports USDC/USDT quote tokens only.",
        }

    from_token_amount = _amount_to_base_units(amount_usdt, symbol_config.quote_token_decimals)
    quote_result = OkxDexQuoteClient().quote(
        chain_id=symbol_config.chain_id,
        from_token_address=symbol_config.quote_token_address,
        to_token_address=symbol_config.token_address,
        amount=from_token_amount,
    )
    parsed_quote = parse_okx_quote(
        raw_quote=quote_result.get("quote"),
        amount_usdt=amount_value,
        quote_token_symbol=symbol_config.quote_token_symbol,
        quote_token_decimals=symbol_config.quote_token_decimals,
        token_symbol=symbol_config.token_symbol,
        token_decimals=symbol_config.token_decimals,
        max_slippage_pct=symbol_config.max_slippage_pct,
        latency_ms=quote_result.get("latency_ms"),
    )
    return {
        "ok": bool(quote_result.get("ok")),
        "symbol": normalized_symbol,
        "chain_id": symbol_config.chain_id,
        "token_symbol": symbol_config.token_symbol,
        "token_address": symbol_config.token_address,
        "quote_token_symbol": symbol_config.quote_token_symbol,
        "quote_token_address": symbol_config.quote_token_address,
        "amount_usdt": amount_value,
        "from_token_amount": str(from_token_amount),
        "quote": quote_result.get("quote"),
        "parsed_quote": parsed_quote,
        "endpoint": quote_result.get("endpoint"),
        "status_code": quote_result.get("status_code"),
        "http_status": quote_result.get("http_status"),
        "request_url": quote_result.get("request_url"),
        "request_headers_present": quote_result.get("request_headers_present"),
        "timestamp": quote_result.get("timestamp"),
        "response_body": quote_result.get("response_body"),
        "diagnostics": quote_result.get("diagnostics"),
        "latency_ms": quote_result.get("latency_ms"),
        "error": quote_result.get("error"),
        "message": quote_result.get("message"),
        "quote_only": True,
    }


def build_readiness_payload() -> dict[str, Any]:
    symbols = load_onchain_symbols_config()
    items = []
    for symbol, symbol_config in symbols.items():
        futures_signal = read_signal_for_mapping(symbol_config)
        cached_quote = get_cached_quote(symbol)
        cached_quote_error = cached_quote.get("error") if cached_quote else None
        cached_quote_stale = quote_is_stale(cached_quote)
        executable_check = check_onchain_executable(
            mapping=symbol_config,
            futures_signal=futures_signal,
            quote_result=cached_quote,
        )
        items.append(
            {
                "symbol": symbol,
                "enabled": symbol_config.enabled,
                "source_symbol": symbol_config.source_symbol,
                "signal_source": symbol_config.signal_source,
                "token_symbol": symbol_config.token_symbol,
                "token_address": symbol_config.token_address,
                "quote_token_symbol": symbol_config.quote_token_symbol,
                "quote_token_address": symbol_config.quote_token_address,
                "quote_status": "not_tested" if cached_quote is None else "ok" if cached_quote.get("ok") else "error",
                "cached_quote_ok": cached_quote.get("ok") if cached_quote else None,
                "cached_quote_time": cached_quote.get("quoted_at") if cached_quote else None,
                "cached_quote_error": cached_quote_error,
                "cached_quote_amount_usdt": cached_quote.get("amount_usdt") if cached_quote else None,
                "quote_stale": cached_quote_stale,
                "futures_signal": futures_signal,
                "executable": executable_check["executable"],
                "reasons": executable_check["reasons"],
            }
        )
    return {
        "symbols_count": len(symbols),
        "items": items,
    }


def main() -> int:
    args = parse_args(sys.argv[1:])
    selected_modes = sum(bool(mode) for mode in (args.symbols, args.quote, args.readiness, args.quote_cache))
    if selected_modes == 0:
        print(
            json.dumps(
                {
                    "error": "missing_mode",
                    "message": "use --symbols, --quote, --readiness, or --quote-cache",
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 1
    if selected_modes > 1:
        print(json.dumps({"error": "invalid_mode", "message": "use only one mode"}, indent=2, sort_keys=True))
        return 1
    if args.quote and args.amount_usdt is None:
        print(json.dumps({"error": "missing_amount", "message": "--quote requires --amount-usdt"}, indent=2, sort_keys=True))
        return 1

    try:
        if args.symbols:
            payload = onchain_symbols_payload()
        elif args.readiness:
            payload = build_readiness_payload()
        elif args.quote_cache:
            payload = load_quote_cache()
        else:
            payload = build_quote_payload(args.quote, args.amount_usdt)
    except Exception as exc:
        print(json.dumps({"error": "onchain_config_error", "message": str(exc)}, indent=2, sort_keys=True))
        return 1

    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
