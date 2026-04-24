from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from execution.broker import Broker, OrderResult, Position


@dataclass
class PaperPosition:
    symbol: str
    qty: float = 0.0
    avg_price: float = 0.0
    realized_pnl: float = 0.0


@dataclass
class PaperBrokerState:
    cash_balance: float
    positions: dict[str, PaperPosition] = field(default_factory=dict)
    orders: list[dict[str, Any]] = field(default_factory=list)


class PaperBroker(Broker):
    def __init__(
        self,
        *,
        initial_cash: float = 10000.0,
        state_file: str = "runtime/paper_state.json",
        trade_log_file: str = "logs/paper_trades.jsonl",
        price_feed: dict[str, float] | None = None,
    ) -> None:
        self.state_path = Path(state_file)
        self.trade_log_path = Path(trade_log_file)
        self.price_feed = dict(price_feed or {})
        self.state = self._load_state(initial_cash)

    def _load_state(self, initial_cash: float) -> PaperBrokerState:
        if not self.state_path.exists():
            return PaperBrokerState(cash_balance=initial_cash)

        payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        positions = {
            symbol: PaperPosition(
                symbol=symbol,
                qty=float(data.get("qty", 0.0)),
                avg_price=float(data.get("avg_price", 0.0)),
                realized_pnl=float(data.get("realized_pnl", 0.0)),
            )
            for symbol, data in payload.get("positions", {}).items()
        }
        return PaperBrokerState(
            cash_balance=float(payload.get("cash_balance", initial_cash)),
            positions=positions,
            orders=list(payload.get("orders", [])),
        )

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "cash_balance": self.state.cash_balance,
            "positions": {
                symbol: {
                    "qty": position.qty,
                    "avg_price": position.avg_price,
                    "realized_pnl": position.realized_pnl,
                }
                for symbol, position in self.state.positions.items()
            },
            "orders": self.state.orders,
        }
        self.state_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _append_trade_log(self, event: dict[str, Any]) -> None:
        self.trade_log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.trade_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def set_market_price(self, symbol: str, price: float) -> None:
        if price <= 0:
            raise ValueError("price must be positive")
        self.price_feed[symbol] = float(price)

    def _resolve_price(self, symbol: str) -> float:
        if symbol not in self.price_feed:
            raise ValueError(f"Missing paper price for symbol '{symbol}'")
        price = float(self.price_feed[symbol])
        if price <= 0:
            raise ValueError(f"Invalid paper price for symbol '{symbol}'")
        return price

    def get_cash_balance(self) -> float:
        return self.state.cash_balance

    def get_positions(self) -> list[Position]:
        return [
            Position(
                symbol=position.symbol,
                qty=position.qty,
                avg_price=position.avg_price,
                realized_pnl=position.realized_pnl,
            )
            for position in self.state.positions.values()
            if position.qty > 0
        ]

    def get_open_orders(self) -> list[dict[str, Any]]:
        return [
            deepcopy(order)
            for order in self.state.orders
            if order.get("status") not in {"FILLED", "CANCELLED"}
        ]

    def _new_order_event(
        self,
        *,
        symbol: str,
        side: str,
        qty: float,
        price: float,
        status: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "order_id": uuid4().hex,
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "price": price,
            "status": status,
            "filled_qty": qty if status == "FILLED" else 0.0,
            "average_price": price if status == "FILLED" else 0.0,
            "metadata": metadata or {},
        }

    def _to_order_result(self, order: dict[str, Any]) -> OrderResult:
        return OrderResult(
            order_id=order["order_id"],
            symbol=order["symbol"],
            side=order["side"],
            qty=order["qty"],
            price=order["price"],
            status=order["status"],
            filled_qty=order["filled_qty"],
            average_price=order["average_price"],
            metadata=deepcopy(order.get("metadata", {})),
        )

    def place_market_buy(self, symbol: str, qty: float) -> OrderResult:
        if qty <= 0:
            raise ValueError("qty must be positive")

        price = self._resolve_price(symbol)
        notional = price * qty
        if notional > self.state.cash_balance:
            raise ValueError(
                f"Insufficient cash for BUY {symbol}: need {notional:.2f}, have {self.state.cash_balance:.2f}"
            )

        position = self.state.positions.get(symbol, PaperPosition(symbol=symbol))
        new_qty = position.qty + qty
        new_avg_price = (
            ((position.qty * position.avg_price) + notional) / new_qty
            if new_qty > 0
            else 0.0
        )
        position.qty = new_qty
        position.avg_price = new_avg_price
        self.state.positions[symbol] = position
        self.state.cash_balance -= notional

        order = self._new_order_event(symbol=symbol, side="BUY", qty=qty, price=price, status="FILLED")
        self.state.orders.append(order)
        self._save_state()
        self._append_trade_log({**order, "cash_balance": self.state.cash_balance})
        return self._to_order_result(order)

    def place_market_sell(self, symbol: str, qty: float) -> OrderResult:
        if qty <= 0:
            raise ValueError("qty must be positive")

        position = self.state.positions.get(symbol)
        if position is None or position.qty < qty:
            current_qty = 0.0 if position is None else position.qty
            raise ValueError(
                f"Insufficient position for SELL {symbol}: need {qty:.8f}, have {current_qty:.8f}"
            )

        price = self._resolve_price(symbol)
        notional = price * qty
        realized_pnl = (price - position.avg_price) * qty

        position.qty -= qty
        position.realized_pnl += realized_pnl
        if position.qty == 0:
            position.avg_price = 0.0
        self.state.cash_balance += notional

        if position.qty > 0:
            self.state.positions[symbol] = position
        else:
            self.state.positions.pop(symbol, None)

        order = self._new_order_event(
            symbol=symbol,
            side="SELL",
            qty=qty,
            price=price,
            status="FILLED",
            metadata={"realized_pnl": realized_pnl},
        )
        self.state.orders.append(order)
        self._save_state()
        self._append_trade_log({**order, "cash_balance": self.state.cash_balance})
        return self._to_order_result(order)

    def cancel_all_orders(self, symbol: str | None = None) -> list[str]:
        cancelled_ids: list[str] = []
        for order in self.state.orders:
            if order.get("status") in {"FILLED", "CANCELLED"}:
                continue
            if symbol is None or order["symbol"] == symbol:
                cancelled_ids.append(order["order_id"])
                cancel_event = {
                    **order,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "status": "CANCELLED",
                }
                self._append_trade_log(cancel_event)
                order["status"] = "CANCELLED"
        self._save_state()
        return cancelled_ids
