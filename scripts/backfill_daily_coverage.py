"""One-time migration: backfill daily_coverage from existing price_bars.

Run once after deploying the daily_coverage schema change:

    PYTHONPATH=src python3 scripts/backfill_daily_coverage.py

For each (symbol, bar_size, trade_date) already in price_bars, counts the
distinct timestamps (deduplicated across sources) and writes a daily_coverage
row. Rows that already exist in daily_coverage are left unchanged (uses INSERT
OR IGNORE via save_daily_coverage's upsert — existing is_complete state wins).
"""
from __future__ import annotations

import sys
from pathlib import Path

# Allow running from repo root without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from intraday_auto_trading.config import load_settings
from intraday_auto_trading.models import DailyCoverage
from intraday_auto_trading.persistence.market_data_repository import SqliteMarketDataRepository
from intraday_auto_trading.persistence.sqlite_base import connect_sqlite
from intraday_auto_trading.services.bar_data_service import _expected_bars


def backfill(repo: SqliteMarketDataRepository) -> None:
    with connect_sqlite(repo.db_path) as conn:
        rows = conn.execute(
            """
            SELECT symbol, bar_size,
                   substr(ts, 1, 10) AS trade_date,
                   COUNT(DISTINCT ts)  AS actual_bars
            FROM price_bars
            GROUP BY symbol, bar_size, substr(ts, 1, 10)
            ORDER BY symbol, bar_size, trade_date
            """
        ).fetchall()

    if not rows:
        print("No price_bars rows found — nothing to backfill.")
        return

    inserted = 0
    skipped = 0
    for row in rows:
        symbol = row["symbol"]
        bar_size = row["bar_size"]
        trade_date = row["trade_date"]
        actual_bars = row["actual_bars"]
        expected = _expected_bars(bar_size)
        is_complete = actual_bars >= expected

        # Check if coverage already exists — don't overwrite manual entries
        existing = repo.load_daily_coverage(symbol, bar_size, trade_date)
        if existing is not None:
            skipped += 1
            continue

        repo.save_daily_coverage(DailyCoverage(
            symbol=symbol,
            bar_size=bar_size,
            trade_date=trade_date,
            source="backfill",
            expected_bars=expected,
            actual_bars=actual_bars,
            is_complete=is_complete,
        ))
        inserted += 1

    print(f"Backfill complete: {inserted} inserted, {skipped} skipped (already existed).")


def main() -> None:
    settings = load_settings("config/settings.toml")
    repo = SqliteMarketDataRepository(settings.data.market_data_db)
    print(f"DB: {settings.data.market_data_db}")
    backfill(repo)


if __name__ == "__main__":
    main()
