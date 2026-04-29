from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
import sqlite3

from intraday_auto_trading.models import (
    CapabilityStatus,
    MarketDataType,
    MinuteBar,
    OpeningImbalance,
    OptionQuote,
    ProviderCapabilities,
    ProviderCapability,
    SessionMetrics,
    SyncStatus,
)
from intraday_auto_trading.persistence.market_data_repository import SqliteMarketDataRepository
from intraday_auto_trading.services.market_data_sync import MarketDataSyncService


@dataclass(slots=True)
class FakeBatchGateway:
    provider_name: str = "fake"

    def probe_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=self.provider_name,
            bars_1m=ProviderCapability(MarketDataType.BARS_1M, CapabilityStatus.AVAILABLE),
            bars_15m_direct=ProviderCapability(MarketDataType.BARS_15M_DIRECT, CapabilityStatus.AVAILABLE),
            bars_15m_derived=ProviderCapability(MarketDataType.BARS_15M_DERIVED, CapabilityStatus.AVAILABLE),
            opening_imbalance=ProviderCapability(MarketDataType.OPENING_IMBALANCE, CapabilityStatus.AVAILABLE),
            options=ProviderCapability(MarketDataType.OPTIONS, CapabilityStatus.AVAILABLE),
        )

    def get_official_open(self, symbol: str, at_time: datetime) -> float:
        return 100.0

    def get_last_price(self, symbol: str, at_time: datetime) -> float:
        return 115.0

    def get_session_vwap(self, symbol: str, at_time: datetime) -> float:
        return 107.5

    def get_session_metrics(self, symbol: str, at_time: datetime) -> SessionMetrics | None:
        return SessionMetrics(
            symbol=symbol,
            timestamp=at_time,
            source=self.provider_name,
            official_open=100.0,
            last_price=115.0,
            session_vwap=107.5,
        )

    def get_minute_bars(self, symbol: str, start: datetime, end: datetime) -> list[MinuteBar]:
        return self.get_minute_bars_batch([symbol], start, end)[symbol]

    def get_direct_fifteen_minute_bars(self, symbol: str, start: datetime, end: datetime) -> list[MinuteBar]:
        return self.get_direct_fifteen_minute_bars_batch([symbol], start, end)[symbol]

    def get_opening_imbalance(self, symbol: str, trade_date: date) -> OpeningImbalance | None:
        return OpeningImbalance(
            symbol=symbol,
            trade_date=trade_date.isoformat(),
            source=self.provider_name,
            opening_imbalance_side="BUY",
            opening_imbalance_qty=1_000,
            paired_shares=800,
            indicative_open_price=100.2,
        )

    def get_option_quotes(self, symbol: str, at_time: datetime) -> list[OptionQuote]:
        return self.get_option_quotes_batch([symbol], at_time)[symbol]

    def get_minute_bars_batch(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
    ) -> dict[str, list[MinuteBar]]:
        payload: dict[str, list[MinuteBar]] = {}
        for symbol in symbols:
            payload[symbol] = [
                MinuteBar(
                    timestamp=start + timedelta(minutes=offset),
                    open=100.0 + offset,
                    high=101.0 + offset,
                    low=99.5 + offset,
                    close=100.5 + offset,
                    volume=1_000 + offset * 10,
                )
                for offset in range(16)
            ]
        return payload

    def get_direct_fifteen_minute_bars_batch(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
    ) -> dict[str, list[MinuteBar]]:
        payload: dict[str, list[MinuteBar]] = {}
        for symbol in symbols:
            payload[symbol] = [
                MinuteBar(
                    timestamp=start,
                    open=100.0,
                    high=114.0,
                    low=99.5,
                    close=114.5,
                    volume=15_000,
                ),
                MinuteBar(
                    timestamp=start + timedelta(minutes=15),
                    open=115.0,
                    high=116.0,
                    low=114.5,
                    close=115.5,
                    volume=2_000,
                ),
            ]
        return payload

    def get_option_quotes_batch(
        self,
        symbols: list[str],
        at_time: datetime,
    ) -> dict[str, list[OptionQuote]]:
        payload: dict[str, list[OptionQuote]] = {}
        for symbol in symbols:
            payload[symbol] = [
                OptionQuote(
                    symbol=symbol,
                    strike=100.0,
                    side="CALL",
                    bid=1.2,
                    ask=1.3,
                    last=1.25,
                    volume=100,
                    expiry="2026-04-17",
                    snapshot_time=at_time,
                )
            ]
        return payload


@dataclass(slots=True)
class FakeOptionFailureGateway(FakeBatchGateway):
    def get_option_quotes_batch(
        self,
        symbols: list[str],
        at_time: datetime,
    ) -> dict[str, list[OptionQuote]]:
        raise RuntimeError("option backend unavailable")


def _row_count(db_path: Path, table_name: str) -> int:
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()
    return int(row[0])


def test_market_data_sync_service_saves_multiple_data_types(tmp_path: Path) -> None:
    db_path = tmp_path / "market_data.sqlite"
    repository = SqliteMarketDataRepository(db_path)
    service = MarketDataSyncService(
        repository=repository,
        providers={"fake": FakeBatchGateway()},
        enabled_data_types=["bars", "opening_imbalance", "options"],
        enable_direct_15m=True,
        enable_derived_15m=True,
    )

    summary = service.sync_market_data(
        symbols=["SPY"],
        providers=["fake"],
        start=datetime(2026, 4, 15, 9, 30),
        end=datetime(2026, 4, 15, 9, 45),
    )

    statuses = {(result.data_type, result.status) for result in summary.results}

    assert (MarketDataType.BARS_1M, SyncStatus.SUCCESS) in statuses
    assert not any(result.data_type is MarketDataType.BARS_15M_DIRECT for result in summary.results)
    assert not any(result.data_type is MarketDataType.BARS_15M_DERIVED for result in summary.results)
    assert (MarketDataType.OPENING_IMBALANCE, SyncStatus.UNSUPPORTED) in statuses
    assert (MarketDataType.OPTIONS, SyncStatus.UNSUPPORTED) in statuses
    assert _row_count(db_path, "price_bars") == 16


def test_market_data_sync_service_keeps_successful_bars_when_options_fail(tmp_path: Path) -> None:
    db_path = tmp_path / "market_data.sqlite"
    repository = SqliteMarketDataRepository(db_path)
    service = MarketDataSyncService(
        repository=repository,
        providers={"fake": FakeOptionFailureGateway()},
        enabled_data_types=["bars", "options"],
        enable_direct_15m=True,
        enable_derived_15m=True,
    )

    summary = service.sync_market_data(
        symbols=["SPY"],
        providers=["fake"],
        start=datetime(2026, 4, 15, 9, 30),
        end=datetime(2026, 4, 15, 9, 45),
    )

    by_data_type = {result.data_type: result for result in summary.results}

    assert by_data_type[MarketDataType.BARS_1M].status is SyncStatus.SUCCESS
    assert by_data_type[MarketDataType.OPTIONS].status is SyncStatus.UNSUPPORTED
    assert _row_count(db_path, "price_bars") == 16
