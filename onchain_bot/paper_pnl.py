from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from onchain_bot.paper_state import load_paper_state, save_paper_state
from onchain_bot.quote_cache import get_cached_quote


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


def _latest_quote_price(symbol: str) -> float | None:
    cached_quote = get_cached_quote(symbol)
    if not cached_quote or not bool(cached_quote.get("ok")):
        return None
    parsed_quote = cached_quote.get("parsed_quote")
    if not isinstance(parsed_quote, dict):
        return None
    price = _decimal_from_text(parsed_quote.get("implied_price"))
    return float(price) if price is not None else None


def update_paper_positions_with_latest_quotes() -> dict[str, Any]:
    state = load_paper_state()
    positions = state.get("positions", {})
    if not isinstance(positions, dict):
        positions = {}
        state["positions"] = positions

    updated_symbols: list[str] = []
    skipped_symbols: list[str] = []
    for symbol, position in positions.items():
        if not isinstance(position, dict):
            skipped_symbols.append(symbol)
            continue
        latest_price = _latest_quote_price(symbol)
        if latest_price is None:
            skipped_symbols.append(symbol)
            continue

        entry_quote_amount = float(position.get("entry_quote_amount") or 0.0)
        entry_token_amount = float(position.get("entry_token_amount") or 0.0)
        current_value = entry_token_amount * latest_price
        unrealized_pnl = current_value - entry_quote_amount
        unrealized_pnl_pct = (unrealized_pnl / entry_quote_amount * 100) if entry_quote_amount else 0.0

        position["latest_quote_price"] = latest_price
        position["last_quote_price"] = latest_price
        position["unrealized_pnl"] = unrealized_pnl
        position["unrealized_pnl_pct"] = unrealized_pnl_pct
        updated_symbols.append(symbol)

    save_result = save_paper_state(state)
    return {
        "ok": bool(save_result.get("ok")),
        "updated_symbols": updated_symbols,
        "skipped_symbols": skipped_symbols,
        "positions_count": len(positions),
        "error": save_result.get("error"),
        "message": save_result.get("message"),
    }
