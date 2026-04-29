"""In-memory futures paper broker."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FuturesPosition:
    symbol: str
    side: str
    position_amt: float
    entry_price: float
    mark_price: float
    unrealized_pnl: float
    leverage: float
    margin: float


class FuturesPaperBroker:
    """Local-only paper broker; it never calls Binance or places real orders."""

    def __init__(self) -> None:
        self._positions: dict[str, FuturesPosition] = {}

    def open_position(
        self,
        symbol: str,
        side: str,
        margin: float,
        leverage: float,
        price: float,
    ) -> FuturesPosition:
        normalized_side = side.upper()
        if normalized_side not in {"LONG", "SHORT"}:
            raise ValueError("side must be LONG or SHORT")
        if margin <= 0:
            raise ValueError("margin must be greater than zero")
        if leverage <= 0:
            raise ValueError("leverage must be greater than zero")
        if price <= 0:
            raise ValueError("price must be greater than zero")

        position_amt = margin * leverage / price
        position = FuturesPosition(
            symbol=symbol,
            side=normalized_side,
            position_amt=position_amt,
            entry_price=price,
            mark_price=price,
            unrealized_pnl=0.0,
            leverage=leverage,
            margin=margin,
        )
        self._positions[symbol] = position
        return position

    def close_position(self, symbol: str, price: float) -> FuturesPosition:
        if price <= 0:
            raise ValueError("price must be greater than zero")
        position = self._positions.pop(symbol)
        position.mark_price = price
        position.unrealized_pnl = self._calculate_unrealized_pnl(position)
        return position

    def update_mark_price(self, symbol: str, mark_price: float) -> FuturesPosition:
        if mark_price <= 0:
            raise ValueError("mark_price must be greater than zero")
        position = self._positions[symbol]
        position.mark_price = mark_price
        position.unrealized_pnl = self._calculate_unrealized_pnl(position)
        return position

    def get_positions(self) -> list[FuturesPosition]:
        return list(self._positions.values())

    @staticmethod
    def _calculate_unrealized_pnl(position: FuturesPosition) -> float:
        if position.side == "LONG":
            return (position.mark_price - position.entry_price) * position.position_amt
        if position.side == "SHORT":
            return (position.entry_price - position.mark_price) * position.position_amt
        raise ValueError("side must be LONG or SHORT")
