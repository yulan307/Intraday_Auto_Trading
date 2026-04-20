"""Intraday Low Execution Rule V2 signal module.

Logic:
    pullback_ok  = close < ema20
    reversal_ok  = reversal_ok_a OR reversal_ok_b OR reversal_ok_c
      A: close > max(high[-1], high[-2], high[-3])
      B: low[-1] > low[-2] AND low[0] > low[-1]
      C: close > ema5 AND ema5 > ema5_prev
    buy_now = pullback_ok AND reversal_ok

Limit price (only when buy_now):
    prev_mid    = (bars[i-1].close + bars[i-1].low) / 2
    limit_price = round(min(vwap, prev_mid), 2)

EMA: pure Python, alpha = 2 / (span + 1), no pandas.
Warmup: current_idx < 20 → "wait" (ema20 needs 20 bars).
force_buy_time is passed externally; not hardcoded here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from intraday_auto_trading.models import MinuteBar


@dataclass(slots=True)
class IntradayLowConfig:
    ema_fast_span: int = 5
    ema_slow_span: int = 20
    recent_high_lookback: int = 3
    force_buy_minutes_before_close: int = 15


@dataclass(slots=True)
class IntradayLowSignalResult:
    signal: str                       # "wait" | "buy_now" | "force_buy"
    pullback_ok: bool
    reversal_ok_a: bool
    reversal_ok_b: bool
    reversal_ok_c: bool
    reversal_ok: bool
    ema5: float | None
    ema20: float | None
    recent_3bar_high: float | None
    limit_price: float | None         # only set when signal == "buy_now"
    vwap: float | None
    prev_mid: float | None


def _compute_ema(values: Sequence[float], span: int) -> float:
    """Return the final EMA value over the given sequence using pure Python.

    alpha = 2 / (span + 1), seed = first value.
    """
    alpha = 2.0 / (span + 1)
    ema = values[0]
    for v in values[1:]:
        ema = alpha * v + (1.0 - alpha) * ema
    return ema


def _compute_vwap(bars: Sequence[MinuteBar], up_to_idx: int) -> float:
    """Cumulative VWAP from bars[0] to bars[up_to_idx] inclusive."""
    cum_pv = 0.0
    cum_v = 0.0
    for bar in bars[: up_to_idx + 1]:
        cum_pv += bar.close * bar.volume
        cum_v += bar.volume
    return bars[up_to_idx].close if cum_v <= 0 else cum_pv / cum_v


def compute_intraday_low_signal(
    bars: Sequence[MinuteBar],
    current_idx: int,
    force_buy_time: datetime,
    already_bought_today: bool,
    config: IntradayLowConfig = IntradayLowConfig(),
) -> IntradayLowSignalResult:
    """Evaluate V2 intraday low signal for the bar at current_idx.

    Parameters
    ----------
    bars:
        Sequence of MinuteBar for the current trading session (chronological).
    current_idx:
        Index of the current *closed* bar within bars.
    force_buy_time:
        Externally supplied deadline; if the bar's timestamp >= this value,
        output "force_buy" (and already_bought_today has not been set).
    already_bought_today:
        If True, always returns "wait" regardless of signal conditions.
    config:
        Optional parameter overrides.
    """
    _no_signal = IntradayLowSignalResult(
        signal="wait",
        pullback_ok=False,
        reversal_ok_a=False,
        reversal_ok_b=False,
        reversal_ok_c=False,
        reversal_ok=False,
        ema5=None,
        ema20=None,
        recent_3bar_high=None,
        limit_price=None,
        vwap=None,
        prev_mid=None,
    )

    # 1. Already bought today → always wait
    if already_bought_today:
        return _no_signal

    current_time = bars[current_idx].timestamp

    # 2. force_buy window
    if current_time >= force_buy_time:
        return IntradayLowSignalResult(
            signal="force_buy",
            pullback_ok=False,
            reversal_ok_a=False,
            reversal_ok_b=False,
            reversal_ok_c=False,
            reversal_ok=False,
            ema5=None,
            ema20=None,
            recent_3bar_high=None,
            limit_price=None,
            vwap=None,
            prev_mid=None,
        )

    # 3. Warmup guard (need at least ema_slow_span bars and 3 extra for lookback)
    lookback_needed = max(config.ema_slow_span, config.recent_high_lookback + 1)
    if current_idx < lookback_needed:
        return _no_signal

    # 4. Build close series up to current_idx (inclusive)
    closes = [bars[i].close for i in range(current_idx + 1)]

    # 5. EMA calculations
    ema20 = _compute_ema(closes, config.ema_slow_span)
    ema5 = _compute_ema(closes, config.ema_fast_span)
    ema5_prev = _compute_ema(closes[:-1], config.ema_fast_span)

    close_now = bars[current_idx].close
    low_now = bars[current_idx].low

    # 6. Recent high lookback (bars[-1], [-2], [-3] relative to current bar)
    lookback = config.recent_high_lookback
    recent_3bar_high = max(
        bars[current_idx - k].high for k in range(1, lookback + 1)
    )

    # 7. Signal conditions
    pullback_ok = close_now < ema20

    reversal_ok_a = close_now > recent_3bar_high
    reversal_ok_b = (
        bars[current_idx - 1].low > bars[current_idx - 2].low
        and low_now > bars[current_idx - 1].low
    )
    reversal_ok_c = (close_now > ema5) and (ema5 > ema5_prev)

    reversal_ok = reversal_ok_a or reversal_ok_b or reversal_ok_c

    if not (pullback_ok and reversal_ok):
        return IntradayLowSignalResult(
            signal="wait",
            pullback_ok=bool(pullback_ok),
            reversal_ok_a=bool(reversal_ok_a),
            reversal_ok_b=bool(reversal_ok_b),
            reversal_ok_c=bool(reversal_ok_c),
            reversal_ok=bool(reversal_ok),
            ema5=ema5,
            ema20=ema20,
            recent_3bar_high=recent_3bar_high,
            limit_price=None,
            vwap=None,
            prev_mid=None,
        )

    # 8. buy_now — compute limit price
    vwap = _compute_vwap(bars, current_idx)
    prev_mid = (bars[current_idx - 1].close + bars[current_idx - 1].low) / 2.0
    limit_price = round(min(vwap, prev_mid), 2)

    return IntradayLowSignalResult(
        signal="buy_now",
        pullback_ok=True,
        reversal_ok_a=bool(reversal_ok_a),
        reversal_ok_b=bool(reversal_ok_b),
        reversal_ok_c=bool(reversal_ok_c),
        reversal_ok=True,
        ema5=ema5,
        ema20=ema20,
        recent_3bar_high=recent_3bar_high,
        limit_price=limit_price,
        vwap=vwap,
        prev_mid=prev_mid,
    )
