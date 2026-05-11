from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from onchain_bot.config_loader import load_onchain_symbols_config
from onchain_bot.executable_check import check_onchain_executable
from onchain_bot.okx_dex_client import OkxDexQuoteClient
from onchain_bot.paper_state import load_paper_state
from onchain_bot.risk import check_onchain_quote_risk
from onchain_bot.session_filter import get_execution_session_status
from onchain_bot.signal_reader import read_signal_for_mapping
from onchain_bot.trade_limits import check_onchain_trade_limits
from runtime.safety import load_runtime_safety_config


SUPPORTED_QUOTE_TOKENS = {"USDC", "USDT"}
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


def _parse_amount(value: str | int | float | Decimal) -> Decimal:
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("amount must be a number greater than 0") from exc
    if amount <= 0:
        raise ValueError("amount must be greater than 0")
    return amount


def _address_missing(value: str | None) -> bool:
    return not value or value.lower() == ZERO_ADDRESS


def _quote_for_preview(symbol_config: Any, direction: str, amount: str) -> dict[str, Any]:
    normalized_direction = direction.strip().lower()
    if symbol_config.quote_token_symbol.upper() not in SUPPORTED_QUOTE_TOKENS:
        return {
            "ok": False,
            "direction": normalized_direction,
            "symbol": symbol_config.symbol,
            "chain_id": symbol_config.chain_id,
            "token_symbol": symbol_config.token_symbol,
            "token_address": symbol_config.token_address,
            "quote_token_symbol": symbol_config.quote_token_symbol,
            "quote_token_address": symbol_config.quote_token_address,
            "amount_usdc": float(_parse_amount(amount)),
            "amount_display": float(_parse_amount(amount)),
            "quote": None,
            "parsed_quote": {},
            "error": "unsupported_quote_token",
            "message": "Current preview expects USDC as the default quote token.",
            "quote_only": True,
        }

    if normalized_direction == "buy":
        from_token_address = symbol_config.quote_token_address
        to_token_address = symbol_config.token_address
        from_token_decimals = symbol_config.quote_token_decimals
        to_token_decimals = symbol_config.token_decimals
        from_token_symbol = symbol_config.quote_token_symbol
        to_token_symbol = symbol_config.token_symbol
    else:
        from_token_address = symbol_config.token_address
        to_token_address = symbol_config.quote_token_address
        from_token_decimals = symbol_config.token_decimals
        to_token_decimals = symbol_config.quote_token_decimals
        from_token_symbol = symbol_config.token_symbol
        to_token_symbol = symbol_config.quote_token_symbol

    quote_result = OkxDexQuoteClient().get_quote(
        chain_id=symbol_config.chain_id,
        from_token_address=from_token_address,
        to_token_address=to_token_address,
        from_token_decimals=from_token_decimals,
        to_token_decimals=to_token_decimals,
        amount_display=amount,
        slippage_pct=symbol_config.max_slippage_pct,
        direction=normalized_direction,
        from_token_symbol=from_token_symbol,
        to_token_symbol=to_token_symbol,
    )
    quoted_at = datetime.now(timezone.utc).isoformat()
    return {
        "ok": bool(quote_result.get("ok")),
        "direction": normalized_direction,
        "symbol": symbol_config.symbol,
        "chain_id": symbol_config.chain_id,
        "token_symbol": symbol_config.token_symbol,
        "token_address": symbol_config.token_address,
        "quote_token_symbol": symbol_config.quote_token_symbol,
        "quote_token_address": symbol_config.quote_token_address,
        "amount_usdc": float(_parse_amount(amount)),
        "amount_display": float(_parse_amount(amount)),
        "from_token_symbol": from_token_symbol,
        "to_token_symbol": to_token_symbol,
        "from_token_address": from_token_address,
        "to_token_address": to_token_address,
        "from_token_amount": quote_result.get("from_token_amount"),
        "from_amount_display": quote_result.get("from_amount_display"),
        "to_amount_display": quote_result.get("to_amount_display"),
        "implied_price": quote_result.get("implied_price"),
        "price_impact_pct": quote_result.get("price_impact_pct"),
        "route": quote_result.get("route"),
        "quote": quote_result.get("quote"),
        "parsed_quote": quote_result.get("parsed_quote"),
        "endpoint": quote_result.get("endpoint"),
        "status_code": quote_result.get("status_code"),
        "http_status": quote_result.get("http_status"),
        "latency_ms": quote_result.get("latency_ms"),
        "error": quote_result.get("error"),
        "message": quote_result.get("message"),
        "quoted_at": quoted_at,
        "quote_only": True,
    }


def _safety_payload() -> dict[str, Any]:
    config = load_runtime_safety_config()
    failures: list[str] = []
    warnings: list[str] = []
    if config.global_kill_switch:
        failures.append("global_kill_switch_enabled")
    if config.onchain_kill_switch:
        failures.append("onchain_kill_switch_enabled")
    if not config.onchain_paper_enabled:
        warnings.append("onchain_paper_disabled")
    if config.onchain_trading_enabled:
        failures.append("onchain_trading_enabled_true")
    else:
        warnings.append("live_trading_not_enabled_yet")
    return {
        "ok": not failures,
        "global_kill_switch": config.global_kill_switch,
        "onchain_paper_enabled": config.onchain_paper_enabled,
        "onchain_trading_enabled": config.onchain_trading_enabled,
        "onchain_kill_switch": config.onchain_kill_switch,
        "failures": failures,
        "warnings": warnings,
    }


def build_live_swap_preview(symbol: str, direction: str, amount: str | int | float | Decimal) -> dict[str, Any]:
    normalized_symbol = symbol.strip().upper()
    normalized_direction = direction.strip().lower()
    if normalized_direction not in {"buy", "sell"}:
        raise ValueError("direction must be buy or sell")
    amount_text = str(_parse_amount(amount))
    symbols = load_onchain_symbols_config()
    symbol_config = symbols.get(normalized_symbol)
    if symbol_config is None:
        return {
            "ok": False,
            "symbol": normalized_symbol,
            "direction": normalized_direction,
            "quote": None,
            "parsed_quote": {},
            "readiness": {},
            "risk": {},
            "trade_limits": {},
            "safety": {},
            "session": {},
            "can_continue_to_manual_swap": False,
            "failures": ["mapping_not_found"],
            "warnings": [],
        }

    futures_signal = read_signal_for_mapping(symbol_config)
    quote_result = _quote_for_preview(symbol_config, normalized_direction, amount_text)
    session = get_execution_session_status(symbol_config.execution_session_filter)
    readiness = check_onchain_executable(
        mapping=symbol_config,
        futures_signal=futures_signal,
        quote_result=quote_result,
        buy_quote_result=quote_result if normalized_direction == "buy" else None,
        sell_quote_result=quote_result if normalized_direction == "sell" else None,
    )
    risk = check_onchain_quote_risk(normalized_symbol, symbol_config, quote_result, normalized_direction)
    trade_limits = check_onchain_trade_limits(
        normalized_symbol,
        "open" if normalized_direction == "buy" else "close",
        load_paper_state(),
        symbol_config,
    )
    safety = _safety_payload()

    failures: list[str] = []
    warnings: list[str] = list(safety.get("warnings", []))
    if not symbol_config.enabled:
        failures.append("mapping_disabled")
    if _address_missing(symbol_config.token_address):
        failures.append("missing_token_address")
    if _address_missing(symbol_config.quote_token_address):
        failures.append("missing_quote_token_address")
    if not session.get("session_allowed"):
        failures.append("outside_us_regular_session")
    if not safety.get("ok"):
        failures.extend(safety.get("failures", []))
    if not quote_result.get("ok"):
        failures.append("quote_not_ok")
    if not readiness.get("executable"):
        failures.append("readiness_failed")
    if not risk.get("ok"):
        failures.append("risk_failed")
    if not trade_limits.get("ok"):
        failures.append("trade_limit_failed")

    failures = list(dict.fromkeys(failures))
    return {
        "ok": not failures,
        "symbol": normalized_symbol,
        "direction": normalized_direction,
        "amount": float(_parse_amount(amount_text)),
        "amount_usdc": float(_parse_amount(amount_text)),
        "mapping": symbol_config.to_dict(),
        "futures_signal": futures_signal,
        "quote": quote_result,
        "parsed_quote": quote_result.get("parsed_quote") if isinstance(quote_result.get("parsed_quote"), dict) else {},
        "readiness": readiness,
        "risk": risk,
        "trade_limits": trade_limits,
        "safety": safety,
        "session": session,
        "can_continue_to_manual_swap": not failures,
        "failures": failures,
        "warnings": list(dict.fromkeys(warnings)),
        "dry_run_only": True,
    }
