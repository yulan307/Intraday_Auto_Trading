"""Intraday Low Signal — dev20 momentum algorithm.

Algorithm (per docs/signal-dev20.md):

    Indicators:
        vwap     = cumulative session VWAP (typical price × volume weighted)
        ema20    = 20-period EMA of close
        dev20    = (vwap - ema20) / vwap          # positive → ema20 below vwap (price low zone)
        s_dev20  = Theil-Sen slope(dev20,  window=10)   # 1st-order momentum
        ss_dev20 = Theil-Sen slope(s_dev20, window=10)  # 2nd-order (acceleration)
        valley   = s_dev20 + 10 × ss_dev20         # composite momentum indicator
        s_valley = Theil-Sen slope(valley,  window=3)   # valley momentum

    Buy signal (all conditions must hold simultaneously):
        ema20 < vwap                       # price in low zone
        AND s_dev20 > valley > 0           # positive momentum, valley < s_dev20
        AND s_valley < 0                   # valley turning downward (exhaustion)
        AND abs(s_valley × 10) > s_dev20   # downward force exceeds 1st-order momentum

    Limit price (when buy_now):
        (bars[current_idx - 1].low + bars[current_idx - 1].close) / 2

    Cancel condition (handled externally by caller):
        EMA5 < EMA10

Warmup: requires current_idx >= ema_slow_span (20 bars) before any signal is possible.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from intraday_auto_trading.models import Dev20SignalResult, MinuteBar


@dataclass(slots=True)
class IntradayLowConfig:
    ema_fast_span: int = 5       # EMA5  (used for cancel condition EMA5 < EMA10)
    ema10_span: int = 10         # EMA10 (cancel condition reference)
    ema_slow_span: int = 20      # EMA20 (dev20 base)
    dev20_window: int = 10       # Theil-Sen window for s_dev20
    s_dev20_window: int = 10     # Theil-Sen window for ss_dev20
    valley_window: int = 3       # Theil-Sen window for s_valley


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

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
    """Cumulative VWAP from bars[0] to bars[up_to_idx] inclusive.

    Uses close price as the typical price proxy (legacy; see _compute_vwap_series
    for the standard (H+L+C)/3 typical price variant).
    """
    cum_pv = 0.0
    cum_v = 0.0
    for bar in bars[: up_to_idx + 1]:
        cum_pv += bar.close * bar.volume
        cum_v += bar.volume
    return bars[up_to_idx].close if cum_v <= 0 else cum_pv / cum_v


def _compute_vwap_series(bars: Sequence[MinuteBar], up_to_idx: int) -> list[float]:
    """Return cumulative VWAP for bars[0..up_to_idx] (inclusive).

    Uses standard typical price = (high + low + close) / 3.
    """
    cum_vol = 0.0
    cum_tp_vol = 0.0
    result: list[float] = []
    for bar in bars[: up_to_idx + 1]:
        tp = (bar.high + bar.low + bar.close) / 3.0
        cum_vol += bar.volume
        cum_tp_vol += tp * bar.volume
        result.append(cum_tp_vol / cum_vol if cum_vol > 0 else bar.close)
    return result


def _theil_sen_slope(values: list[float], n: int) -> float | None:
    """Theil-Sen robust slope over the last n values.

    Returns None if there are fewer than n values available.
    The slope is the median of all pairwise slopes (y[j]-y[i])/(j-i).
    """
    if len(values) < n:
        return None
    y = values[-n:]
    slopes: list[float] = []
    for i in range(n):
        for j in range(i + 1, n):
            slopes.append((y[j] - y[i]) / (j - i))
    slopes.sort()
    return slopes[len(slopes) // 2]


# ---------------------------------------------------------------------------
# Public signal function
# ---------------------------------------------------------------------------

def compute_intraday_low_signal(
    bars: Sequence[MinuteBar],
    current_idx: int,
    config: IntradayLowConfig = IntradayLowConfig(),
) -> Dev20SignalResult:
    """Evaluate dev20-based intraday low signal for the bar at current_idx.

    Parameters
    ----------
    bars:
        Sequence of MinuteBar for the current trading session (chronological).
    current_idx:
        Index of the current *closed* bar within bars.
    config:
        Optional parameter overrides.

    Returns
    -------
    Dev20SignalResult with signal="wait" or "buy_now".
    All indicator fields are None during the warmup period or when data is
    insufficient for slope computation.
    """
    _no_signal = Dev20SignalResult(
        signal="wait",
        dev20=None,
        s_dev20=None,
        ss_dev20=None,
        valley=None,
        s_valley=None,
        ema5=None,
        ema10=None,
        ema20=None,
        vwap=None,
        limit_price=None,
    )

    # 1. Warmup guard: need at least ema_slow_span bars
    if current_idx < config.ema_slow_span:
        return _no_signal

    # 2. Build VWAP series for all bars up to current_idx
    vwap_series = _compute_vwap_series(bars, current_idx)

    # 3. Accumulate dev20 series from ema_slow_span onward
    dev20_buf: list[float] = []
    s_dev20_buf: list[float] = []
    valley_buf: list[float] = []

    for i in range(config.ema_slow_span, current_idx + 1):
        closes_i = [bars[k].close for k in range(i + 1)]
        ema20_i = _compute_ema(closes_i, config.ema_slow_span)
        v = vwap_series[i]
        d20 = (v - ema20_i) / v if v != 0 else 0.0
        dev20_buf.append(d20)

        s = _theil_sen_slope(dev20_buf, config.dev20_window)
        if s is not None:
            s_dev20_buf.append(s)

        ss = _theil_sen_slope(s_dev20_buf, config.s_dev20_window)
        if s is not None and ss is not None:
            valley_buf.append(s + 10.0 * ss)

    # 4. Current-bar indicator values
    closes = [bars[k].close for k in range(current_idx + 1)]
    ema5 = _compute_ema(closes, config.ema_fast_span)
    ema10 = _compute_ema(closes, config.ema10_span)
    ema20 = _compute_ema(closes, config.ema_slow_span)
    vwap = vwap_series[-1]

    dev20 = dev20_buf[-1] if dev20_buf else None
    s_dev20 = _theil_sen_slope(dev20_buf, config.dev20_window)
    ss_dev20 = _theil_sen_slope(s_dev20_buf, config.s_dev20_window)
    valley = (s_dev20 + 10.0 * ss_dev20) if (s_dev20 is not None and ss_dev20 is not None) else None
    s_valley = _theil_sen_slope(valley_buf, config.valley_window)

    # 5. Check buy conditions (signal-dev20.md)
    buy_signal = (
        s_dev20 is not None
        and valley is not None
        and s_valley is not None
        and ema20 < vwap
        and s_dev20 > valley > 0
        and s_valley < 0
        and abs(s_valley * 10) > s_dev20
    )

    if not buy_signal:
        return Dev20SignalResult(
            signal="wait",
            dev20=dev20,
            s_dev20=s_dev20,
            ss_dev20=ss_dev20,
            valley=valley,
            s_valley=s_valley,
            ema5=ema5,
            ema10=ema10,
            ema20=ema20,
            vwap=vwap,
            limit_price=None,
        )

    # 6. Compute limit price: (prev_bar.low + prev_bar.close) / 2
    prev = bars[current_idx - 1]
    limit_price = round((prev.low + prev.close) / 2.0, 2)

    return Dev20SignalResult(
        signal="buy_now",
        dev20=dev20,
        s_dev20=s_dev20,
        ss_dev20=ss_dev20,
        valley=valley,
        s_valley=s_valley,
        ema5=ema5,
        ema10=ema10,
        ema20=ema20,
        vwap=vwap,
        limit_price=limit_price,
    )
