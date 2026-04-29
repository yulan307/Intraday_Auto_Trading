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

CREATE TABLE IF NOT EXISTS bar_request_log (
    symbol TEXT NOT NULL,
    bar_size TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    source TEXT NOT NULL,
    request_start_ts TEXT NOT NULL,
    request_end_ts TEXT NOT NULL,
    status TEXT NOT NULL,
    expected_bars INTEGER NOT NULL,
    actual_bars INTEGER NOT NULL,
    message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (symbol, bar_size, trade_date)
);

CREATE INDEX IF NOT EXISTS idx_price_bars_symbol_ts
ON price_bars (symbol, bar_size, ts);

CREATE INDEX IF NOT EXISTS idx_bar_request_log_symbol_bar_size
ON bar_request_log (symbol, bar_size, trade_date);
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
