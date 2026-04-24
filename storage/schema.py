from __future__ import annotations


CREATE_TRADES_TABLE = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity REAL NOT NULL,
    price REAL NOT NULL,
    amount REAL NOT NULL,
    fee REAL NOT NULL DEFAULT 0,
    realized_pnl REAL NOT NULL DEFAULT 0,
    mode TEXT NOT NULL
)
"""


CREATE_POSITION_SNAPSHOTS_TABLE = """
CREATE TABLE IF NOT EXISTS position_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    quantity REAL NOT NULL,
    avg_price REAL NOT NULL,
    current_price REAL NOT NULL,
    market_value REAL NOT NULL,
    unrealized_pnl REAL NOT NULL,
    mode TEXT NOT NULL
)
"""


CREATE_EQUITY_SNAPSHOTS_TABLE = """
CREATE TABLE IF NOT EXISTS equity_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    total_equity REAL NOT NULL,
    cash REAL NOT NULL,
    position_value REAL NOT NULL,
    realized_pnl REAL NOT NULL,
    unrealized_pnl REAL NOT NULL,
    mode TEXT NOT NULL
)
"""


CREATE_SYMBOL_PNL_SNAPSHOTS_TABLE = """
CREATE TABLE IF NOT EXISTS symbol_pnl_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    realized_pnl REAL NOT NULL,
    unrealized_pnl REAL NOT NULL,
    total_pnl REAL NOT NULL,
    mode TEXT NOT NULL
)
"""


CREATE_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_trades_symbol_timestamp ON trades(symbol, timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_position_snapshots_symbol_timestamp ON position_snapshots(symbol, timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_equity_snapshots_timestamp ON equity_snapshots(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_symbol_pnl_snapshots_symbol_timestamp ON symbol_pnl_snapshots(symbol, timestamp)",
)


CREATE_TABLES = (
    CREATE_TRADES_TABLE,
    CREATE_POSITION_SNAPSHOTS_TABLE,
    CREATE_EQUITY_SNAPSHOTS_TABLE,
    CREATE_SYMBOL_PNL_SNAPSHOTS_TABLE,
)

