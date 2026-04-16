from __future__ import annotations

import importlib.util
from datetime import date, datetime
from typing import Sequence
from unittest.mock import patch

import pytest

from intraday_auto_trading.gateways.yfinance_market_data import (
    YfinanceMarketDataGateway,
    YfinanceBackend,
)
from intraday_auto_trading.models import CapabilityStatus, MinuteBar


# ---------------------------------------------------------------------------
# Fake backend for injection
# ---------------------------------------------------------------------------

class _FakeBackend:
    """Minimal YfinanceBackend implementation for tests."""

    def __init__(self, bars: dict[str, list[MinuteBar]]) -> None:
        self._bars = bars

    def fetch_bars(
        self,
        symbols: Sequence[str],
        interval: str,
        start: datetime,
        end: datetime,
    ) -> dict[str, list[MinuteBar]]:
        return {s: self._bars[s] for s in symbols if s in self._bars}


def _make_bar(ts: datetime) -> MinuteBar:
    return MinuteBar(timestamp=ts, open=100.0, high=101.0, low=99.0, close=100.5, volume=1000)


# ---------------------------------------------------------------------------
# probe_capabilities
# ---------------------------------------------------------------------------

def test_probe_capabilities_available_when_backend_set() -> None:
    gw = YfinanceMarketDataGateway(backend=_FakeBackend({}))
    caps = gw.probe_capabilities()
    assert caps.bars_1m.status == CapabilityStatus.AVAILABLE
    assert caps.bars_15m_direct.status == CapabilityStatus.AVAILABLE
    assert caps.bars_15m_derived.status == CapabilityStatus.AVAILABLE


def test_probe_capabilities_unavailable_when_no_backend() -> None:
    gw = YfinanceMarketDataGateway(backend=None)
    caps = gw.probe_capabilities()
    assert caps.bars_1m.status == CapabilityStatus.UNAVAILABLE
    assert caps.bars_15m_direct.status == CapabilityStatus.UNAVAILABLE


def test_probe_capabilities_unavailable_when_yfinance_not_installed() -> None:
    with patch("importlib.util.find_spec", return_value=None):
        gw = YfinanceMarketDataGateway(backend=_FakeBackend({}))
        caps = gw.probe_capabilities()
    assert caps.bars_1m.status == CapabilityStatus.UNAVAILABLE


def test_probe_capabilities_unsupported_for_options_and_imbalance() -> None:
    gw = YfinanceMarketDataGateway(backend=_FakeBackend({}))
    caps = gw.probe_capabilities()
    assert caps.opening_imbalance.status == CapabilityStatus.UNSUPPORTED
    assert caps.options.status == CapabilityStatus.UNSUPPORTED


# ---------------------------------------------------------------------------
# bar fetching
# ---------------------------------------------------------------------------

def test_get_minute_bars_returns_bars_from_backend() -> None:
    ts = datetime(2026, 4, 15, 9, 30)
    bar = _make_bar(ts)
    gw = YfinanceMarketDataGateway(backend=_FakeBackend({"SPY": [bar]}))
    bars = gw.get_minute_bars("SPY", datetime(2026, 4, 15, 9, 30), datetime(2026, 4, 15, 10, 0))
    assert len(bars) == 1
    assert bars[0].timestamp == ts


def test_get_minute_bars_returns_empty_when_no_backend() -> None:
    gw = YfinanceMarketDataGateway(backend=None)
    bars = gw.get_minute_bars("SPY", datetime(2026, 4, 15, 9, 30), datetime(2026, 4, 15, 10, 0))
    assert bars == []


def test_get_minute_bars_batch_aggregates_multiple_symbols() -> None:
    ts = datetime(2026, 4, 15, 9, 30)
    gw = YfinanceMarketDataGateway(
        backend=_FakeBackend({"SPY": [_make_bar(ts)], "QQQ": [_make_bar(ts)]})
    )
    result = gw.get_minute_bars_batch(["SPY", "QQQ"], datetime(2026, 4, 15, 9, 30), datetime(2026, 4, 15, 10, 0))
    assert "SPY" in result
    assert "QQQ" in result


def test_get_direct_fifteen_minute_bars_delegates_correctly() -> None:
    ts = datetime(2026, 4, 15, 9, 30)
    captured: list[str] = []

    class _CapturingBackend:
        def fetch_bars(self, symbols, interval, start, end):
            captured.append(interval)
            return {"SPY": [_make_bar(ts)]}

    gw = YfinanceMarketDataGateway(backend=_CapturingBackend())
    gw.get_direct_fifteen_minute_bars("SPY", datetime(2026, 4, 15, 9, 30), datetime(2026, 4, 15, 10, 0))
    assert captured == ["15m"]


def test_get_option_quotes_returns_empty() -> None:
    gw = YfinanceMarketDataGateway(backend=_FakeBackend({}))
    assert gw.get_option_quotes("SPY", datetime(2026, 4, 15, 9, 30)) == []


def test_get_opening_imbalance_returns_none() -> None:
    gw = YfinanceMarketDataGateway(backend=_FakeBackend({}))
    assert gw.get_opening_imbalance("SPY", date(2026, 4, 15)) is None


def test_get_session_metrics_returns_none() -> None:
    gw = YfinanceMarketDataGateway(backend=_FakeBackend({}))
    assert gw.get_session_metrics("SPY", datetime(2026, 4, 15, 9, 30)) is None
