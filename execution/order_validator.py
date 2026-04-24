from __future__ import annotations

from dataclasses import dataclass

from config.loader import SymbolTradingConfig
from exchange.rules import SymbolTradingRules, get_default_symbol_rules


@dataclass(frozen=True)
class OrderValidationResult:
    ok: bool
    reason: str
    normalized_quantity: float
    normalized_amount: float


def _round_down(value: float, precision: int) -> float:
    factor = 10 ** precision
    return int(float(value) * factor) / factor


def validate_entry_order(
    *,
    symbol_config: SymbolTradingConfig,
    quantity: float,
    price: float,
    realized_pnl: float,
    current_position_count: int,
    max_positions: int,
    bot_status: str,
    rules: SymbolTradingRules | None = None,
) -> OrderValidationResult:
    rules = rules or get_default_symbol_rules(symbol_config.symbol)

    if bot_status != "running":
        return OrderValidationResult(False, "bot_not_running", 0.0, 0.0)
    if not symbol_config.enabled:
        return OrderValidationResult(False, "symbol_disabled", 0.0, 0.0)
    if symbol_config.paused_by_loss:
        return OrderValidationResult(False, "symbol_paused_by_loss", 0.0, 0.0)
    if symbol_config.order_amount <= 0:
        return OrderValidationResult(False, "order_amount_not_positive", 0.0, 0.0)
    if quantity <= 0:
        return OrderValidationResult(False, "quantity_not_positive", 0.0, 0.0)
    if price <= 0:
        return OrderValidationResult(False, "price_not_positive", 0.0, 0.0)
    if realized_pnl <= -symbol_config.max_loss_amount:
        return OrderValidationResult(False, "max_loss_amount_reached", 0.0, 0.0)
    if current_position_count >= max_positions:
        return OrderValidationResult(False, "max_positions_reached", 0.0, 0.0)

    normalized_quantity = _round_down(quantity, rules.quantity_precision)
    normalized_amount = _round_down(normalized_quantity * price, rules.amount_precision)
    if normalized_quantity <= 0:
        return OrderValidationResult(False, "normalized_quantity_not_positive", 0.0, normalized_amount)
    if normalized_amount < rules.min_notional:
        return OrderValidationResult(False, "min_notional_not_met", normalized_quantity, normalized_amount)

    return OrderValidationResult(True, "ok", normalized_quantity, normalized_amount)

