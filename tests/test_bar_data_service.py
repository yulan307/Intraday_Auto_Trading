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
    BarRequestLog,
    CapabilityStatus,
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

    provider_name = "ibkr"

    def __init__(self, bars: list[MinuteBar], available: bool = True) -> None:
        self._bars = bars
        self._available = available
        self.call_count = 0

    def probe_capabilities(self) -> _FakeCapabilities:
        return _FakeCapabilities(self._available)

    def get_minute_bars(self, symbol: str, start: datetime, end: datetime) -> list[MinuteBar]:
        self.call_count += 1
        return list(self._bars)

    def get_daily_bars(self, symbol: str, start: datetime, end: datetime) -> list[MinuteBar]:
        self.call_count += 1
        return list(self._bars)


class PartiallyFailingBatchGateway(StubGateway):
    def __init__(self, bars: list[MinuteBar], failing_symbols: set[str]) -> None:
        super().__init__(bars)
        self.failing_symbols = failing_symbols

    def get_minute_bars_batch(
        self, symbols: list[str], start: datetime, end: datetime
    ) -> dict[str, list[MinuteBar]]:
        raise RuntimeError("batch unavailable")

    def get_minute_bars(self, symbol: str, start: datetime, end: datetime) -> list[MinuteBar]:
        self.call_count += 1
        if symbol in self.failing_symbols:
            raise RuntimeError(f"backend failure for {symbol}")
        return list(self._bars)


class StubYfinance:
    """Yfinance stub — implements batch API (mirrors real YfinanceMarketDataGateway)."""

    def __init__(self, bars: list[MinuteBar]) -> None:
        self._bars = bars
        self.call_count = 0
        self.call_ranges: list[tuple[datetime, datetime]] = []

    def get_minute_bars_batch(
        self, symbols: list[str], start: datetime, end: datetime
    ) -> dict[str, list[MinuteBar]]:
        self.call_count += 1
        self.call_ranges.append((start, end))
        return {s: list(self._bars) for s in symbols}

    def get_direct_fifteen_minute_bars_batch(
        self, symbols: list[str], start: datetime, end: datetime
    ) -> dict[str, list[MinuteBar]]:
        self.call_count += 1
        self.call_ranges.append((start, end))
        return {s: [] for s in symbols}

    # Single-symbol fallbacks (used by code that doesn't detect batch capability)
    def get_minute_bars(self, symbol: str, start: datetime, end: datetime) -> list[MinuteBar]:
        return self.get_minute_bars_batch([symbol], start, end).get(symbol, [])

    def get_direct_fifteen_minute_bars(self, symbol: str, start: datetime, end: datetime) -> list[MinuteBar]:
        return []

    def get_daily_bars_batch(
        self, symbols: list[str], start: datetime, end: datetime
    ) -> dict[str, list[MinuteBar]]:
        self.call_count += 1
        self.call_ranges.append((start, end))
        return {s: list(self._bars) for s in symbols}


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
    """If bar_request_log says success, bars come from DB and gateway is not called."""
    bars = _make_bars(HIST_DATE)
    repo.upsert_symbol(SymbolInfo(symbol="SPY"))
    repo.save_price_bars("SPY", "1m", bars, "ibkr")
    repo.save_bar_request_log(BarRequestLog(
        symbol="SPY", bar_size="1m", trade_date=HIST_DATE.isoformat(),
        source="ibkr",
        request_start_ts=datetime(2026, 4, 14, 13, 30),
        request_end_ts=datetime(2026, 4, 14, 20, 0),
        status="success",
        expected_bars=390, actual_bars=390,
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
    log = repo.load_bar_request_log("SPY", "1m", HIST_DATE.isoformat())
    assert log is not None
    assert log.status == "success"
    assert log.actual_bars == 390
    assert log.source == "yfinance"


def test_default_policy_uses_ibkr_for_history_date(repo: SqliteMarketDataRepository) -> None:
    bars = _make_bars(HIST_DATE)
    ibkr_stub = StubGateway(bars)
    yf_stub = StubYfinance(bars)
    svc = BarDataService(
        repository=repo,
        policy=DataFetchPolicy(),
        ibkr_gateway=ibkr_stub,
        moomoo_gateway=None,
        yfinance_gateway=yf_stub,
        exchange_timezone="America/New_York",
    )

    result = svc.get_bars(["SPY"], "1m", HIST_DATE, HIST_DATE)

    assert len(result["SPY"]) == 390
    assert ibkr_stub.call_count == 1
    assert yf_stub.call_count == 0
    log = repo.load_bar_request_log("SPY", "1m", HIST_DATE.isoformat())
    assert log is not None
    assert log.source == "ibkr"


def test_source_order_override_can_force_ibkr_for_history_date(repo: SqliteMarketDataRepository) -> None:
    """A caller-provided source order overrides history/live provider policy."""
    bars = _make_bars(HIST_DATE)
    ibkr_stub = StubGateway(bars)
    yf_stub = StubYfinance(bars)
    svc = BarDataService(
        repository=repo,
        policy=DataFetchPolicy(
            db_source_priority=["yfinance", "ibkr"],
            live_source_order=["yfinance"],
            history_source_order=["yfinance"],
        ),
        ibkr_gateway=ibkr_stub,
        moomoo_gateway=None,
        yfinance_gateway=yf_stub,
        exchange_timezone="America/New_York",
    )

    result = svc.get_bars(["SPY"], "1m", HIST_DATE, HIST_DATE, source_order=["ibkr"])

    assert len(result["SPY"]) == 390
    assert yf_stub.call_count == 0
    assert ibkr_stub.call_count == 1
    log = repo.load_bar_request_log("SPY", "1m", HIST_DATE.isoformat())
    assert log is not None
    assert log.status == "success"
    assert log.source == "ibkr"


def test_all_sources_empty_marks_complete_confirmed_no_data(repo: SqliteMarketDataRepository) -> None:
    """When all sources return no bars, is_complete=True with actual_bars=0."""
    svc = _service(repo, yfinance_bars=[], ibkr_bars=None)

    result = svc.get_bars(["SPY"], "1m", HIST_DATE, HIST_DATE)

    assert result["SPY"] == []
    log = repo.load_bar_request_log("SPY", "1m", HIST_DATE.isoformat())
    assert log is not None
    assert log.status == "no_data"
    assert log.actual_bars == 0
    assert log.source == "none"


def test_provider_exception_marks_failed_with_message_per_symbol(repo: SqliteMarketDataRepository) -> None:
    bars = _make_bars(HIST_DATE)
    ibkr_stub = PartiallyFailingBatchGateway(bars, failing_symbols={"QQQ"})
    svc = BarDataService(
        repository=repo,
        policy=DataFetchPolicy(
            db_source_priority=["ibkr"],
            live_source_order=["ibkr"],
            history_source_order=["ibkr"],
        ),
        ibkr_gateway=ibkr_stub,
        moomoo_gateway=None,
        yfinance_gateway=StubYfinance([]),
        exchange_timezone="America/New_York",
    )

    result = svc.get_bars(["SPY", "QQQ"], "1m", HIST_DATE, HIST_DATE)

    assert len(result["SPY"]) == 390
    assert result["QQQ"] == []
    spy_log = repo.load_bar_request_log("SPY", "1m", HIST_DATE.isoformat())
    qqq_log = repo.load_bar_request_log("QQQ", "1m", HIST_DATE.isoformat())
    assert spy_log is not None
    assert spy_log.status == "success"
    assert qqq_log is not None
    assert qqq_log.status == "failed"
    assert qqq_log.message is not None
    assert "backend failure for QQQ" in qqq_log.message


def test_daily_bars_fetch_uses_daily_provider_method(repo: SqliteMarketDataRepository) -> None:
    bars = _make_bars(HIST_DATE, n=1)
    svc = _service(repo, ibkr_bars=bars, yfinance_bars=[])

    result = svc.get_bars(["SPY"], "1d", HIST_DATE, HIST_DATE, source_order=["ibkr"])

    assert len(result["SPY"]) == 1
    log = repo.load_bar_request_log("SPY", "1d", HIST_DATE.isoformat())
    assert log is not None
    assert log.status == "success"
    assert log.source == "ibkr"


def test_partial_fetch_marks_incomplete(repo: SqliteMarketDataRepository) -> None:
    """When fewer than expected bars are fetched, is_complete=False."""
    partial_bars = _make_bars(HIST_DATE, n=30)  # only 30 of 390
    svc = _service(repo, yfinance_bars=partial_bars)

    svc.get_bars(["SPY"], "1m", HIST_DATE, HIST_DATE)

    log = repo.load_bar_request_log("SPY", "1m", HIST_DATE.isoformat())
    assert log is not None
    assert log.status == "partial"
    assert log.actual_bars == 30


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

    # Stub returns 390 bars per symbol per call; 2 days → each symbol accumulates 780 bars
    assert len(result["SPY"]) == 780
    assert len(result["QQQ"]) == 780
    assert yf_stub.call_count == 2     # 1 batch call per day (both symbols together)


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
    assert yf_stub.call_count == 1


def test_force_refresh_ignores_terminal_request_log(repo: SqliteMarketDataRepository) -> None:
    bars = _make_bars(HIST_DATE)
    repo.save_bar_request_log(BarRequestLog(
        symbol="SPY", bar_size="1m", trade_date=HIST_DATE.isoformat(),
        source="none",
        request_start_ts=datetime(2026, 4, 14, 13, 30),
        request_end_ts=datetime(2026, 4, 14, 20, 0),
        status="no_data",
        expected_bars=390, actual_bars=0,
    ))
    yf_stub = StubYfinance(bars)
    svc = BarDataService(
        repository=repo,
        policy=DataFetchPolicy(
            db_source_priority=["yfinance"],
            live_source_order=["yfinance"],
            history_source_order=["yfinance"],
        ),
        ibkr_gateway=None,
        moomoo_gateway=None,
        yfinance_gateway=yf_stub,
        exchange_timezone="America/New_York",
    )

    result = svc.get_bars(["SPY"], "1m", HIST_DATE, HIST_DATE, force_refresh=True)

    assert len(result["SPY"]) == 390
    assert yf_stub.call_count == 1
    log = repo.load_bar_request_log("SPY", "1m", HIST_DATE.isoformat())
    assert log is not None
    assert log.status == "success"


def test_est_session_window_converts_to_utc(repo: SqliteMarketDataRepository) -> None:
    trade_date = date(2026, 2, 2)
    yf_stub = StubYfinance([])
    svc = BarDataService(
        repository=repo,
        policy=DataFetchPolicy(
            db_source_priority=["yfinance"],
            live_source_order=["yfinance"],
            history_source_order=["yfinance"],
        ),
        ibkr_gateway=None,
        moomoo_gateway=None,
        yfinance_gateway=yf_stub,
        exchange_timezone="America/New_York",
    )

    svc.get_bars(["SPY"], "1m", trade_date, trade_date)

    assert yf_stub.call_ranges == [
        (datetime(2026, 2, 2, 14, 30), datetime(2026, 2, 2, 21, 0))
    ]
    log = repo.load_bar_request_log("SPY", "1m", trade_date.isoformat())
    assert log is not None
    assert log.request_start_ts == datetime(2026, 2, 2, 14, 30)
    assert log.request_end_ts == datetime(2026, 2, 2, 21, 0)


def test_edt_session_window_converts_to_utc(repo: SqliteMarketDataRepository) -> None:
    trade_date = date(2026, 4, 16)
    yf_stub = StubYfinance([])
    svc = BarDataService(
        repository=repo,
        policy=DataFetchPolicy(
            db_source_priority=["yfinance"],
            live_source_order=["yfinance"],
            history_source_order=["yfinance"],
        ),
        ibkr_gateway=None,
        moomoo_gateway=None,
        yfinance_gateway=yf_stub,
        exchange_timezone="America/New_York",
    )

    svc.get_bars(["SPY"], "1m", trade_date, trade_date)

    assert yf_stub.call_ranges == [
        (datetime(2026, 4, 16, 13, 30), datetime(2026, 4, 16, 20, 0))
    ]
    log = repo.load_bar_request_log("SPY", "1m", trade_date.isoformat())
    assert log is not None
    assert log.request_start_ts == datetime(2026, 4, 16, 13, 30)
    assert log.request_end_ts == datetime(2026, 4, 16, 20, 0)

    svc.get_bars(["SPY"], "1m", trade_date, trade_date)
    assert yf_stub.call_count == 1  # still 1 — used DB on second call
