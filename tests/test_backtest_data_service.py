from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Sequence

import pytest

from intraday_auto_trading.gateways.yfinance_market_data import YfinanceMarketDataGateway
from intraday_auto_trading.models import (
    CapabilityStatus,
    MarketDataType,
    MinuteBar,
    ProviderCapabilities,
    ProviderCapability,
    SymbolInfo,
)
from intraday_auto_trading.persistence.market_data_repository import SqliteMarketDataRepository
from intraday_auto_trading.services.backtest_data_service import BacktestDataService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

START = datetime(2026, 4, 15, 9, 30)
END = datetime(2026, 4, 15, 10, 0)


def _bar(minute: int) -> MinuteBar:
    ts = datetime(2026, 4, 15, 9, 30 + minute)
    return MinuteBar(timestamp=ts, open=100.0, high=101.0, low=99.0, close=100.5, volume=1000)


def _bars(count: int) -> list[MinuteBar]:
    return [_bar(i) for i in range(count)]


def _available_caps(provider: str) -> ProviderCapabilities:
    avail = ProviderCapability(MarketDataType.BARS_1M, CapabilityStatus.AVAILABLE)
    avail_15m = ProviderCapability(MarketDataType.BARS_15M_DIRECT, CapabilityStatus.AVAILABLE)
    unavail_15m_d = ProviderCapability(MarketDataType.BARS_15M_DERIVED, CapabilityStatus.UNAVAILABLE)
    unavail_oi = ProviderCapability(MarketDataType.OPENING_IMBALANCE, CapabilityStatus.UNSUPPORTED)
    unavail_opt = ProviderCapability(MarketDataType.OPTIONS, CapabilityStatus.UNSUPPORTED)
    return ProviderCapabilities(
        provider=provider,
        bars_1m=avail,
        bars_15m_direct=avail_15m,
        bars_15m_derived=unavail_15m_d,
        opening_imbalance=unavail_oi,
        options=unavail_opt,
    )


def _unavailable_caps(provider: str) -> ProviderCapabilities:
    unavail = ProviderCapability(MarketDataType.BARS_1M, CapabilityStatus.UNAVAILABLE)
    unavail_15m = ProviderCapability(MarketDataType.BARS_15M_DIRECT, CapabilityStatus.UNAVAILABLE)
    unavail_15md = ProviderCapability(MarketDataType.BARS_15M_DERIVED, CapabilityStatus.UNAVAILABLE)
    unavail_oi = ProviderCapability(MarketDataType.OPENING_IMBALANCE, CapabilityStatus.UNSUPPORTED)
    unavail_opt = ProviderCapability(MarketDataType.OPTIONS, CapabilityStatus.UNSUPPORTED)
    return ProviderCapabilities(
        provider=provider,
        bars_1m=unavail,
        bars_15m_direct=unavail_15m,
        bars_15m_derived=unavail_15md,
        opening_imbalance=unavail_oi,
        options=unavail_opt,
    )


class _FakeGateway:
    def __init__(self, provider_name: str, bars: list[MinuteBar], available: bool = True) -> None:
        self.provider_name = provider_name
        self._bars = bars
        self._available = available
        self.calls: list[str] = []

    def probe_capabilities(self) -> ProviderCapabilities:
        if self._available:
            return _available_caps(self.provider_name)
        return _unavailable_caps(self.provider_name)

    def get_minute_bars_batch(
        self, symbols: Sequence[str], start: datetime, end: datetime
    ) -> dict[str, list[MinuteBar]]:
        self.calls.append("get_minute_bars_batch")
        return {s: self._bars for s in symbols}

    def get_direct_fifteen_minute_bars_batch(
        self, symbols: Sequence[str], start: datetime, end: datetime
    ) -> dict[str, list[MinuteBar]]:
        self.calls.append("get_direct_fifteen_minute_bars_batch")
        return {s: self._bars for s in symbols}

    def get_daily_bars_batch(
        self, symbols: Sequence[str], start: datetime, end: datetime
    ) -> dict[str, list[MinuteBar]]:
        self.calls.append("get_daily_bars_batch")
        return {s: self._bars for s in symbols}

    def get_minute_bars(self, symbol: str, start: datetime, end: datetime) -> list[MinuteBar]:
        return self._bars

    def get_direct_fifteen_minute_bars(self, symbol: str, start: datetime, end: datetime) -> list[MinuteBar]:
        return self._bars

    def get_daily_bars(self, symbol: str, start: datetime, end: datetime) -> list[MinuteBar]:
        return self._bars


class _FakeYfinanceBackend:
    def __init__(self, bars: dict[str, list[MinuteBar]]) -> None:
        self._bars = bars
        self.calls: list[str] = []

    def fetch_bars(
        self, symbols: Sequence[str], interval: str, start: datetime, end: datetime
    ) -> dict[str, list[MinuteBar]]:
        self.calls.append(interval)
        return {s: self._bars[s] for s in symbols if s in self._bars}


def _make_service(
    tmp_path: Path,
    ibkr_gateway=None,
    moomoo_gateway=None,
    yfinance_backend=None,
    source_priority=None,
) -> BacktestDataService:
    repo = SqliteMarketDataRepository(tmp_path / "db.sqlite")
    yf_gw = YfinanceMarketDataGateway(backend=yfinance_backend or _FakeYfinanceBackend({}))
    kwargs = dict(
        repository=repo,
        yfinance_gateway=yf_gw,
        ibkr_gateway=ibkr_gateway,
        moomoo_gateway=moomoo_gateway,
    )
    if source_priority is not None:
        kwargs["source_priority"] = source_priority
    return BacktestDataService(**kwargs)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_returns_from_db_when_data_exists(tmp_path: Path) -> None:
    repo = SqliteMarketDataRepository(tmp_path / "db.sqlite")
    repo.upsert_symbol(SymbolInfo("SPY"))
    repo.save_price_bars("SPY", "1m", _bars(5), source="ibkr")

    ibkr = _FakeGateway("ibkr", _bars(5))
    yf_gw = YfinanceMarketDataGateway(backend=_FakeYfinanceBackend({}))
    service = BacktestDataService(repository=repo, ibkr_gateway=ibkr, yfinance_gateway=yf_gw)
    results = service.get_bars(["SPY"], "1m", START, END)

    assert results[0].source == "db:ibkr"
    assert results[0].bar_count == 5
    assert ibkr.calls == []  # no live call made


def test_falls_back_to_ibkr_when_db_empty(tmp_path: Path) -> None:
    ibkr = _FakeGateway("ibkr", _bars(3))
    service = _make_service(tmp_path, ibkr_gateway=ibkr)
    results = service.get_bars(["SPY"], "1m", START, END)

    assert results[0].source == "ibkr"
    assert results[0].bar_count == 3
    assert "written to db" in results[0].message
    assert "get_minute_bars_batch" in ibkr.calls


def test_ibkr_result_cached_in_db(tmp_path: Path) -> None:
    ibkr = _FakeGateway("ibkr", _bars(3))
    service = _make_service(tmp_path, ibkr_gateway=ibkr)

    service.get_bars(["SPY"], "1m", START, END)  # first call: fetches from ibkr
    ibkr.calls.clear()
    results = service.get_bars(["SPY"], "1m", START, END)  # second call: should hit DB

    assert results[0].source == "db:ibkr"
    assert ibkr.calls == []


def test_falls_back_to_moomoo_when_ibkr_unavailable(tmp_path: Path) -> None:
    ibkr = _FakeGateway("ibkr", _bars(3), available=False)
    moomoo = _FakeGateway("moomoo", _bars(4))
    service = _make_service(
        tmp_path,
        ibkr_gateway=ibkr,
        moomoo_gateway=moomoo,
        source_priority=["ibkr", "moomoo"],
    )
    results = service.get_bars(["SPY"], "1m", START, END)

    assert results[0].source == "moomoo"
    assert results[0].bar_count == 4
    assert "get_minute_bars_batch" not in ibkr.calls


def test_falls_back_to_yfinance_when_live_unavailable(tmp_path: Path) -> None:
    ibkr = _FakeGateway("ibkr", [], available=False)
    yf_backend = _FakeYfinanceBackend({"SPY": _bars(6)})
    service = _make_service(
        tmp_path,
        ibkr_gateway=ibkr,
        yfinance_backend=yf_backend,
        source_priority=["ibkr", "yfinance"],
    )
    results = service.get_bars(["SPY"], "1m", START, END)

    assert results[0].source == "yfinance"
    assert results[0].bar_count == 6
    assert len(yf_backend.calls) == 1


def test_returns_none_when_all_sources_fail(tmp_path: Path) -> None:
    ibkr = _FakeGateway("ibkr", [], available=False)
    yf_backend = _FakeYfinanceBackend({})
    service = _make_service(tmp_path, ibkr_gateway=ibkr, yfinance_backend=yf_backend)
    results = service.get_bars(["SPY"], "1m", START, END)

    assert results[0].source == "none"
    assert results[0].bar_count == 0


def test_db_source_priority_prefers_ibkr_over_yfinance(tmp_path: Path) -> None:
    repo = SqliteMarketDataRepository(tmp_path / "db.sqlite")
    repo.upsert_symbol(SymbolInfo("SPY"))
    repo.save_price_bars("SPY", "1m", _bars(2), source="yfinance")
    repo.save_price_bars("SPY", "1m", _bars(2), source="ibkr")

    yf_gw = YfinanceMarketDataGateway(backend=_FakeYfinanceBackend({}))
    service = BacktestDataService(
        repository=repo,
        yfinance_gateway=yf_gw,
        source_priority=["ibkr", "moomoo", "yfinance"],
    )
    results = service.get_bars(["SPY"], "1m", START, END)

    assert results[0].source == "db:ibkr"


def test_daily_bars_use_correct_batch_method(tmp_path: Path) -> None:
    ibkr = _FakeGateway("ibkr", _bars(2))
    service = _make_service(tmp_path, ibkr_gateway=ibkr)
    results = service.get_bars(["SPY"], "1d", START, END)

    assert results[0].source == "ibkr"
    assert "get_daily_bars_batch" in ibkr.calls


def test_multiple_symbols_handled_independently(tmp_path: Path) -> None:
    yf_backend = _FakeYfinanceBackend({"SPY": _bars(3), "QQQ": _bars(2)})
    service = _make_service(
        tmp_path,
        yfinance_backend=yf_backend,
        source_priority=["yfinance"],
    )
    results = service.get_bars(["SPY", "QQQ"], "1m", START, END)

    by_symbol = {r.symbol: r for r in results}
    assert by_symbol["SPY"].bar_count == 3
    assert by_symbol["QQQ"].bar_count == 2
