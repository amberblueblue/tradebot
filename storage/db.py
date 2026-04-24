from __future__ import annotations

import sqlite3
from pathlib import Path

from storage.schema import CREATE_INDEXES, CREATE_TABLES


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "tradebot.sqlite3"


def ensure_data_dir(db_path: Path = DEFAULT_DB_PATH) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)


def get_connection(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    path = Path(db_path)
    ensure_data_dir(path)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database(db_path: str | Path = DEFAULT_DB_PATH) -> Path:
    path = Path(db_path)
    with get_connection(path) as connection:
        for statement in CREATE_TABLES:
            connection.execute(statement)
        for statement in CREATE_INDEXES:
            connection.execute(statement)
        connection.commit()
    return path


def verify_database(db_path: str | Path = DEFAULT_DB_PATH) -> tuple[str, ...]:
    path = initialize_database(db_path)
    with get_connection(path) as connection:
        rows = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
              AND name IN (
                  'trades',
                  'position_snapshots',
                  'equity_snapshots',
                  'symbol_pnl_snapshots'
              )
            ORDER BY name
            """
        ).fetchall()
    return tuple(row["name"] for row in rows)


if __name__ == "__main__":
    created_tables = verify_database()
    print(f"SQLite database ready: {DEFAULT_DB_PATH}")
    print("Tables: " + ", ".join(created_tables))

