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

CREATE TABLE IF NOT EXISTS daily_coverage (
    symbol        TEXT NOT NULL,
    bar_size      TEXT NOT NULL,
    trade_date    TEXT NOT NULL,
    source        TEXT NOT NULL,
    expected_bars INTEGER NOT NULL,
    actual_bars   INTEGER NOT NULL,
    is_complete   INTEGER NOT NULL DEFAULT 0,
    updated_at    TEXT NOT NULL,
    PRIMARY KEY (symbol, bar_size, trade_date)
);

CREATE INDEX IF NOT EXISTS idx_daily_coverage_symbol_bar_size
ON daily_coverage (symbol, bar_size, trade_date);
"""


BACKTEST_ACCOUNT_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS backtest_runs (
    run_id TEXT PRIMARY KEY,
    name TEXT,
    symbols TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    initial_cash REAL NOT NULL,
    config_snapshot TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS backtest_orders (
    order_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,
    strategy TEXT,
    total_qty REAL NOT NULL,
    filled_qty REAL NOT NULL DEFAULT 0,
    limit_price REAL,
    avg_fill_price REAL,
    status TEXT NOT NULL,
    placed_at TEXT NOT NULL,
    filled_at TEXT,
    cancelled_at TEXT,
    PRIMARY KEY (run_id, order_id),
    FOREIGN KEY (run_id) REFERENCES backtest_runs (run_id)
);

CREATE INDEX IF NOT EXISTS idx_backtest_orders_run_symbol
ON backtest_orders (run_id, symbol);
"""


def create_market_data_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(MARKET_DATA_SCHEMA_SQL)


def create_backtest_account_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(BACKTEST_ACCOUNT_SCHEMA_SQL)

