from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SymbolTradingRules:
    symbol: str
    min_notional: float = 5.0
    amount_precision: int = 2
    quantity_precision: int = 6


DEFAULT_MIN_NOTIONAL = 5.0
DEFAULT_AMOUNT_PRECISION = 2
DEFAULT_QUANTITY_PRECISION = 6


def get_default_symbol_rules(symbol: str) -> SymbolTradingRules:
    return SymbolTradingRules(symbol=symbol)

