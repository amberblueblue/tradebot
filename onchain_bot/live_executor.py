from __future__ import annotations

import json
import os
from decimal import Decimal, InvalidOperation
from typing import Any

from onchain_bot.config_loader import load_onchain_settings_config, load_onchain_symbols_config
from onchain_bot.executable_check import check_onchain_executable
from onchain_bot.live_guard import assert_onchain_live_allowed
from onchain_bot.live_trade_log import append_live_trade
from onchain_bot.live_transaction_builder import build_unsigned_transactions
from onchain_bot.okx_dex_client import OkxDexQuoteClient
from onchain_bot.paper_state import load_paper_state
from onchain_bot.risk import check_onchain_quote_risk
from onchain_bot.session_filter import get_execution_session_status
from onchain_bot.signal_reader import read_signal_for_mapping
from onchain_bot.status_onchain import build_quote_payload
from onchain_bot.trade_limits import check_onchain_trade_limits
from onchain_bot.wallet_guard import check_wallet_environment
from onchain_bot.wallet_signer import broadcast_transaction as broadcast_signed_transaction
from onchain_bot.wallet_signer import sign_transaction as sign_unsigned_transaction


ONCHAIN_WALLET_ADDRESS_ENV = "ONCHAIN_WALLET_ADDRESS"


def _parse_amount(value: str | int | float | Decimal) -> Decimal:
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("amount must be a number greater than 0") from exc
    if amount <= 0:
        raise ValueError("amount must be greater than 0")
    return amount


def _float_from_text(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).split(" ", 1)[0].replace(",", "")
    try:
        return float(Decimal(text))
    except (InvalidOperation, ValueError):
        return None


def _guard_amount(direction: str, requested_amount: str, quote_result: dict[str, Any]) -> str:
    if direction == "buy":
        return requested_amount
    parsed = quote_result.get("parsed_quote")
    if isinstance(parsed, dict):
        to_amount = _float_from_text(parsed.get("to_amount_display"))
        if to_amount is not None and to_amount > 0:
            return str(to_amount)
    return requested_amount


def _swap_addresses(mapping: Any, direction: str) -> tuple[str, str, str, str]:
    if direction == "buy":
        return (
            mapping.quote_token_address,
            mapping.token_address,
            mapping.quote_token_symbol,
            mapping.token_symbol,
        )
    return (
        mapping.token_address,
        mapping.quote_token_address,
        mapping.token_symbol,
        mapping.quote_token_symbol,
    )


def prepare_live_swap(symbol: str, direction: str, amount: str | int | float | Decimal) -> dict[str, Any]:
    normalized_symbol = symbol.strip().upper()
    normalized_direction = direction.strip().lower()
    if normalized_direction not in {"buy", "sell"}:
        raise ValueError("direction must be buy or sell")
    amount_text = str(_parse_amount(amount))

    symbols = load_onchain_symbols_config()
    mapping = symbols.get(normalized_symbol)
    if mapping is None:
        return {
            "ok": False,
            "symbol": normalized_symbol,
            "direction": normalized_direction,
            "tx_preview": None,
            "failures": ["mapping_not_found"],
            "warnings": [],
        }

    futures_signal = read_signal_for_mapping(mapping)
    session = get_execution_session_status(mapping.execution_session_filter)
    quote_result = build_quote_payload(normalized_symbol, amount_text, direction=normalized_direction)
    readiness = check_onchain_executable(
        mapping=mapping,
        futures_signal=futures_signal,
        quote_result=quote_result,
        buy_quote_result=quote_result if normalized_direction == "buy" else None,
        sell_quote_result=quote_result if normalized_direction == "sell" else None,
    )
    risk = check_onchain_quote_risk(normalized_symbol, mapping, quote_result, normalized_direction)
    trade_limits = check_onchain_trade_limits(
        normalized_symbol,
        "open" if normalized_direction == "buy" else "close",
        load_paper_state(),
        mapping,
    )
    guard = assert_onchain_live_allowed(
        f"auto_{normalized_direction}",
        amount_usdt=_guard_amount(normalized_direction, amount_text, quote_result),
    )

    failures: list[str] = []
    warnings: list[str] = []
    if not mapping.enabled:
        failures.append("mapping_disabled")
    if not session.get("session_allowed"):
        failures.append("outside_us_regular_session")
    if not quote_result.get("ok"):
        failures.append("quote_not_ok")
    if not readiness.get("executable"):
        failures.append("readiness_failed")
    if not risk.get("ok"):
        failures.append("risk_failed")
    if not trade_limits.get("ok"):
        failures.append("trade_limit_failed")
    if not guard.get("allowed"):
        failures.append(str(guard.get("reason") or "live_guard_rejected"))

    tx_preview = None
    if not failures:
        from_token_address, to_token_address, from_token_symbol, to_token_symbol = _swap_addresses(mapping, normalized_direction)
        print(
            f"[ONCHAIN_TX_PREVIEW] symbol={normalized_symbol} direction={normalized_direction} "
            f"from={from_token_symbol} to={to_token_symbol}"
        )
        tx_preview = OkxDexQuoteClient().swap_tx_data(
            chain_id=mapping.chain_id,
            from_token_address=from_token_address,
            to_token_address=to_token_address,
            amount=int(quote_result.get("from_token_amount") or 0),
            slippage_pct=mapping.max_slippage_pct,
            user_wallet_address=os.environ.get(ONCHAIN_WALLET_ADDRESS_ENV, ""),
        )
        if not tx_preview.get("ok"):
            failures.append(str(tx_preview.get("error") or "tx_preview_failed"))
    else:
        print(
            f"[ONCHAIN_TX_PREVIEW] skipped symbol={normalized_symbol} "
            f"direction={normalized_direction} failures={','.join(failures)}"
        )

    result = {
        "ok": not failures,
        "symbol": normalized_symbol,
        "direction": normalized_direction,
        "amount": float(_parse_amount(amount_text)),
        "mapping": mapping.to_dict(),
        "futures_signal": futures_signal,
        "session": session,
        "quote": quote_result,
        "parsed_quote": quote_result.get("parsed_quote") if isinstance(quote_result.get("parsed_quote"), dict) else {},
        "readiness": readiness,
        "risk": risk,
        "trade_limits": trade_limits,
        "live_guard": guard,
        "tx_preview": tx_preview,
        "failures": list(dict.fromkeys(failures)),
        "warnings": warnings,
        "signing": "not_implemented",
        "broadcast": "not_implemented",
    }
    print(f"[ONCHAIN_AUTO_LIVE_CHECK] {json.dumps(result, ensure_ascii=False, sort_keys=True, default=str)}")
    return result


def prepare_unsigned_live_transactions(symbol: str, direction: str, amount: str | int | float | Decimal) -> dict[str, Any]:
    preview = prepare_live_swap(symbol, direction, amount)
    wallet_guard = check_wallet_environment()
    live_guard = preview.get("live_guard") if isinstance(preview.get("live_guard"), dict) else {}
    mapping = preview.get("mapping") if isinstance(preview.get("mapping"), dict) else {}
    unsigned = build_unsigned_transactions(
        preview.get("tx_preview") if isinstance(preview.get("tx_preview"), dict) else None,
        chain_id=str(mapping.get("chain_id") or preview.get("chain_id") or ""),
        direction=str(preview.get("direction") or direction),
        symbol=str(preview.get("symbol") or symbol).upper(),
    )
    failures = []
    for source in (preview.get("failures"), unsigned.get("failures")):
        if isinstance(source, list):
            failures.extend(str(item) for item in source)
    warnings = []
    for source in (preview.get("warnings"), unsigned.get("warnings")):
        if isinstance(source, list):
            warnings.extend(str(item) for item in source)
    return {
        "ok": bool(preview.get("ok")) and bool(unsigned.get("ok")),
        "symbol": str(preview.get("symbol") or symbol).upper(),
        "direction": str(preview.get("direction") or direction),
        "amount": preview.get("amount"),
        "approve_transaction": unsigned.get("approve_transaction"),
        "swap_transaction": unsigned.get("swap_transaction"),
        "wallet_guard": wallet_guard,
        "broadcast_guard": {
            "broadcast_enabled": wallet_guard.get("broadcast_enabled"),
            "allowed": False,
            "reason": "broadcast_not_enabled",
        },
        "live_guard": live_guard,
        "tx_preview": preview.get("tx_preview"),
        "quote": preview.get("quote"),
        "parsed_quote": preview.get("parsed_quote"),
        "risk_result": preview.get("risk"),
        "failures": list(dict.fromkeys(failures)),
        "warnings": list(dict.fromkeys(warnings)),
        "signing": "not_implemented",
        "broadcast": "not_implemented",
    }


def sign_live_transaction(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return sign_unsigned_transaction(*args, **kwargs)


def broadcast_live_transaction(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return broadcast_signed_transaction(*args, **kwargs)


def execute_live_swap(symbol: str, direction: str, amount: str | int | float | Decimal) -> dict[str, Any]:
    settings = load_onchain_settings_config()
    unsigned = prepare_unsigned_live_transactions(symbol, direction, amount)
    if unsigned.get("failures"):
        return {
            **unsigned,
            "ok": False,
            "reason": "preflight_failed",
            "tx_hash": None,
        }
    if not settings.live_wallet_signing_enabled:
        return {
            **unsigned,
            "ok": False,
            "reason": "wallet_signing_not_enabled",
            "tx_hash": None,
        }
    if not settings.live_broadcast_enabled:
        return {
            **unsigned,
            "ok": False,
            "reason": "broadcast_not_enabled",
            "tx_hash": None,
        }
    swap_transaction = unsigned.get("swap_transaction")
    if not isinstance(swap_transaction, dict):
        return {
            **unsigned,
            "ok": False,
            "reason": "swap_tx_data_missing",
            "tx_hash": None,
        }
    signed = sign_live_transaction(
        swap_transaction,
        amount_usdt=amount if str(direction).lower() == "buy" else unsigned.get("amount"),
        action=f"execute_{direction}",
    )
    if not signed.get("ok"):
        return {
            **unsigned,
            "ok": False,
            "reason": signed.get("reason") or "wallet_signing_failed",
            "sign_result": {key: value for key, value in signed.items() if key != "signed_tx"},
            "tx_hash": None,
        }
    broadcast = broadcast_live_transaction(
        signed.get("signed_tx"),
        chain_id=swap_transaction.get("chain_id"),
    )
    if not broadcast.get("ok"):
        return {
            **unsigned,
            "ok": False,
            "reason": broadcast.get("reason") or "broadcast_failed",
            "broadcast_result": broadcast,
            "tx_hash": None,
        }
    tx_hash = broadcast.get("tx_hash")
    append_live_trade(
        {
            "symbol": unsigned.get("symbol"),
            "direction": unsigned.get("direction"),
            "amount": unsigned.get("amount"),
            "tx_hash": tx_hash,
            "status": "submitted",
            "quote": unsigned.get("tx_preview"),
            "parsed_quote": unsigned.get("parsed_quote"),
            "risk_result": unsigned.get("risk_result"),
        }
    )
    return {
        **unsigned,
        "ok": True,
        "reason": "submitted",
        "tx_hash": tx_hash,
        "broadcast_result": broadcast,
    }
