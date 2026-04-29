from __future__ import annotations

from datetime import datetime

from intraday_auto_trading.models import MinuteBar, SessionMetrics


def compute_vwap_from_minute_bars(bars: list[MinuteBar]) -> float | None:
    """Compute session VWAP from 1m bars using close-price volume weighting."""
    if not bars:
        return None
    total_volume = sum(bar.volume for bar in bars)
    if total_volume <= 0:
        return bars[-1].close
    return sum(bar.close * bar.volume for bar in bars) / total_volume


def derive_session_metrics_from_minute_bars(
    symbol: str,
    eval_time: datetime,
    bars: list[MinuteBar],
) -> SessionMetrics:
    if not bars:
        raise ValueError("Cannot derive session metrics without 1m bars.")
    return SessionMetrics(
        symbol=symbol,
        timestamp=eval_time,
        source="derived_1m",
        official_open=bars[0].open,
        last_price=bars[-1].close,
        session_vwap=compute_vwap_from_minute_bars(bars),
    )
