"""In-memory futures paper broker."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATE_PATH = PROJECT_ROOT / "data" / "futures_paper_state.json"


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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FuturesPaperBroker:
    """Local-only paper broker; it never calls Binance or places real orders."""

    def __init__(self, state_path: Path | None = None) -> None:
        self._state_path = state_path or DEFAULT_STATE_PATH
        self._positions: dict[str, FuturesPosition] = {}
        self._closed_trades: list[dict[str, Any]] = []
        self.load_state()

    def load_state(self) -> None:
        if not self._state_path.exists():
            self._positions = {}
            self._closed_trades = []
            self.save_state()
            return

        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._positions = {}
            self._closed_trades = []
            self.save_state()
            return

        if not isinstance(payload, dict):
            self._positions = {}
            self._closed_trades = []
            self.save_state()
            return

        self._positions = self._load_positions(payload.get("positions"))
        closed_trades = payload.get("closed_trades", [])
        self._closed_trades = closed_trades if isinstance(closed_trades, list) else []

    def save_state(self) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "positions": [position.to_dict() for position in self.get_positions()],
            "closed_trades": self._closed_trades,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._state_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )

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
        self.save_state()
        return position

    def close_position(self, symbol: str, price: float) -> FuturesPosition:
        if price <= 0:
            raise ValueError("price must be greater than zero")
        position = self._positions.pop(symbol)
        position.mark_price = price
        position.unrealized_pnl = self._calculate_unrealized_pnl(position)
        self._closed_trades.append(
            {
                "symbol": position.symbol,
                "side": position.side,
                "position_amt": position.position_amt,
                "entry_price": position.entry_price,
                "close_price": price,
                "realized_pnl": position.unrealized_pnl,
                "leverage": position.leverage,
                "margin": position.margin,
                "closed_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        self.save_state()
        return position

    def update_mark_price(self, symbol: str, mark_price: float) -> FuturesPosition:
        if mark_price <= 0:
            raise ValueError("mark_price must be greater than zero")
        position = self._positions[symbol]
        position.mark_price = mark_price
        position.unrealized_pnl = self._calculate_unrealized_pnl(position)
        self.save_state()
        return position

    def get_positions(self) -> list[FuturesPosition]:
        return list(self._positions.values())

    def get_closed_trades(self) -> list[dict[str, Any]]:
        return list(self._closed_trades)

    @staticmethod
    def _load_positions(payload: Any) -> dict[str, FuturesPosition]:
        if not isinstance(payload, list):
            return {}

        positions: dict[str, FuturesPosition] = {}
        for item in payload:
            if not isinstance(item, dict):
                continue
            try:
                position = FuturesPosition(
                    symbol=str(item["symbol"]),
                    side=str(item["side"]),
                    position_amt=float(item["position_amt"]),
                    entry_price=float(item["entry_price"]),
                    mark_price=float(item["mark_price"]),
                    unrealized_pnl=float(item["unrealized_pnl"]),
                    leverage=float(item["leverage"]),
                    margin=float(item["margin"]),
                )
            except (KeyError, TypeError, ValueError):
                continue
            positions[position.symbol] = position
        return positions

    @staticmethod
    def _calculate_unrealized_pnl(position: FuturesPosition) -> float:
        if position.side == "LONG":
            return (position.mark_price - position.entry_price) * position.position_amt
        if position.side == "SHORT":
            return (position.entry_price - position.mark_price) * position.position_amt
        raise ValueError("side must be LONG or SHORT")
