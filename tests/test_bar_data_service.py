"""Unit tests for BarDataService.

Scenarios:
- is_complete=True in coverage → DB bars returned, gateways never called
- is_complete=False, history date → history_source_order tried, bars fetched + coverage updated
- All gateways return empty → source="none", actual_bars=0, is_complete=True
- Partial fetch (< expected bars) → is_complete=False saved
- Multi-day multi-symbol: returns correct bars merged per symbol
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Sequence
import tempfile

import pytest

from intraday_auto_trading.models import (
    CapabilityStatus,
    DailyCoverage,
    MarketDataType,
    MinuteBar,
    ProviderCapabilities,
    ProviderCapability,
    SymbolInfo,
)
from intraday_auto_trading.persistence.market_data_repository import SqliteMarketDataRepository
from intraday_auto_trading.services.bar_data_service import BarDataService
from intraday_auto_trading.services.data_fetch_policy import DataFetchPolicy


# ---------------------------------------------------------------------------
# Stub gateways
# ---------------------------------------------------------------------------

class _FakeCapabilities:
    """Minimal ProviderCapabilities stub."""
    def __init__(self, available: bool) -> None:
        status = CapabilityStatus.AVAILABLE if available else CapabilityStatus.UNAVAILABLE
        cap = ProviderCapability(data_type=MarketDataType.BARS_1M, status=status)
        self.bars_1m = cap
        self.bars_15m_direct = ProviderCapability(
            data_type=MarketDataType.BARS_15M_DIRECT,
            status=CapabilityStatus.UNAVAILABLE,
        )


class StubGateway:
    """Gateway that returns a fixed set of bars on request."""

    def __init__(self, bars: list[MinuteBar], available: bool = True) -> None:
        self._bars = bars
        self._available = available
        self.call_count = 0

    def probe_capabilities(self) -> _FakeCapabilities:
        return _FakeCapabilities(self._available)

    def get_minute_bars(self, symbol: str, start: datetime, end: datetime) -> list[MinuteBar]:
        self.call_count += 1
        return list(self._bars)


class StubYfinance:
    """Yfinance stub."""

    def __init__(self, bars: list[MinuteBar]) -> None:
        self._bars = bars
        self.call_count = 0

    # BarDataService checks for yfinance_gateway attribute via _fetch_from_yfinance
    def get_minute_bars(self, symbol: str, start: datetime, end: datetime) -> list[MinuteBar]:
        self.call_count += 1
        return list(self._bars)

    def get_direct_fifteen_minute_bars(self, symbol: str, start: datetime, end: datetime) -> list[MinuteBar]:
        self.call_count += 1
        return []


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_bars(trade_date: date, n: int = 390, price: float = 100.0) -> list[MinuteBar]:
    """Build n 1-minute bars starting at 13:30 UTC (= 9:30 ET)."""
    base = datetime(trade_date.year, trade_date.month, trade_date.day, 13, 30)
    return [
        MinuteBar(
            timestamp=base + timedelta(minutes=i),
            open=price,
            high=price + 0.1,
            low=price - 0.1,
            close=price,
            volume=1000.0,
        )
        for i in range(n)
    ]


@pytest.fixture
def repo(tmp_path: Path) -> SqliteMarketDataRepository:
    return SqliteMarketDataRepository(tmp_path / "test.db")


def _service(
    repo: SqliteMarketDataRepository,
    ibkr_bars: list[MinuteBar] | None = None,
    yfinance_bars: list[MinuteBar] | None = None,
    ibkr_available: bool = True,
) -> BarDataService:
    ibkr_gw = StubGateway(ibkr_bars or [], available=ibkr_available) if ibkr_bars is not None else None
    yf_gw = StubYfinance(yfinance_bars or [])
    policy = DataFetchPolicy(
        db_source_priority=["ibkr", "yfinance"],
        live_source_order=["ibkr"],
        history_source_order=["yfinance", "ibkr"],
    )
    return BarDataService(
        repository=repo,
        policy=policy,
        ibkr_gateway=ibkr_gw,
        moomoo_gateway=None,
        yfinance_gateway=yf_gw,
        exchange_timezone="America/New_York",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

# Past date well before today so history_source_order is used
HIST_DATE = date(2026, 4, 14)  # Monday


def test_complete_coverage_uses_db_no_gateway_call(repo: SqliteMarketDataRepository) -> None:
    """If daily_coverage says is_complete=True, bars come from DB, gateway not called."""
    bars = _make_bars(HIST_DATE)
    repo.upsert_symbol(SymbolInfo(symbol="SPY"))
    repo.save_price_bars("SPY", "1m", bars, "yfinance")
    repo.save_daily_coverage(DailyCoverage(
        symbol="SPY", bar_size="1m", trade_date=HIST_DATE.isoformat(),
        source="yfinance", expected_bars=390, actual_bars=390, is_complete=True,
    ))

    yf_stub = StubYfinance([])
    svc = BarDataService(
        repository=repo,
        policy=DataFetchPolicy(),
        ibkr_gateway=None,
        moomoo_gateway=None,
        yfinance_gateway=yf_stub,
        exchange_timezone="America/New_York",
    )

    result = svc.get_bars(["SPY"], "1m", HIST_DATE, HIST_DATE)
    assert len(result["SPY"]) == 390
    assert yf_stub.call_count == 0


def test_incomplete_coverage_fetches_from_history_source(repo: SqliteMarketDataRepository) -> None:
    """When coverage is missing, history source order is used for past dates."""
    bars = _make_bars(HIST_DATE)
    svc = _service(repo, yfinance_bars=bars)

    result = svc.get_bars(["SPY"], "1m", HIST_DATE, HIST_DATE)

    assert len(result["SPY"]) == 390
    # Coverage should now be written
    cov = repo.load_daily_coverage("SPY", "1m", HIST_DATE.isoformat())
    assert cov is not None
    assert cov.is_complete is True
    assert cov.actual_bars == 390
    assert cov.source == "yfinance"


def test_all_sources_empty_marks_complete_confirmed_no_data(repo: SqliteMarketDataRepository) -> None:
    """When all sources return no bars, is_complete=True with actual_bars=0."""
    svc = _service(repo, yfinance_bars=[], ibkr_bars=None)

    result = svc.get_bars(["SPY"], "1m", HIST_DATE, HIST_DATE)

    assert result["SPY"] == []
    cov = repo.load_daily_coverage("SPY", "1m", HIST_DATE.isoformat())
    assert cov is not None
    assert cov.is_complete is True
    assert cov.actual_bars == 0
    assert cov.source == "none"


def test_partial_fetch_marks_incomplete(repo: SqliteMarketDataRepository) -> None:
    """When fewer than expected bars are fetched, is_complete=False."""
    partial_bars = _make_bars(HIST_DATE, n=30)  # only 30 of 390
    svc = _service(repo, yfinance_bars=partial_bars)

    svc.get_bars(["SPY"], "1m", HIST_DATE, HIST_DATE)

    cov = repo.load_daily_coverage("SPY", "1m", HIST_DATE.isoformat())
    assert cov is not None
    assert cov.is_complete is False
    assert cov.actual_bars == 30


def test_multi_day_multi_symbol(repo: SqliteMarketDataRepository) -> None:
    """get_bars calls gateway once per (symbol, day) and returns all fetched bars per symbol."""
    date1 = date(2026, 4, 14)  # Mon
    date2 = date(2026, 4, 15)  # Tue

    # Stub returns 390 bars for every call (same set, service doesn't filter by day itself)
    stub_bars = _make_bars(date1, n=390, price=100.0)

    yf_stub = StubYfinance(stub_bars)
    policy = DataFetchPolicy(
        db_source_priority=["yfinance"],
        live_source_order=["yfinance"],
        history_source_order=["yfinance"],
    )
    svc = BarDataService(
        repository=repo,
        policy=policy,
        ibkr_gateway=None,
        moomoo_gateway=None,
        yfinance_gateway=yf_stub,
        exchange_timezone="America/New_York",
    )

    result = svc.get_bars(["SPY", "QQQ"], "1m", date1, date2)

    # Stub returns 390 bars per call; 2 days per symbol → each symbol accumulates 780 bars
    assert len(result["SPY"]) == 780
    assert len(result["QQQ"]) == 780
    assert yf_stub.call_count == 4     # 2 symbols × 2 days


def test_second_call_uses_cache_no_gateway(repo: SqliteMarketDataRepository) -> None:
    """Second call for same date hits DB (is_complete=True), gateway not called again."""
    bars = _make_bars(HIST_DATE)
    yf_stub = StubYfinance(bars)
    policy = DataFetchPolicy(
        db_source_priority=["yfinance"],
        live_source_order=["yfinance"],
        history_source_order=["yfinance"],
    )
    svc = BarDataService(
        repository=repo,
        policy=policy,
        ibkr_gateway=None,
        moomoo_gateway=None,
        yfinance_gateway=yf_stub,
        exchange_timezone="America/New_York",
    )

    svc.get_bars(["SPY"], "1m", HIST_DATE, HIST_DATE)
    assert yf_stub.call_count == 1

    svc.get_bars(["SPY"], "1m", HIST_DATE, HIST_DATE)
    assert yf_stub.call_count == 1  # still 1 — used DB on second call
