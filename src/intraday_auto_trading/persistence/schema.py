from __future__ import annotations

import sqlite3


MARKET_DATA_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS symbols (
    symbol TEXT PRIMARY KEY,
    name TEXT,
    exchange TEXT,
    asset_type TEXT,
    currency TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS price_bars (
    symbol TEXT NOT NULL,
    bar_size TEXT NOT NULL,
    ts TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (symbol, bar_size, ts, source)
);

CREATE TABLE IF NOT EXISTS session_metrics (
    symbol TEXT NOT NULL,
    ts TEXT NOT NULL,
    official_open REAL,
    last_price REAL,
    session_vwap REAL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (symbol, ts, source)
);

CREATE TABLE IF NOT EXISTS opening_imbalance (
    symbol TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    opening_imbalance_side TEXT,
    opening_imbalance_qty REAL,
    paired_shares REAL,
    indicative_open_price REAL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (symbol, trade_date, source)
);

CREATE TABLE IF NOT EXISTS option_contracts (
    contract_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    expiry TEXT NOT NULL,
    strike REAL NOT NULL,
    option_type TEXT NOT NULL,
    exchange TEXT,
    multiplier INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS option_quotes (
    contract_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    snapshot_ts TEXT NOT NULL,
    bid REAL,
    ask REAL,
    bid_size INTEGER,
    ask_size INTEGER,
    last REAL,
    volume INTEGER,
    iv REAL,
    delta REAL,
    gamma REAL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (contract_id, snapshot_ts, source),
    FOREIGN KEY (contract_id) REFERENCES option_contracts (contract_id)
);

CREATE TABLE IF NOT EXISTS trend_snapshots (
    symbol TEXT NOT NULL,
    eval_time TEXT NOT NULL,
    regime TEXT NOT NULL,
    score REAL NOT NULL,
    reason TEXT NOT NULL,
    official_open REAL,
    last_price REAL,
    session_vwap REAL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (symbol, eval_time, source)
);

CREATE INDEX IF NOT EXISTS idx_price_bars_symbol_ts
ON price_bars (symbol, bar_size, ts);

CREATE INDEX IF NOT EXISTS idx_option_quotes_symbol_ts
ON option_quotes (symbol, snapshot_ts);
"""


def create_market_data_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(MARKET_DATA_SCHEMA_SQL)

