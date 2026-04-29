from __future__ import annotations

from datetime import datetime

from intraday_auto_trading.models import MinuteBar
from intraday_auto_trading.services.bar_metrics import (
    compute_vwap_from_minute_bars,
    derive_session_metrics_from_minute_bars,
)


def test_compute_vwap_from_minute_bars_uses_volume_weighting() -> None:
    bars = [
        MinuteBar(datetime(2026, 4, 16, 9, 30), 10, 11, 9, 10, 100),
        MinuteBar(datetime(2026, 4, 16, 9, 31), 10, 12, 10, 12, 50),
    ]

    assert compute_vwap_from_minute_bars(bars) == (10 * 100 + 12 * 50) / 150


def test_compute_vwap_from_minute_bars_uses_last_close_when_volume_zero() -> None:
    bars = [
        MinuteBar(datetime(2026, 4, 16, 9, 30), 10, 11, 9, 10, 0),
        MinuteBar(datetime(2026, 4, 16, 9, 31), 10, 12, 10, 12, 0),
    ]

    assert compute_vwap_from_minute_bars(bars) == 12


def test_derive_session_metrics_from_minute_bars() -> None:
    eval_time = datetime(2026, 4, 16, 10, 0)
    bars = [
        MinuteBar(datetime(2026, 4, 16, 9, 30), 10, 11, 9, 10, 100),
        MinuteBar(datetime(2026, 4, 16, 9, 31), 10, 12, 10, 12, 50),
    ]

    metrics = derive_session_metrics_from_minute_bars("SPY", eval_time, bars)

    assert metrics.source == "derived_1m"
    assert metrics.official_open == 10
    assert metrics.last_price == 12
    assert metrics.session_vwap == (10 * 100 + 12 * 50) / 150
