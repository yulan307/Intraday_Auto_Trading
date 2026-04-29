from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from intraday_auto_trading.models import MinuteBar
from intraday_auto_trading.persistence.market_data_repository import SqliteMarketDataRepository
from intraday_auto_trading.services.symbol_management_data_fetcher import SymbolManagementDataFetcher
from intraday_auto_trading.symbol_manager import (
    SymbolGroupRegistry,
    SymbolGroupSettings,
)


def _bars_for_day(trade_date: date, count: int = 2) -> list[MinuteBar]:
    start = datetime(trade_date.year, trade_date.month, trade_date.day, 13, 30)
    return [
        MinuteBar(
            timestamp=start + timedelta(minutes=i),
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=1000.0,
        )
        for i in range(count)
    ]


class StubBarDataService:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str, date, date, list[str] | None, bool]] = []

    def get_bars(
        self,
        symbols: list[str],
        bar_size: str,
        start_date: date,
        end_date: date,
        source_order: list[str] | None = None,
        force_refresh: bool = False,
    ) -> dict[str, list[MinuteBar]]:
        self.calls.append((symbols, bar_size, start_date, end_date, source_order, force_refresh))
        if bar_size == "1m":
            return {
                symbol: _bars_for_day(start_date) + _bars_for_day(end_date)
                for symbol in symbols
            }
        if bar_size == "1d":
            return {
                symbol: [
                    _bars_for_day(start_date, 1)[0],
                    _bars_for_day(end_date, 1)[0],
                ]
                for symbol in symbols
            }
        return {symbol: [] for symbol in symbols}


def test_fetch_all_symbol_pool_data_reuses_services_for_all_groups(tmp_path: Path) -> None:
    registry = SymbolGroupRegistry(
        groups={
            "core": SymbolGroupSettings("core", ["SPY", "QQQ"], 100.0),
            "growth": SymbolGroupSettings("growth", ["QQQ", "NVDA"], 100.0),
        },
        default_group="core",
    )
    bar_service = StubBarDataService()
    fetcher = SymbolManagementDataFetcher(
        repository=SqliteMarketDataRepository(tmp_path / "market_data.db"),
        bar_data_service=bar_service,  # type: ignore[arg-type]
        session_gateways={},
        option_gateways={},
    )

    summary = fetcher.fetch_all_symbol_pool_data(
        symbol_groups=registry,
        start_date=date(2026, 4, 1),
        end_date=date(2026, 4, 2),
    )

    assert summary.symbols == ["SPY", "QQQ", "NVDA"]
    assert [call[1] for call in bar_service.calls] == ["1m", "1d"]
    assert summary.one_minute_bar_counts == {"SPY": 4, "QQQ": 4, "NVDA": 4}
    assert summary.daily_bar_counts == {"SPY": 2, "QQQ": 2, "NVDA": 2}
    assert summary.bar_providers == ["ibkr"]
    assert [call[4] for call in bar_service.calls] == [["ibkr"], ["ibkr"]]
    assert summary.errors == []


def test_fetch_all_symbol_pool_data_can_restrict_bar_providers(tmp_path: Path) -> None:
    registry = SymbolGroupRegistry(
        groups={
            "core": SymbolGroupSettings("core", ["SPY"], 100.0),
        },
        default_group="core",
    )
    bar_service = StubBarDataService()
    fetcher = SymbolManagementDataFetcher(
        repository=SqliteMarketDataRepository(tmp_path / "market_data.db"),
        bar_data_service=bar_service,  # type: ignore[arg-type]
        session_gateways={},
        option_gateways={},
    )

    summary = fetcher.fetch_all_symbol_pool_data(
        symbol_groups=registry,
        start_date=date(2026, 2, 1),
        end_date=date(2026, 4, 27),
        bar_providers=["IBKR"],
        force_refresh=True,
    )

    assert summary.bar_providers == ["ibkr"]
    assert [call[4] for call in bar_service.calls] == [["ibkr"], ["ibkr"]]
    assert [call[5] for call in bar_service.calls] == [True, True]


def test_fetch_all_symbol_pool_data_rejects_non_ibkr_bar_provider(tmp_path: Path) -> None:
    registry = SymbolGroupRegistry(
        groups={
            "core": SymbolGroupSettings("core", ["SPY"], 100.0),
        },
        default_group="core",
    )
    fetcher = SymbolManagementDataFetcher(
        repository=SqliteMarketDataRepository(tmp_path / "market_data.db"),
        bar_data_service=StubBarDataService(),  # type: ignore[arg-type]
        session_gateways={},
        option_gateways={},
    )

    with pytest.raises(ValueError, match="fixed to IB Gateway"):
        fetcher.fetch_all_symbol_pool_data(
            symbol_groups=registry,
            start_date=date(2026, 2, 1),
            end_date=date(2026, 4, 27),
            bar_providers=["yfinance"],
        )
