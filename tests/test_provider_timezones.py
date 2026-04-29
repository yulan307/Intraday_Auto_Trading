from __future__ import annotations

from datetime import datetime

from intraday_auto_trading.config import IBKRProfileSettings, MoomooSettings
from intraday_auto_trading.gateways.ibkr_market_data import RealIBKRBackend, _IBHistoricalDataApp
from intraday_auto_trading.gateways.moomoo_options import RealMoomooBackend


def test_ibkr_formats_utc_naive_end_without_exchange_reinterpretation() -> None:
    backend = RealIBKRBackend(
        profile=IBKRProfileSettings(host="127.0.0.1", port=4002, client_id=1),
        exchange_timezone="America/New_York",
    )

    assert backend._format_end_datetime(datetime(2026, 2, 2, 21, 0)) == "20260202-21:00:00"
    assert backend._format_end_datetime(datetime(2026, 4, 16, 20, 0)) == "20260416-20:00:00"


def test_ibkr_string_bar_time_converts_exchange_time_to_utc_across_dst() -> None:
    app = _IBHistoricalDataApp("America/New_York")

    assert app._parse_bar_time("20260202 09:30:00") == datetime(2026, 2, 2, 14, 30)
    assert app._parse_bar_time("20260416 09:30:00") == datetime(2026, 4, 16, 13, 30)
    assert app._parse_bar_time("20260202") == datetime(2026, 2, 2, 5, 0)
    assert app._parse_bar_time("20260416") == datetime(2026, 4, 16, 4, 0)


def test_moomoo_time_key_converts_exchange_time_to_utc_across_dst() -> None:
    settings = MoomooSettings(enabled=True, host="127.0.0.1", port=11111)
    backend = RealMoomooBackend(settings=settings, exchange_timezone="America/New_York")

    assert backend._parse_time_key("2026-02-02 09:30:00", backend.exchange_timezone) == datetime(2026, 2, 2, 14, 30)
    assert backend._parse_time_key("2026-04-16 09:30:00", backend.exchange_timezone) == datetime(2026, 4, 16, 13, 30)
    assert backend._parse_time_key("2026-02-02", backend.exchange_timezone) == datetime(2026, 2, 2, 5, 0)
    assert backend._parse_time_key("2026-04-16", backend.exchange_timezone) == datetime(2026, 4, 16, 4, 0)
