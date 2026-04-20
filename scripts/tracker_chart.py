"""Render local tracker analysis charts using V2 intraday low signal (1m bars).

Output:
- one PNG per symbol
- all trading dates rendered in the same figure
- each date is labelled on its own price + volume panel
- Reference lines: EMA5 (pink), EMA20 (purple), VWAP (orange), Prev Bar Mid (cyan)
- Signal markers: PLACE (green triangle) with reversal type annotation
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from intraday_auto_trading.app import build_bar_data_service
from intraday_auto_trading.config import load_settings
from intraday_auto_trading.models import MinuteBar
from intraday_auto_trading.services.intraday_low_signal import (
    IntradayLowConfig,
    IntradayLowSignalResult,
    compute_intraday_low_signal,
)


SYMBOLS = ["JEPI", "JEPQ", "SCHD", "DGRW"]
START_DATE = date(2026, 4, 13)
END_DATE = date(2026, 4, 17)
SESSION_OPEN = time(9, 30)
SESSION_CLOSE = time(16, 0)
OUTPUT_ROOT = Path("artifacts/tracker_analysis")
OUTPUT_MIRROR = Path("/mnt/d/OneDrive/图片/Output")

EMA_CFG = IntradayLowConfig(
    ema_fast_span=5,
    ema_slow_span=20,
    recent_high_lookback=3,
    force_buy_minutes_before_close=15,
)


@dataclass(slots=True)
class TrackerEvent:
    action: str
    timestamp: datetime
    limit_price: float | None
    bar_close: float
    bar_low: float
    reason: str
    reversal_types: list[str]


@dataclass(slots=True)
class DayPayload:
    trade_date: date
    bars: list[MinuteBar]
    source: str


def _trading_days(start: date, end: date) -> list[date]:
    days: list[date] = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def _compute_ema_series(closes: list[float], span: int) -> list[float]:
    """Return a per-bar EMA series (same length as closes)."""
    alpha = 2.0 / (span + 1)
    result: list[float] = []
    ema = closes[0]
    for c in closes:
        ema = alpha * c + (1.0 - alpha) * ema
        result.append(ema)
    return result


def _build_vwap_series(bars: Sequence[MinuteBar]) -> list[float]:
    cum_pv = 0.0
    cum_v = 0.0
    result: list[float] = []
    for bar in bars:
        cum_pv += bar.close * bar.volume
        cum_v += bar.volume
        result.append(bar.close if cum_v <= 0 else cum_pv / cum_v)
    return result


def _build_prev_mid_series(bars: Sequence[MinuteBar]) -> list[float]:
    result: list[float] = []
    for i, bar in enumerate(bars):
        prev = bars[i - 1] if i > 0 else bar
        result.append((prev.close + prev.low) / 2.0)
    return result


def _simulate_v2_events(bars: Sequence[MinuteBar], trade_date: date) -> list[TrackerEvent]:
    if not bars:
        return []

    session_close_dt = datetime.combine(trade_date, SESSION_CLOSE)
    force_buy_time = session_close_dt - timedelta(minutes=EMA_CFG.force_buy_minutes_before_close)

    events: list[TrackerEvent] = []
    already_bought = False

    for idx in range(len(bars)):
        result: IntradayLowSignalResult = compute_intraday_low_signal(
            bars=bars,
            current_idx=idx,
            force_buy_time=force_buy_time,
            already_bought_today=already_bought,
            config=EMA_CFG,
        )

        if already_bought:
            continue

        if result.signal == "buy_now":
            rtypes = []
            if result.reversal_ok_a:
                rtypes.append("A")
            if result.reversal_ok_b:
                rtypes.append("B")
            if result.reversal_ok_c:
                rtypes.append("C")
            events.append(
                TrackerEvent(
                    action="PLACE",
                    timestamp=bars[idx].timestamp,
                    limit_price=result.limit_price,
                    bar_close=bars[idx].close,
                    bar_low=bars[idx].low,
                    reason="buy_now",
                    reversal_types=rtypes,
                )
            )
            already_bought = True

        elif result.signal == "force_buy":
            events.append(
                TrackerEvent(
                    action="FORCE",
                    timestamp=bars[idx].timestamp,
                    limit_price=bars[idx].close,
                    bar_close=bars[idx].close,
                    bar_low=bars[idx].low,
                    reason="force_buy",
                    reversal_types=[],
                )
            )
            already_bought = True

    return events


def _plot_candlesticks(ax, bars: Sequence[MinuteBar], bar_width_days: float) -> None:
    for bar in bars:
        x = mdates.date2num(bar.timestamp)
        color = "#0a7f39" if bar.close >= bar.open else "#c0392b"
        ax.vlines(x, bar.low, bar.high, color=color, linewidth=0.6)
        lower = min(bar.open, bar.close)
        height = max(abs(bar.close - bar.open), 0.0001)
        rect = Rectangle(
            (x - bar_width_days / 2, lower),
            bar_width_days,
            height,
            facecolor=color,
            edgecolor=color,
            linewidth=0.4,
        )
        ax.add_patch(rect)
    ax.xaxis_date()


def _render_day_panel(price_ax, vol_ax, payload: DayPayload) -> None:
    bars = payload.bars
    times = [bar.timestamp for bar in bars]
    bar_width = 0.8 / (24 * 60)

    closes = [b.close for b in bars]
    ema5_series = _compute_ema_series(closes, EMA_CFG.ema_fast_span)
    ema20_series = _compute_ema_series(closes, EMA_CFG.ema_slow_span)
    vwap_series = _build_vwap_series(bars)
    prev_mid_series = _build_prev_mid_series(bars)
    events = _simulate_v2_events(bars, payload.trade_date)

    _plot_candlesticks(price_ax, bars, bar_width)

    price_ax.plot(times, ema5_series, color="#e91e63", linewidth=1.0, label="EMA5", alpha=0.85)
    price_ax.plot(times, ema20_series, color="#7b1fa2", linewidth=1.2, label="EMA20", alpha=0.85)
    price_ax.plot(times, vwap_series, color="#f57f17", linewidth=1.4, label="VWAP", zorder=3)
    price_ax.plot(
        times,
        prev_mid_series,
        color="#00838f",
        linewidth=1.0,
        linestyle="-.",
        label="Prev Bar Mid",
        alpha=0.9,
    )

    for event in events:
        y = event.limit_price if event.limit_price is not None else event.bar_close
        if event.action == "PLACE":
            price_ax.scatter([event.timestamp], [y], marker="^", color="#2e7d32", s=90, zorder=6)
            rtype_str = "|".join(event.reversal_types) if event.reversal_types else "?"
            label = (
                f"PLACE [{rtype_str}]\n"
                f"limit={y:.2f}"
            )
            color = "#1b5e20"
            edge = "#2e7d32"
        else:
            price_ax.scatter([event.timestamp], [y], marker="D", color="#ff6f00", s=70, zorder=6)
            label = f"FORCE\nlimit={y:.2f}"
            color = "#e65100"
            edge = "#ff6f00"

        price_ax.annotate(
            label,
            (event.timestamp, y),
            textcoords="offset points",
            xytext=(8, 4),
            fontsize=7,
            color=color,
            bbox={
                "boxstyle": "round,pad=0.2",
                "facecolor": "white",
                "alpha": 0.78,
                "edgecolor": edge,
            },
        )

    price_ax.set_title(f"{payload.trade_date.isoformat()} | {payload.source} | V2 1m signal")
    price_ax.set_ylabel("Price")
    price_ax.grid(True, alpha=0.2)
    price_ax.legend(loc="upper left", fontsize=7, ncol=4)

    colors = ["#0a7f39" if bar.close >= bar.open else "#c0392b" for bar in bars]
    vol_ax.bar(times, [bar.volume for bar in bars], width=bar_width, color=colors, alpha=0.8)
    vol_ax.set_ylabel("Vol", fontsize=7)
    vol_ax.grid(True, axis="y", alpha=0.2)
    vol_ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    vol_ax.set_xlabel("Time (ET)")


def render_symbol_chart(
    output_path: Path,
    symbol: str,
    day_payloads: Sequence[DayPayload],
) -> None:
    valid_payloads = [payload for payload in day_payloads if payload.bars]
    if not valid_payloads:
        print(f"[skip] {symbol}: no bars across requested dates")
        return

    fig, axes = plt.subplots(
        len(valid_payloads) * 2,
        1,
        figsize=(18, max(10, len(valid_payloads) * 5)),
        sharex=False,
        gridspec_kw={"height_ratios": [4, 1] * len(valid_payloads)},
    )
    if hasattr(axes, "flat"):
        axes = list(axes.flat)
    elif isinstance(axes, Sequence):
        axes = list(axes)
    else:
        axes = [axes]

    fig.suptitle(
        f"{symbol} | 1m V2 Signal Analysis | {START_DATE.isoformat()} -> {END_DATE.isoformat()}",
        fontsize=13,
    )

    for idx, payload in enumerate(valid_payloads):
        price_ax = axes[idx * 2]
        vol_ax = axes[idx * 2 + 1]
        _render_day_panel(price_ax, vol_ax, payload)

    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    print(f"  saved -> {output_path}")

    mirror_path = OUTPUT_MIRROR / output_path.name
    OUTPUT_MIRROR.mkdir(parents=True, exist_ok=True)
    fig.savefig(mirror_path, dpi=150, bbox_inches="tight")
    print(f"  saved -> {mirror_path}")

    plt.close(fig)


def main() -> None:
    settings = load_settings("config/settings.toml")
    data_service = build_bar_data_service(settings)

    print(f"Symbols: {SYMBOLS}")
    print(f"Date range: {START_DATE} -> {END_DATE}")
    print(f"Rule: V2 intraday low signal (EMA{EMA_CFG.ema_fast_span}/EMA{EMA_CFG.ema_slow_span}, 1m bars)")
    print(f"Limit: min(VWAP, prev bar mid)")
    print()

    bars_by_symbol = data_service.get_bars(SYMBOLS, "1m", START_DATE, END_DATE)

    days = _trading_days(START_DATE, END_DATE)
    payloads_by_symbol: dict[str, list[DayPayload]] = {s: [] for s in SYMBOLS}

    for symbol in SYMBOLS:
        all_bars = bars_by_symbol[symbol]
        for trade_date in days:
            day_start = datetime.combine(trade_date, SESSION_OPEN)
            day_end = datetime.combine(trade_date, SESSION_CLOSE)
            day_bars = [
                b for b in all_bars
                if day_start <= b.timestamp.replace(tzinfo=None) < day_end
            ]
            print(f"  [{symbol}] {trade_date}: {len(day_bars)} bars")
            payloads_by_symbol[symbol].append(
                DayPayload(trade_date=trade_date, bars=day_bars, source="unified")
            )

    print()
    for symbol in SYMBOLS:
        output_path = OUTPUT_ROOT / f"{symbol}.png"
        render_symbol_chart(output_path, symbol, payloads_by_symbol[symbol])
        print()

    print(f"Done. Charts saved to {OUTPUT_ROOT}/")


if __name__ == "__main__":
    main()
