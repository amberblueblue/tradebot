from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from onchain_bot.paper_state import load_paper_state, save_paper_state


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decimal_from_text(value: Any) -> Decimal | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    first_part = text.split(" ", 1)[0].replace(",", "")
    try:
        return Decimal(first_part)
    except (InvalidOperation, ValueError):
        return None


def _float_from_text(value: Any) -> float | None:
    parsed = _decimal_from_text(value)
    return float(parsed) if parsed is not None else None


def _parsed_quote(quote_result: dict[str, Any]) -> dict[str, Any]:
    parsed = quote_result.get("parsed_quote")
    return parsed if isinstance(parsed, dict) else {}


def _quote_amount(quote_result: dict[str, Any]) -> float | None:
    parsed = _parsed_quote(quote_result)
    return _float_from_text(parsed.get("from_amount_display")) or _float_from_text(quote_result.get("amount_usdt"))


def _token_amount(quote_result: dict[str, Any]) -> float | None:
    return _float_from_text(_parsed_quote(quote_result).get("to_amount_display"))


def _price(quote_result: dict[str, Any]) -> float | None:
    return _float_from_text(_parsed_quote(quote_result).get("implied_price"))


def open_paper_position(
    symbol: str,
    mapping: Any,
    quote_result: dict[str, Any],
    *,
    state_path: Path | None = None,
) -> dict[str, Any]:
    normalized_symbol = symbol.strip().upper()
    state = load_paper_state(state_path)
    positions = state["positions"]
    if normalized_symbol in positions:
        return {
            "ok": False,
            "action": "skipped",
            "symbol": normalized_symbol,
            "reason": "position_exists",
        }

    if not bool(quote_result.get("ok")):
        return {
            "ok": False,
            "action": "skipped",
            "symbol": normalized_symbol,
            "reason": "quote_not_ok",
            "quote_error": quote_result.get("error"),
        }

    entry_quote_amount = _quote_amount(quote_result)
    entry_token_amount = _token_amount(quote_result)
    entry_price = _price(quote_result)
    if entry_quote_amount is None or entry_token_amount is None or entry_price is None:
        return {
            "ok": False,
            "action": "skipped",
            "symbol": normalized_symbol,
            "reason": "quote_missing_parsed_amounts",
        }

    position = {
        "symbol": normalized_symbol,
        "source_symbol": getattr(mapping, "source_symbol", normalized_symbol),
        "chain_name": getattr(mapping, "chain_name", None),
        "chain_id": getattr(mapping, "chain_id", None),
        "token_symbol": getattr(mapping, "token_symbol", None),
        "token_address": getattr(mapping, "token_address", None),
        "quote_token_symbol": getattr(mapping, "quote_token_symbol", None),
        "quote_token_address": getattr(mapping, "quote_token_address", None),
        "entry_quote_amount": entry_quote_amount,
        "entry_token_amount": entry_token_amount,
        "entry_price": entry_price,
        "entry_time": _now_iso(),
        "last_quote_price": entry_price,
        "unrealized_pnl": 0.0,
        "unrealized_pnl_pct": 0.0,
    }
    positions[normalized_symbol] = position
    save_result = save_paper_state(state, state_path)
    return {
        "ok": bool(save_result.get("ok")),
        "action": "paper_open",
        "symbol": normalized_symbol,
        "reason": None if save_result.get("ok") else save_result.get("error"),
        "position": position,
    }


def close_paper_position(
    symbol: str,
    sell_quote_result: dict[str, Any],
    *,
    close_reason: str = "futures_signal_close",
    state_path: Path | None = None,
) -> dict[str, Any]:
    normalized_symbol = symbol.strip().upper()
    state = load_paper_state(state_path)
    positions = state["positions"]
    position = positions.get(normalized_symbol)
    if not isinstance(position, dict):
        return {
            "ok": False,
            "action": "skipped",
            "symbol": normalized_symbol,
            "reason": "position_not_found",
        }

    if not bool(sell_quote_result.get("ok")):
        return {
            "ok": False,
            "action": "skipped",
            "symbol": normalized_symbol,
            "reason": "sell_quote_not_available",
        }
    if sell_quote_result.get("direction") != "sell":
        return {
            "ok": False,
            "action": "skipped",
            "symbol": normalized_symbol,
            "reason": "sell_quote_not_available",
        }

    exit_quote_amount = _token_amount(sell_quote_result)
    if exit_quote_amount is None:
        return {
            "ok": False,
            "action": "skipped",
            "symbol": normalized_symbol,
            "reason": "sell_quote_not_available",
            "sell_quote_mode": "sell_quote",
        }

    entry_price = float(position.get("entry_price") or 0.0)
    entry_token_amount = float(position.get("entry_token_amount") or 0.0)
    entry_quote_amount = float(position.get("entry_quote_amount") or 0.0)
    exit_price = (exit_quote_amount / entry_token_amount) if entry_token_amount else 0.0
    realized_pnl = exit_quote_amount - entry_quote_amount
    realized_pnl_pct = (realized_pnl / entry_quote_amount * 100) if entry_quote_amount else 0.0
    parsed_sell_quote = _parsed_quote(sell_quote_result)

    closed_trade = {
        **position,
        "exit_price": exit_price,
        "exit_quote_amount": exit_quote_amount,
        "exit_time": _now_iso(),
        "realized_pnl": realized_pnl,
        "realized_pnl_pct": realized_pnl_pct,
        "close_reason": close_reason,
        "sell_quote_mode": "sell_quote",
        "sell_quote_route": parsed_sell_quote.get("route"),
        "sell_quote_price_impact": parsed_sell_quote.get("price_impact_pct"),
    }
    positions.pop(normalized_symbol)
    state["closed_trades"].append(closed_trade)
    save_result = save_paper_state(state, state_path)
    return {
        "ok": bool(save_result.get("ok")),
        "action": "paper_close",
        "symbol": normalized_symbol,
        "reason": None if save_result.get("ok") else save_result.get("error"),
        "closed_trade": closed_trade,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "realized_pnl": realized_pnl,
        "realized_pnl_pct": realized_pnl_pct,
        "sell_quote_mode": "sell_quote",
    }
