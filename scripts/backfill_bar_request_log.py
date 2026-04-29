"""Backfill bar_request_log from existing price_bars rows.

This helper is mainly for ad-hoc recovery. The normal symbol-pool fetch path
writes request-log rows as it fetches bars.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from intraday_auto_trading.config import load_settings
from intraday_auto_trading.models import BarRequestLog
from intraday_auto_trading.persistence.market_data_repository import SqliteMarketDataRepository


EXPECTED_BARS = {"1m": 390, "15m": 26, "1d": 1}


def main() -> None:
    settings = load_settings("config/settings.toml", symbol_groups_path="config/symbol_group.toml")
    repo = SqliteMarketDataRepository(settings.data.market_data_db)

    grouped: dict[tuple[str, str, str, str], list[str]] = defaultdict(list)
    import sqlite3

    with sqlite3.connect(repo.db_path) as connection:
        rows = connection.execute(
            """
            SELECT symbol, bar_size, source, substr(ts, 1, 10) AS trade_date, ts
            FROM price_bars
            ORDER BY symbol, bar_size, source, ts
            """
        ).fetchall()
    for symbol, bar_size, source, trade_date, ts in rows:
        grouped[(symbol, bar_size, source, trade_date)].append(ts)

    written = 0
    for (symbol, bar_size, source, trade_date), timestamps in grouped.items():
        expected = EXPECTED_BARS.get(bar_size, 1)
        actual = len(set(timestamps))
        status = "success" if actual >= expected else "partial"
        repo.save_bar_request_log(
            BarRequestLog(
                symbol=symbol,
                bar_size=bar_size,
                trade_date=trade_date,
                source=source,
                request_start_ts=datetime.fromisoformat(min(timestamps)),
                request_end_ts=datetime.fromisoformat(max(timestamps)),
                status=status,
                expected_bars=expected,
                actual_bars=actual,
                message=None if status == "success" else f"Backfilled {actual} of {expected} bars.",
            )
        )
        written += 1

    print(f"Backfilled {written} bar_request_log rows.")


if __name__ == "__main__":
    main()
