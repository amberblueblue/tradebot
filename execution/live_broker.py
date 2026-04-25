from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from execution.broker import Broker, OrderResult, Position


class LiveBroker(Broker):
    """Live broker skeleton that simulates orders without calling an exchange."""

    def __init__(self) -> None:
        self.market_prices: dict[str, float] = {}

    def set_market_price(self, symbol: str, price: float) -> None:
        if price <= 0:
            raise ValueError("price must be positive")
        self.market_prices[symbol] = float(price)

    def get_market_price(self, symbol: str) -> float | None:
        if symbol not in self.market_prices:
            return None
        return float(self.market_prices[symbol])

    def get_cash_balance(self) -> float:
        return 0.0

    def get_positions(self) -> list[Position]:
        return []

    def get_open_orders(self) -> list[dict[str, Any]]:
        return []

    def place_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float | None = None,
    ) -> OrderResult:
        if quantity <= 0:
            raise ValueError("quantity must be positive")

        normalized_side = side.upper()
        resolved_price = 0.0 if price is None else float(price)
        print(
            f"[LIVE_ORDER_SIMULATION] symbol={symbol} "
            f"side={normalized_side} quantity={quantity}"
        )
        return OrderResult(
            order_id=uuid4().hex,
            symbol=symbol,
            side=normalized_side,
            qty=quantity,
            price=resolved_price,
            status="SIMULATED",
            filled_qty=0.0,
            average_price=resolved_price,
            metadata={
                "simulated": True,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    def place_market_buy(self, symbol: str, qty: float) -> OrderResult:
        return self.place_order(symbol, "BUY", qty)

    def place_market_sell(self, symbol: str, qty: float) -> OrderResult:
        return self.place_order(symbol, "SELL", qty)

    def cancel_order(self, order_id: str) -> str:
        return order_id

    def cancel_all_orders(self, symbol: str | None = None) -> list[str]:
        return []
