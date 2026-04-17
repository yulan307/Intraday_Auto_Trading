from __future__ import annotations

from intraday_auto_trading.config import IBKRProfileSettings, MoomooSettings
from intraday_auto_trading.gateways.ibkr_market_data import IBKRMarketDataGateway
from intraday_auto_trading.gateways.moomoo_options import MoomooMarketDataGateway
from intraday_auto_trading.models import CapabilityStatus, MinuteBar, OptionQuote, SessionMetrics
from datetime import datetime


class _FakeMoomooBackend:
    def probe(self):
        return CapabilityStatus.AVAILABLE, "ok"

    def fetch_minute_bars(self, symbols, start, end):
        return {
            symbol: [
                MinuteBar(timestamp=start, open=1, high=2, low=0.5, close=1.5, volume=100)
            ]
            for symbol in symbols
        }

    def fetch_direct_fifteen_minute_bars(self, symbols, start, end):
        return {
            symbol: [
                MinuteBar(timestamp=start, open=1, high=2, low=0.5, close=1.5, volume=100)
            ]
            for symbol in symbols
        }

    def fetch_session_metrics(self, symbols, at_time):
        return {
            symbol: SessionMetrics(
                symbol=symbol,
                timestamp=at_time,
                source="moomoo",
                official_open=1.0,
                last_price=1.5,
                session_vwap=1.25,
            )
            for symbol in symbols
        }

    def fetch_option_quotes(self, symbols, at_time):
        return {
            symbol: [
                OptionQuote(
                    symbol=symbol,
                    strike=100.0,
                    side="CALL",
                    bid=1.0,
                    ask=1.1,
                    snapshot_time=at_time,
                )
            ]
            for symbol in symbols
        }


def test_ibkr_gateway_reports_unavailable_without_runtime_backend() -> None:
    gateway = IBKRMarketDataGateway(
        profile_name="paper",
        profile=IBKRProfileSettings(host="127.0.0.1", port=4002, client_id=9),
    )

    capabilities = gateway.probe_capabilities()

    assert capabilities.bars_1m.status in {CapabilityStatus.UNAVAILABLE, CapabilityStatus.UNTESTED}
    assert capabilities.options.status is CapabilityStatus.UNSUPPORTED


def test_moomoo_gateway_reports_disabled_when_not_enabled() -> None:
    gateway = MoomooMarketDataGateway(
        MoomooSettings(enabled=False, host="127.0.0.1", port=11111),
    )

    capabilities = gateway.probe_capabilities()

    assert capabilities.options.status is CapabilityStatus.UNAVAILABLE
    assert capabilities.bars_1m.status is CapabilityStatus.UNAVAILABLE


def test_moomoo_gateway_reports_bars_available_with_backend() -> None:
    gateway = MoomooMarketDataGateway(
        MoomooSettings(enabled=True, host="127.0.0.1", port=11111),
        backend=_FakeMoomooBackend(),
    )

    capabilities = gateway.probe_capabilities()

    assert capabilities.bars_1m.status is CapabilityStatus.AVAILABLE
    assert capabilities.bars_15m_direct.status is CapabilityStatus.AVAILABLE
    assert gateway.get_minute_bars("SPY", datetime(2026, 4, 16, 9, 30), datetime(2026, 4, 16, 10, 0))
    metrics = gateway.get_session_metrics("SPY", datetime(2026, 4, 16, 10, 0))
    assert metrics is not None
    assert metrics.official_open == 1.0
