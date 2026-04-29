from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sqlite3

from intraday_auto_trading.models import BarRequestLog, MinuteBar, SymbolInfo
from intraday_auto_trading.persistence.market_data_repository import SqliteMarketDataRepository


def _row_count(db_path: Path, table_name: str) -> int:
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()
    return int(row[0])


def test_repository_initializes_schema_and_persists_bars(tmp_path: Path) -> None:
    db_path = tmp_path / "market_data.sqlite"
    repository = SqliteMarketDataRepository(db_path)

    bars = [
        MinuteBar(
            timestamp=datetime(2026, 4, 15, 13, 30),
            open=100.0,
            high=101.0,
            low=99.8,
            close=100.5,
            volume=1_000,
        ),
        MinuteBar(
            timestamp=datetime(2026, 4, 15, 13, 31),
            open=100.5,
            high=101.2,
            low=100.1,
            close=101.0,
            volume=1_200,
        ),
    ]

    repository.save_price_bars("SPY", "1m", bars, source="ibkr")
    repository.save_price_bars("SPY", "1m", bars, source="ibkr")

    loaded = repository.load_price_bars(
        "SPY",
        "1m",
        datetime(2026, 4, 15, 13, 30),
        datetime(2026, 4, 15, 13, 31),
    )

    assert _row_count(db_path, "price_bars") == 2
    assert [bar.close for bar in loaded] == [100.5, 101.0]


def test_repository_initializes_bar_only_schema_and_request_log(tmp_path: Path) -> None:
    db_path = tmp_path / "market_data.sqlite"
    repository = SqliteMarketDataRepository(db_path)

    repository.upsert_symbol(
        SymbolInfo(
            symbol="QQQ",
            name="Invesco QQQ Trust",
            exchange="NASDAQ",
            asset_type="ETF",
        )
    )
    repository.save_bar_request_log(
        BarRequestLog(
            symbol="QQQ",
            bar_size="1m",
            trade_date="2026-04-15",
            source="ibkr",
            request_start_ts=datetime(2026, 4, 15, 13, 30),
            request_end_ts=datetime(2026, 4, 15, 20, 0),
            status="success",
            expected_bars=390,
            actual_bars=390,
        )
    )
    loaded = repository.load_bar_request_log("QQQ", "1m", "2026-04-15")

    assert _row_count(db_path, "symbols") == 1
    assert _row_count(db_path, "bar_request_log") == 1
    assert loaded is not None
    assert loaded.status == "success"
    assert loaded.actual_bars == 390

    with sqlite3.connect(db_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {"symbols", "price_bars", "bar_request_log"}.issubset(tables)
    assert "session_metrics" not in tables
    assert "option_quotes" not in tables
