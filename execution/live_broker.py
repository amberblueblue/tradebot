from __future__ import annotations

from execution.broker import Broker, OrderResult, Position


class LiveBroker(Broker):
    """Reserved for future real trading integration."""

    def get_cash_balance(self) -> float:
        raise NotImplementedError("Live trading is intentionally not enabled in phase 1 step 2")

    def get_positions(self) -> list[Position]:
        raise NotImplementedError("Live trading is intentionally not enabled in phase 1 step 2")

    def place_market_buy(self, symbol: str, qty: float) -> OrderResult:
        raise NotImplementedError("Live trading is intentionally not enabled in phase 1 step 2")

    def place_market_sell(self, symbol: str, qty: float) -> OrderResult:
        raise NotImplementedError("Live trading is intentionally not enabled in phase 1 step 2")

    def cancel_all_orders(self, symbol: str | None = None) -> list[str]:
        raise NotImplementedError("Live trading is intentionally not enabled in phase 1 step 2")
