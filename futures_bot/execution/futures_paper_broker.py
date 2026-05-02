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
    entry_time: str | None = None
    entry_bar_index: int | None = None
    partial1_done: bool = False
    partial2_done: bool = False
    max_unrealized_return: float = 0.0
    current_return: float = 0.0
    holding_bars: int = 0

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
        entry_bar_index: int | None = None,
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
            entry_time=datetime.now(timezone.utc).isoformat(),
            entry_bar_index=entry_bar_index,
            partial1_done=False,
            partial2_done=False,
            max_unrealized_return=0.0,
            current_return=0.0,
            holding_bars=0,
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
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "symbol": position.symbol,
                "side": position.side,
                "entry_price": position.entry_price,
                "exit_price": price,
                "position_amt": position.position_amt,
                "margin": position.margin,
                "leverage": position.leverage,
                "realized_pnl": position.unrealized_pnl,
                "close_type": "full",
                "sell_pct": 100.0,
            }
        )
        self.save_state()
        return position

    def close_partial(self, symbol: str, sell_pct: float, price: float) -> FuturesPosition:
        if price <= 0:
            raise ValueError("price must be greater than zero")
        if not 0 < sell_pct <= 100:
            raise ValueError("sell_pct must be greater than 0 and less than or equal to 100")
        position = self._positions[symbol]
        close_amt = position.position_amt * (sell_pct / 100)
        if close_amt <= 0:
            raise ValueError("partial close amount must be greater than zero")
        realized_pnl = self._calculate_unrealized_pnl_for_amount(position, price, close_amt)
        remaining_amt = position.position_amt - close_amt
        position.position_amt = max(remaining_amt, 0.0)
        position.margin = max(position.margin * (1 - sell_pct / 100), 0.0)
        position.mark_price = price
        position.unrealized_pnl = self._calculate_unrealized_pnl(position)
        position.current_return = self._calculate_current_return(position)
        position.max_unrealized_return = max(position.max_unrealized_return, position.current_return)
        if abs(sell_pct - 30) < 0.000001:
            position.partial1_done = True
        elif abs(sell_pct - 50) < 0.000001:
            position.partial2_done = True
        self._closed_trades.append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "symbol": position.symbol,
                "side": position.side,
                "entry_price": position.entry_price,
                "exit_price": price,
                "position_amt": close_amt,
                "remaining_position_amt": position.position_amt,
                "margin": position.margin,
                "leverage": position.leverage,
                "realized_pnl": realized_pnl,
                "close_type": "partial",
                "sell_pct": sell_pct,
            }
        )
        if position.position_amt <= 0:
            self._positions.pop(symbol, None)
        self.save_state()
        return position

    def update_mark_price(self, symbol: str, mark_price: float) -> FuturesPosition:
        if mark_price <= 0:
            raise ValueError("mark_price must be greater than zero")
        position = self._positions[symbol]
        position.mark_price = mark_price
        position.unrealized_pnl = self._calculate_unrealized_pnl(position)
        position.current_return = self._calculate_current_return(position)
        position.max_unrealized_return = max(position.max_unrealized_return, position.current_return)
        self.save_state()
        return position

    def update_position_metrics(self, symbol: str, *, current_bar_index: int | None = None) -> FuturesPosition:
        position = self._positions[symbol]
        position.current_return = self._calculate_current_return(position)
        position.max_unrealized_return = max(position.max_unrealized_return, position.current_return)
        if current_bar_index is not None and position.entry_bar_index is not None:
            position.holding_bars = max(current_bar_index - position.entry_bar_index, 0)
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
                    entry_time=item.get("entry_time"),
                    entry_bar_index=(
                        int(item["entry_bar_index"])
                        if item.get("entry_bar_index") is not None
                        else None
                    ),
                    partial1_done=bool(item.get("partial1_done", False)),
                    partial2_done=bool(item.get("partial2_done", False)),
                    max_unrealized_return=float(item.get("max_unrealized_return", 0.0)),
                    current_return=float(item.get("current_return", 0.0)),
                    holding_bars=int(item.get("holding_bars", 0)),
                )
            except (KeyError, TypeError, ValueError):
                continue
            positions[position.symbol] = position
        return positions

    @staticmethod
    def _calculate_unrealized_pnl(position: FuturesPosition) -> float:
        return FuturesPaperBroker._calculate_unrealized_pnl_for_amount(
            position,
            position.mark_price,
            position.position_amt,
        )

    @staticmethod
    def _calculate_unrealized_pnl_for_amount(
        position: FuturesPosition,
        price: float,
        amount: float,
    ) -> float:
        if position.side == "LONG":
            return (price - position.entry_price) * amount
        if position.side == "SHORT":
            return (position.entry_price - price) * amount
        raise ValueError("side must be LONG or SHORT")

    @staticmethod
    def _calculate_current_return(position: FuturesPosition) -> float:
        if position.entry_price <= 0:
            return 0.0
        if position.side == "LONG":
            return ((position.mark_price - position.entry_price) / position.entry_price) * 100
        if position.side == "SHORT":
            return ((position.entry_price - position.mark_price) / position.entry_price) * 100
        raise ValueError("side must be LONG or SHORT")
