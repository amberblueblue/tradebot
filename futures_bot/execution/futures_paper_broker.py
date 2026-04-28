from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


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
    liquidation_price: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FuturesPaperBroker:
    """In-memory futures paper broker; never calls Binance or changes exchange state."""

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
        normalized_symbol = self._normalize_symbol(symbol)
        normalized_side = self._normalize_side(side)
        margin_value = self._require_positive(margin, "margin")
        leverage_value = self._require_positive(leverage, "leverage")
        price_value = self._require_positive(price, "price")

        position = FuturesPosition(
            symbol=normalized_symbol,
            side=normalized_side,
            position_amt=(margin_value * leverage_value) / price_value,
            entry_price=price_value,
            mark_price=price_value,
            unrealized_pnl=0.0,
            leverage=leverage_value,
            margin=margin_value,
            liquidation_price=self._estimate_liquidation_price(
                side=normalized_side,
                entry_price=price_value,
                leverage=leverage_value,
            ),
        )
        self._positions[normalized_symbol] = position
        return position

    def close_position(self, symbol: str, price: float) -> dict[str, Any]:
        normalized_symbol = self._normalize_symbol(symbol)
        price_value = self._require_positive(price, "price")
        position = self._positions.pop(normalized_symbol, None)
        if position is None:
            raise ValueError(f"No futures paper position for symbol: {normalized_symbol}")

        realized_pnl = self._calculate_pnl(
            side=position.side,
            entry_price=position.entry_price,
            mark_price=price_value,
            position_amt=position.position_amt,
        )
        position.mark_price = price_value
        position.unrealized_pnl = realized_pnl
        return {
            "symbol": position.symbol,
            "side": position.side,
            "position_amt": position.position_amt,
            "entry_price": position.entry_price,
            "close_price": price_value,
            "realized_pnl": realized_pnl,
            "margin": position.margin,
            "leverage": position.leverage,
        }

    def update_mark_price(self, symbol: str, mark_price: float) -> FuturesPosition:
        normalized_symbol = self._normalize_symbol(symbol)
        mark_price_value = self._require_positive(mark_price, "mark_price")
        position = self._positions.get(normalized_symbol)
        if position is None:
            raise ValueError(f"No futures paper position for symbol: {normalized_symbol}")

        position.mark_price = mark_price_value
        position.unrealized_pnl = self._calculate_pnl(
            side=position.side,
            entry_price=position.entry_price,
            mark_price=mark_price_value,
            position_amt=position.position_amt,
        )
        return position

    def get_positions(self) -> list[FuturesPosition]:
        return list(self._positions.values())

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        normalized = str(symbol).strip().upper()
        if not normalized:
            raise ValueError("symbol must be non-empty")
        return normalized

    @staticmethod
    def _normalize_side(side: str) -> str:
        normalized = str(side).strip().upper()
        if normalized not in {"LONG", "SHORT"}:
            raise ValueError("side must be LONG or SHORT")
        return normalized

    @staticmethod
    def _require_positive(value: float, field_name: str) -> float:
        if isinstance(value, bool):
            raise ValueError(f"{field_name} must be a positive number")
        numeric_value = float(value)
        if numeric_value <= 0:
            raise ValueError(f"{field_name} must be greater than 0")
        return numeric_value

    @staticmethod
    def _calculate_pnl(
        *,
        side: str,
        entry_price: float,
        mark_price: float,
        position_amt: float,
    ) -> float:
        if side == "LONG":
            return (mark_price - entry_price) * position_amt
        if side == "SHORT":
            return (entry_price - mark_price) * position_amt
        raise ValueError("side must be LONG or SHORT")

    @staticmethod
    def _estimate_liquidation_price(*, side: str, entry_price: float, leverage: float) -> float:
        leverage = max(leverage, 1.0)
        if side == "LONG":
            return max(0.0, entry_price * (1 - 1 / leverage))
        if side == "SHORT":
            return entry_price * (1 + 1 / leverage)
        raise ValueError("side must be LONG or SHORT")
