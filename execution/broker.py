from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Position:
    symbol: str
    qty: float
    avg_price: float
    realized_pnl: float = 0.0


@dataclass(frozen=True)
class OrderResult:
    order_id: str
    symbol: str
    side: str
    qty: float
    price: float
    status: str
    filled_qty: float
    average_price: float
    metadata: dict[str, Any] = field(default_factory=dict)


class Broker(ABC):
    @abstractmethod
    def get_cash_balance(self) -> float:
        raise NotImplementedError

    @abstractmethod
    def get_positions(self) -> list[Position]:
        raise NotImplementedError

    @abstractmethod
    def place_market_buy(self, symbol: str, qty: float) -> OrderResult:
        raise NotImplementedError

    @abstractmethod
    def place_market_sell(self, symbol: str, qty: float) -> OrderResult:
        raise NotImplementedError

    @abstractmethod
    def cancel_all_orders(self, symbol: str | None = None) -> list[str]:
        raise NotImplementedError
