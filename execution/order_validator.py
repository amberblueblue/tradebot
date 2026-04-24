from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from config.loader import SymbolTradingConfig
from exchange.rules import SymbolTradingRules, fetch_symbol_rules


@dataclass(frozen=True)
class OrderValidationResult:
    ok: bool
    reason: str
    raw_quantity: float
    normalized_quantity: float
    raw_price: float
    normalized_price: float
    notional: float
    normalized_amount: float


def _result(
    ok: bool,
    reason: str,
    *,
    raw_quantity: float,
    normalized_quantity: float,
    raw_price: float,
    normalized_price: float,
    notional: float,
) -> OrderValidationResult:
    return OrderValidationResult(
        ok=ok,
        reason=reason,
        raw_quantity=raw_quantity,
        normalized_quantity=normalized_quantity,
        raw_price=raw_price,
        normalized_price=normalized_price,
        notional=notional,
        normalized_amount=notional,
    )


def _decimal(value: float) -> Decimal:
    return Decimal(str(value))


def _round_down_to_step(value: float, step: float) -> float:
    decimal_value = _decimal(value)
    decimal_step = _decimal(step)
    if decimal_step <= 0:
        return float(decimal_value)
    normalized = (decimal_value // decimal_step) * decimal_step
    return float(normalized)


def _is_step_aligned(value: float, step: float) -> bool:
    if step <= 0:
        return True
    decimal_value = _decimal(value)
    decimal_step = _decimal(step)
    return decimal_value % decimal_step == 0


def _effective_min_qty(rules: SymbolTradingRules) -> float:
    return rules.min_qty


def _effective_max_qty(rules: SymbolTradingRules) -> float:
    return rules.max_qty


def _effective_step_size(rules: SymbolTradingRules) -> float:
    return rules.step_size


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
    rules = rules or fetch_symbol_rules(symbol_config.symbol)
    raw_quantity = float(quantity)
    raw_price = float(price)
    normalized_quantity = 0.0
    normalized_price = 0.0
    notional = 0.0

    if bot_status != "running":
        return _result(
            False,
            "bot_not_running",
            raw_quantity=raw_quantity,
            normalized_quantity=normalized_quantity,
            raw_price=raw_price,
            normalized_price=normalized_price,
            notional=notional,
        )
    if not symbol_config.enabled:
        return _result(
            False,
            "symbol_disabled",
            raw_quantity=raw_quantity,
            normalized_quantity=normalized_quantity,
            raw_price=raw_price,
            normalized_price=normalized_price,
            notional=notional,
        )
    if symbol_config.paused_by_loss:
        return _result(
            False,
            "symbol_paused_by_loss",
            raw_quantity=raw_quantity,
            normalized_quantity=normalized_quantity,
            raw_price=raw_price,
            normalized_price=normalized_price,
            notional=notional,
        )
    if symbol_config.order_amount <= 0:
        return _result(
            False,
            "order_amount_not_positive",
            raw_quantity=raw_quantity,
            normalized_quantity=normalized_quantity,
            raw_price=raw_price,
            normalized_price=normalized_price,
            notional=notional,
        )
    if quantity <= 0:
        return _result(
            False,
            "quantity_not_positive",
            raw_quantity=raw_quantity,
            normalized_quantity=normalized_quantity,
            raw_price=raw_price,
            normalized_price=normalized_price,
            notional=notional,
        )
    if price <= 0:
        return _result(
            False,
            "price_not_positive",
            raw_quantity=raw_quantity,
            normalized_quantity=normalized_quantity,
            raw_price=raw_price,
            normalized_price=normalized_price,
            notional=notional,
        )
    if realized_pnl <= -symbol_config.max_loss_amount:
        return _result(
            False,
            "max_loss_amount_reached",
            raw_quantity=raw_quantity,
            normalized_quantity=normalized_quantity,
            raw_price=raw_price,
            normalized_price=normalized_price,
            notional=notional,
        )
    if current_position_count >= max_positions:
        return _result(
            False,
            "max_positions_reached",
            raw_quantity=raw_quantity,
            normalized_quantity=normalized_quantity,
            raw_price=raw_price,
            normalized_price=normalized_price,
            notional=notional,
        )

    normalized_price = _round_down_to_step(raw_price, rules.tick_size)
    quantity_step_size = _effective_step_size(rules)
    min_qty = _effective_min_qty(rules)
    max_qty = _effective_max_qty(rules)
    normalized_quantity = _round_down_to_step(raw_quantity, quantity_step_size)
    notional = float(_decimal(normalized_price) * _decimal(normalized_quantity))
    min_notional = rules.effective_min_notional

    if symbol_config.order_amount < min_notional:
        return _result(
            False,
            "order_amount_below_min_notional",
            raw_quantity=raw_quantity,
            normalized_quantity=normalized_quantity,
            raw_price=raw_price,
            normalized_price=normalized_price,
            notional=notional,
        )
    if not _is_step_aligned(normalized_price, rules.tick_size):
        return _result(
            False,
            "price_tick_size_mismatch",
            raw_quantity=raw_quantity,
            normalized_quantity=normalized_quantity,
            raw_price=raw_price,
            normalized_price=normalized_price,
            notional=notional,
        )
    if not _is_step_aligned(normalized_quantity, quantity_step_size):
        return _result(
            False,
            "quantity_step_size_mismatch",
            raw_quantity=raw_quantity,
            normalized_quantity=normalized_quantity,
            raw_price=raw_price,
            normalized_price=normalized_price,
            notional=notional,
        )
    if raw_quantity < min_qty or normalized_quantity < min_qty:
        return _result(
            False,
            "quantity_below_min_qty",
            raw_quantity=raw_quantity,
            normalized_quantity=normalized_quantity,
            raw_price=raw_price,
            normalized_price=normalized_price,
            notional=notional,
        )
    if raw_quantity > max_qty or normalized_quantity > max_qty:
        return _result(
            False,
            "quantity_above_max_qty",
            raw_quantity=raw_quantity,
            normalized_quantity=normalized_quantity,
            raw_price=raw_price,
            normalized_price=normalized_price,
            notional=notional,
        )
    if normalized_quantity <= 0:
        return _result(
            False,
            "normalized_quantity_not_positive",
            raw_quantity=raw_quantity,
            normalized_quantity=normalized_quantity,
            raw_price=raw_price,
            normalized_price=normalized_price,
            notional=notional,
        )
    if notional < min_notional:
        return _result(
            False,
            "min_notional_not_met",
            raw_quantity=raw_quantity,
            normalized_quantity=normalized_quantity,
            raw_price=raw_price,
            normalized_price=normalized_price,
            notional=notional,
        )
    if rules.notional_max is not None and rules.notional_max > 0 and notional > rules.notional_max:
        return _result(
            False,
            "max_notional_exceeded",
            raw_quantity=raw_quantity,
            normalized_quantity=normalized_quantity,
            raw_price=raw_price,
            normalized_price=normalized_price,
            notional=notional,
        )

    return _result(
        True,
        "ok",
        raw_quantity=raw_quantity,
        normalized_quantity=normalized_quantity,
        raw_price=raw_price,
        normalized_price=normalized_price,
        notional=notional,
    )
