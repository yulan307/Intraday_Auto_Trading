"""15m 追踪策略可视化脚本

对 core 标的池（JEPI/JEPQ/SCHD/DGRW），输出 2026-04-06 到 2026-04-10
每个标的每天一张图，包含：
  - 15m K 线
  - 参考线 1：追踪中的最低 low（running lowest bar.low，阶梯线）
  - 参考线 2：15m bar 累计 VWAP
  - 参考线 3：当日平均波动率带（close ± dev，其中 dev 为截至当前 bar 的平均区间/close）
  - 标注：FifteenMinuteTracker 的下单时机（PLACE）和撤单时机（CANCEL）

输出目录：artifacts/tracker_analysis/
用法：
    python scripts/tracker_chart.py
"""
from __future__ import annotations

import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Sequence

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from intraday_auto_trading.config import load_settings
from intraday_auto_trading.persistence.market_data_repository import SqliteMarketDataRepository
from intraday_auto_trading.models import MinuteBar
from intraday_auto_trading.services.tracker import FifteenMinuteTracker

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SYMBOLS = ["JEPI", "JEPQ", "SCHD", "DGRW"]
START_DATE = date(2026, 4, 6)
END_DATE = date(2026, 4, 10)
SESSION_OPEN = time(9, 30)
SESSION_CLOSE = time(16, 0)
OUTPUT_ROOT = Path("artifacts/tracker_analysis")


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def _trading_days(start: date, end: date) -> list[date]:
    days = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            days.append(cur)
        cur += timedelta(days=1)
    return days


def _build_vwap_series(bars: Sequence[MinuteBar]) -> list[float]:
    """Cumulative VWAP using bar close × volume."""
    cum_pv = 0.0
    cum_v = 0.0
    result = []
    for bar in bars:
        cum_pv += bar.close * bar.volume
        cum_v += bar.volume
        result.append(bar.close if cum_v <= 0 else cum_pv / cum_v)
    return result


def _build_dev_band(bars: Sequence[MinuteBar]) -> tuple[list[float], list[float], list[float]]:
    """Running average volatility band around each bar's close.

    dev_i = mean of (bar.high - bar.low) / bar.close for bar 0..i
    upper_i = bar_i.close * (1 + dev_i)
    lower_i = bar_i.close * (1 - dev_i)
    Also returns the dev series itself for annotation.
    """
    upper, lower, devs = [], [], []
    running_vols: list[float] = []
    for bar in bars:
        vol = (bar.high - bar.low) / bar.close if bar.close > 0 else 0.0
        running_vols.append(vol)
        dev = sum(running_vols) / len(running_vols)
        devs.append(dev)
        upper.append(bar.close * (1 + dev))
        lower.append(bar.close * (1 - dev))
    return upper, lower, devs


def _build_lowest_low_series(bars: Sequence[MinuteBar]) -> list[float]:
    """Running minimum of bar.low."""
    result = []
    cur_min = float("inf")
    for bar in bars:
        cur_min = min(cur_min, bar.low)
        result.append(cur_min)
    return result


def _simulate_tracker(
    bars: Sequence[MinuteBar],
    confirmation_bars: int,
    limit_price_factor: float,
) -> list[dict]:
    """Run FifteenMinuteTracker and collect events for annotation.

    Returns a list of dicts:
        action:       "PLACE" | "CANCEL"
        bar_index:    index into bars
        timestamp:    bar.timestamp
        bar_close:    bar.close
        bar_low:      bar.low
        lowest_close: tracker.lowest_close at that bar
        limit_price:  computed limit price (PLACE only, else None)
        dev:          running average volatility dev at that bar
    """
    tracker = FifteenMinuteTracker(
        confirmation_bars=confirmation_bars,
        limit_price_factor=limit_price_factor,
    )
    # Precompute running dev so we can annotate each event
    _, _, devs = _build_dev_band(bars)

    events = []
    active_order = False

    for i, bar in enumerate(bars):
        decision = tracker.observe(bar.close)
        dev = devs[i]

        if decision.should_cancel_order and active_order:
            events.append({
                "action": "CANCEL",
                "bar_index": i,
                "timestamp": bar.timestamp,
                "bar_close": bar.close,
                "bar_low": bar.low,
                "lowest_close": decision.lowest_close,
                "limit_price": None,
                "dev": dev,
            })
            active_order = False

        if decision.should_place_order and decision.limit_price is not None and not active_order:
            events.append({
                "action": "PLACE",
                "bar_index": i,
                "timestamp": bar.timestamp,
                "bar_close": bar.close,
                "bar_low": bar.low,
                "lowest_close": decision.lowest_close,
                "limit_price": decision.limit_price,
                "dev": dev,
            })
            active_order = True

    return events


# ---------------------------------------------------------------------------
# Chart rendering
# ---------------------------------------------------------------------------

def _plot_candlesticks(ax, bars: Sequence[MinuteBar], bar_width_days: float) -> None:
    for bar in bars:
        x = mdates.date2num(bar.timestamp)
        color = "#0a7f39" if bar.close >= bar.open else "#c0392b"
        ax.vlines(x, bar.low, bar.high, color=color, linewidth=0.8)
        lower = min(bar.open, bar.close)
        height = max(abs(bar.close - bar.open), 0.0001)
        rect = Rectangle(
            (x - bar_width_days / 2, lower),
            bar_width_days,
            height,
            facecolor=color,
            edgecolor=color,
            linewidth=0.5,
        )
        ax.add_patch(rect)
    ax.xaxis_date()


def render_day_chart(
    output_path: Path,
    symbol: str,
    trade_date: date,
    bars: Sequence[MinuteBar],
    confirmation_bars: int,
    limit_price_factor: float,
) -> None:
    if not bars:
        print(f"  [skip] {symbol} {trade_date}: no bars")
        return

    times = [bar.timestamp for bar in bars]
    bar_width = 14 / (24 * 60)  # 14-minute visual width for 15m bars

    vwap = _build_vwap_series(bars)
    upper, lower, devs = _build_dev_band(bars)
    lowest_low = _build_lowest_low_series(bars)
    events = _simulate_tracker(bars, confirmation_bars, limit_price_factor)

    fig, (price_ax, vol_ax) = plt.subplots(
        2, 1, figsize=(14, 8),
        sharex=True,
        gridspec_kw={"height_ratios": [4, 1]},
    )
    fig.suptitle(
        f"{symbol}  {trade_date}  |  15m Tracker Analysis  "
        f"(confirmation={confirmation_bars} bars, factor={limit_price_factor})",
        fontsize=11,
    )

    # --- Candlesticks ---
    _plot_candlesticks(price_ax, bars, bar_width)

    # --- Reference line 1: running lowest low (step) ---
    price_ax.step(
        times, lowest_low,
        where="post",
        color="#1565c0", linewidth=1.2,
        label="Lowest Low", linestyle="--", alpha=0.8,
    )

    # --- Reference line 2: VWAP ---
    price_ax.plot(
        times, vwap,
        color="#f57f17", linewidth=1.5,
        label="VWAP", zorder=3,
    )

    # --- Reference line 3: dev band (close ± dev) ---
    price_ax.plot(
        times, upper,
        color="#7b1fa2", linewidth=0.9,
        linestyle=":", label="Close×(1+dev)", alpha=0.7,
    )
    price_ax.plot(
        times, lower,
        color="#7b1fa2", linewidth=0.9,
        linestyle=":", label="Close×(1−dev)", alpha=0.7,
    )
    price_ax.fill_between(
        times, upper, lower,
        color="#ce93d8", alpha=0.10,
    )

    # --- PLACE / CANCEL markers and annotations ---
    for ev in events:
        x = ev["timestamp"]
        y_close = ev["bar_close"]
        y_low = ev["bar_low"]
        dev_pct = ev["dev"] * 100

        if ev["action"] == "PLACE":
            lp = ev["limit_price"]
            price_ax.scatter([x], [lp], marker="^", color="#2e7d32", s=100, zorder=6)
            label = (
                f"PLACE\n"
                f"low={y_low:.2f}\n"
                f"close={y_close:.2f}\n"
                f"limit={lp:.2f}\n"
                f"dev={dev_pct:.2f}%"
            )
            price_ax.annotate(
                label, (x, lp),
                textcoords="offset points", xytext=(8, -2),
                fontsize=7, color="#1b5e20",
                bbox={"boxstyle": "round,pad=0.2", "facecolor": "white",
                      "alpha": 0.75, "edgecolor": "#2e7d32"},
            )
        else:
            price_ax.scatter([x], [y_close], marker="x", color="#c62828", s=80, zorder=6)
            label = (
                f"CANCEL\n"
                f"new_low={y_low:.2f}\n"
                f"close={y_close:.2f}\n"
                f"dev={dev_pct:.2f}%"
            )
            price_ax.annotate(
                label, (x, y_close),
                textcoords="offset points", xytext=(8, 4),
                fontsize=7, color="#8e0000",
                bbox={"boxstyle": "round,pad=0.2", "facecolor": "white",
                      "alpha": 0.75, "edgecolor": "#c62828"},
            )

    price_ax.set_ylabel("Price")
    price_ax.legend(loc="upper left", fontsize=8, ncol=2)
    price_ax.grid(True, alpha=0.2)

    # --- Volume panel ---
    colors = ["#0a7f39" if b.close >= b.open else "#c0392b" for b in bars]
    vol_ax.bar(times, [b.volume for b in bars], width=bar_width, color=colors, alpha=0.8)
    vol_ax.set_ylabel("Vol", fontsize=8)
    vol_ax.grid(True, axis="y", alpha=0.2)
    vol_ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    vol_ax.set_xlabel("Time (ET)")

    fig.autofmt_xdate(rotation=30)
    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  saved → {output_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    settings = load_settings("config/settings.toml")
    repo = SqliteMarketDataRepository(settings.data.market_data_db)
    repo.initialize()

    confirmation_bars = settings.strategy.tracking_confirmation_bars
    limit_price_factor = settings.strategy.tracking_limit_price_factor

    print(f"Tracker params: confirmation_bars={confirmation_bars}, limit_price_factor={limit_price_factor}")
    print(f"Symbols: {SYMBOLS}")
    print(f"Date range: {START_DATE} → {END_DATE}")
    print()

    days = _trading_days(START_DATE, END_DATE)
    source_priority = ["ibkr_direct", "ibkr", "ibkr_derived", "moomoo", "yfinance"]

    for symbol in SYMBOLS:
        print(f"[{symbol}]")
        for trade_date in days:
            day_start = datetime.combine(trade_date, SESSION_OPEN)
            day_end = datetime.combine(trade_date, SESSION_CLOSE)

            bars, source = repo.load_price_bars_with_source_priority(
                symbol=symbol,
                bar_size="15m",
                start=day_start,
                end=day_end,
                source_priority=source_priority,
            )

            if not bars:
                print(f"  [skip] {trade_date}: no 15m bars in DB")
                continue

            print(f"  {trade_date}: {len(bars)} bars from {source}")
            out = OUTPUT_ROOT / symbol / f"{trade_date}.png"
            render_day_chart(
                output_path=out,
                symbol=symbol,
                trade_date=trade_date,
                bars=bars,
                confirmation_bars=confirmation_bars,
                limit_price_factor=limit_price_factor,
            )
        print()

    print(f"Done. Charts saved to {OUTPUT_ROOT}/")


if __name__ == "__main__":
    main()
