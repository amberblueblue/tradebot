from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from storage.db import DEFAULT_DB_PATH, get_connection, initialize_database


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StorageRepository:
    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        initialize_database(self.db_path)

    def record_trade(
        self,
        *,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        amount: float | None = None,
        fee: float = 0.0,
        realized_pnl: float = 0.0,
        mode: str = "paper",
        timestamp: str | None = None,
    ) -> int:
        trade_amount = float(amount) if amount is not None else float(quantity) * float(price)
        with get_connection(self.db_path) as connection:
            cursor = connection.execute(
                """
                INSERT INTO trades (
                    timestamp, symbol, side, quantity, price, amount, fee, realized_pnl, mode
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    timestamp or _utc_now(),
                    symbol,
                    side,
                    float(quantity),
                    float(price),
                    trade_amount,
                    float(fee),
                    float(realized_pnl),
                    mode,
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def record_position_snapshot(
        self,
        *,
        symbol: str,
        quantity: float,
        avg_price: float,
        current_price: float,
        market_value: float | None = None,
        unrealized_pnl: float | None = None,
        mode: str = "paper",
        timestamp: str | None = None,
    ) -> int:
        value = float(market_value) if market_value is not None else float(quantity) * float(current_price)
        pnl = (
            float(unrealized_pnl)
            if unrealized_pnl is not None
            else (float(current_price) - float(avg_price)) * float(quantity)
        )
        with get_connection(self.db_path) as connection:
            cursor = connection.execute(
                """
                INSERT INTO position_snapshots (
                    timestamp, symbol, quantity, avg_price, current_price, market_value,
                    unrealized_pnl, mode
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    timestamp or _utc_now(),
                    symbol,
                    float(quantity),
                    float(avg_price),
                    float(current_price),
                    value,
                    pnl,
                    mode,
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def record_equity_snapshot(
        self,
        *,
        total_equity: float,
        cash: float,
        position_value: float,
        realized_pnl: float,
        unrealized_pnl: float,
        mode: str = "paper",
        timestamp: str | None = None,
    ) -> int:
        with get_connection(self.db_path) as connection:
            cursor = connection.execute(
                """
                INSERT INTO equity_snapshots (
                    timestamp, total_equity, cash, position_value, realized_pnl,
                    unrealized_pnl, mode
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    timestamp or _utc_now(),
                    float(total_equity),
                    float(cash),
                    float(position_value),
                    float(realized_pnl),
                    float(unrealized_pnl),
                    mode,
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def record_symbol_pnl_snapshot(
        self,
        *,
        symbol: str,
        realized_pnl: float,
        unrealized_pnl: float,
        total_pnl: float | None = None,
        mode: str = "paper",
        timestamp: str | None = None,
    ) -> int:
        total = float(total_pnl) if total_pnl is not None else float(realized_pnl) + float(unrealized_pnl)
        with get_connection(self.db_path) as connection:
            cursor = connection.execute(
                """
                INSERT INTO symbol_pnl_snapshots (
                    timestamp, symbol, realized_pnl, unrealized_pnl, total_pnl, mode
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    timestamp or _utc_now(),
                    symbol,
                    float(realized_pnl),
                    float(unrealized_pnl),
                    total,
                    mode,
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def get_latest_position_snapshots(self, *, mode: str = "paper") -> dict[str, dict]:
        with get_connection(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT ps.*
                FROM position_snapshots ps
                INNER JOIN (
                    SELECT symbol, MAX(id) AS max_id
                    FROM position_snapshots
                    WHERE mode = ?
                    GROUP BY symbol
                ) latest
                    ON ps.symbol = latest.symbol
                   AND ps.id = latest.max_id
                ORDER BY ps.symbol
                """,
                (mode,),
            ).fetchall()
        return {str(row["symbol"]): dict(row) for row in rows}

    def get_equity_curve(self, *, mode: str = "paper", limit: int = 500) -> list[dict]:
        with get_connection(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT
                    timestamp,
                    total_equity,
                    cash,
                    position_value,
                    realized_pnl,
                    unrealized_pnl,
                    mode
                FROM equity_snapshots
                WHERE mode = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (mode, int(limit)),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def get_latest_equity_snapshot(self, *, mode: str = "paper") -> dict | None:
        with get_connection(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT
                    timestamp,
                    total_equity,
                    cash,
                    position_value,
                    realized_pnl,
                    unrealized_pnl,
                    mode
                FROM equity_snapshots
                WHERE mode = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (mode,),
            ).fetchone()
        return dict(row) if row is not None else None

    def get_symbol_pnl_curve(self, *, symbol: str, mode: str = "paper", limit: int = 500) -> list[dict]:
        with get_connection(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT
                    timestamp,
                    symbol,
                    realized_pnl,
                    unrealized_pnl,
                    total_pnl,
                    mode
                FROM symbol_pnl_snapshots
                WHERE mode = ?
                  AND symbol = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (mode, symbol, int(limit)),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]


def create_repository(db_path: str | Path = DEFAULT_DB_PATH) -> StorageRepository:
    return StorageRepository(db_path)
