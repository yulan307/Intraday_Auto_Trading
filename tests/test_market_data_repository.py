from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sqlite3

from intraday_auto_trading.models import (
    MinuteBar,
    OptionQuote,
    Regime,
    SessionMetrics,
    SymbolInfo,
    TrendSnapshot,
)
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
            timestamp=datetime(2026, 4, 15, 9, 30),
            open=100.0,
            high=101.0,
            low=99.8,
            close=100.5,
            volume=1_000,
        ),
        MinuteBar(
            timestamp=datetime(2026, 4, 15, 9, 31),
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
        datetime(2026, 4, 15, 9, 30),
        datetime(2026, 4, 15, 9, 31),
    )

    assert _row_count(db_path, "price_bars") == 2
    assert [bar.close for bar in loaded] == [100.5, 101.0]


def test_repository_persists_symbol_metrics_quotes_and_trend_snapshot(tmp_path: Path) -> None:
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
    repository.save_session_metrics(
        SessionMetrics(
            symbol="QQQ",
            timestamp=datetime(2026, 4, 15, 10, 0),
            source="ibkr",
            official_open=450.0,
            last_price=452.5,
            session_vwap=451.2,
        )
    )
    repository.save_option_quotes(
        [
            OptionQuote(
                symbol="QQQ",
                strike=450.0,
                side="CALL",
                bid=4.1,
                ask=4.3,
                last=4.2,
                volume=150,
                iv=0.22,
                delta=0.51,
                gamma=0.04,
                expiry="2026-04-17",
                snapshot_time=datetime(2026, 4, 15, 10, 0),
            )
        ],
        source="moomoo",
    )
    repository.save_trend_snapshot(
        TrendSnapshot(
            symbol="QQQ",
            eval_time=datetime(2026, 4, 15, 10, 0),
            source="strategy",
            regime=Regime.RANGE_TRACK_15M,
            score=0.64,
            reason="价格围绕 VWAP 震荡",
            official_open=450.0,
            last_price=452.5,
            session_vwap=451.2,
        )
    )

    assert _row_count(db_path, "symbols") == 1
    assert _row_count(db_path, "session_metrics") == 1
    assert _row_count(db_path, "option_contracts") == 1
    assert _row_count(db_path, "option_quotes") == 1
    assert _row_count(db_path, "trend_snapshots") == 1
