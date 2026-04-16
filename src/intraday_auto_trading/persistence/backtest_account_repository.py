from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from intraday_auto_trading.interfaces.repositories import BacktestAccountRepository
from intraday_auto_trading.models import Order
from intraday_auto_trading.persistence.schema import create_backtest_account_schema
from intraday_auto_trading.persistence.sqlite_base import connect_sqlite, to_storage_ts


class SqliteBacktestAccountRepository:
    """SQLite-backed persistence for backtest runs and their orders."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.initialize()

    def initialize(self) -> None:
        with connect_sqlite(self.db_path) as connection:
            create_backtest_account_schema(connection)

    def create_run(
        self,
        run_id: str,
        name: str,
        symbols: list[str],
        start_date: str,
        end_date: str,
        initial_cash: float,
        config_snapshot: str,
    ) -> None:
        with connect_sqlite(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO backtest_runs (
                    run_id, name, symbols, start_date, end_date,
                    initial_cash, config_snapshot, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    name = excluded.name,
                    symbols = excluded.symbols,
                    start_date = excluded.start_date,
                    end_date = excluded.end_date,
                    initial_cash = excluded.initial_cash,
                    config_snapshot = excluded.config_snapshot
                """,
                (
                    run_id,
                    name,
                    json.dumps(symbols),
                    start_date,
                    end_date,
                    initial_cash,
                    config_snapshot,
                    to_storage_ts(datetime.utcnow()),
                ),
            )

    def save_order(self, run_id: str, order: Order, strategy: str) -> None:
        filled_at = (
            to_storage_ts(order.timestamp)
            if order.status == "Filled"
            else None
        )
        cancelled_at = (
            to_storage_ts(order.timestamp)
            if order.status == "Cancelled"
            else None
        )
        with connect_sqlite(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO backtest_orders (
                    order_id, run_id, symbol, action, strategy,
                    total_qty, filled_qty, limit_price, avg_fill_price,
                    status, placed_at, filled_at, cancelled_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, order_id) DO UPDATE SET
                    filled_qty = excluded.filled_qty,
                    avg_fill_price = excluded.avg_fill_price,
                    status = excluded.status,
                    filled_at = excluded.filled_at,
                    cancelled_at = excluded.cancelled_at
                """,
                (
                    order.broker_order_id,
                    run_id,
                    order.symbol,
                    order.action,
                    strategy,
                    order.total_qty,
                    order.filled_qty,
                    order.limit_price,
                    order.avg_fill_price,
                    order.status,
                    to_storage_ts(order.timestamp),
                    filled_at,
                    cancelled_at,
                ),
            )

    def load_orders(self, run_id: str) -> list[Order]:
        with connect_sqlite(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT order_id, symbol, action, total_qty, filled_qty,
                       limit_price, avg_fill_price, status, placed_at
                FROM backtest_orders
                WHERE run_id = ?
                ORDER BY placed_at ASC
                """,
                (run_id,),
            ).fetchall()

        return [
            Order(
                broker_order_id=row["order_id"],
                account_id="VIRTUAL",
                symbol=row["symbol"],
                action=row["action"],
                total_qty=row["total_qty"],
                filled_qty=row["filled_qty"],
                remaining_qty=row["total_qty"] - row["filled_qty"],
                status=row["status"],
                limit_price=row["limit_price"],
                avg_fill_price=row["avg_fill_price"] or 0.0,
                timestamp=datetime.fromisoformat(row["placed_at"]),
            )
            for row in rows
        ]
