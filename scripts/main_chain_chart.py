"""主链路可视化脚本：BarDataService → TrendClassifier（含期权）→ dev20 信号指标

每个标的输出一个 PNG，每天一组 price+indicator panel。
- 图 title：日期 | regime (score) [opts:N] | reason
- 参考线：EMA5 / EMA10 / EMA20 / VWAP
- 指标定义（compute_intraday_low_signal）：
    dev20    = (vwap - ema20) / vwap
    s_dev20  = Theil-Sen slope of last 10 dev20 values
    ss_dev20 = Theil-Sen slope of last 10 s_dev20 values
    valley   = s_dev20 + 10 * ss_dev20
    s_valley = Theil-Sen slope of last 3 valley values
- 买点 buy_now：ema20 < vwap, s_dev20 > valley > 0, s_valley < 0, |s_valley×10| > s_dev20
    limit_price = (prev.low + prev.close) / 2
- 撤单：仅当 SymbolSelector.select 因新的更优标的出现而返回 cancel_symbol 时触发
- dev20_w = dev20_cls * decay_fn，dev20_cls = 100 - 100 * ema20 / close_prev_day
- 标的列表：从 config/symbol_group.toml 的 "core" 组读取（默认）
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Sequence
from zoneinfo import ZoneInfo

import os
import shutil

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib import rcParams
from matplotlib.patches import Rectangle

# Use a CJK-capable font so TrendClassifier reason strings render correctly
rcParams["font.family"] = ["Microsoft YaHei", "Yu Gothic", "DejaVu Sans"]

from intraday_auto_trading.app import build_bar_data_service, build_option_gateways
from intraday_auto_trading.config import load_settings
from intraday_auto_trading.interfaces.brokers import MarketDataGateway
from intraday_auto_trading.interfaces.repositories import MarketDataRepository
from intraday_auto_trading.models import Dev20SignalResult, MinuteBar, OptionQuote, Regime, TrendInput, TrendSignal
from intraday_auto_trading.persistence.market_data_repository import SqliteMarketDataRepository
from intraday_auto_trading.services.intraday_low_signal import (
    IntradayLowConfig,
    _compute_ema,
    _compute_vwap_series,
    compute_intraday_low_signal,
)
from intraday_auto_trading.services.option_data_service import load_option_quotes_batch
from intraday_auto_trading.services.selector import SymbolSelector
from intraday_auto_trading.services.session_data_service import load_session_metrics
from intraday_auto_trading.services.trend_classifier import TrendClassifier

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
# SYMBOLS 从 config 读取，见 main()；此处仅保留日期和路径常量
START_DATE = date(2026, 4, 13)
END_DATE = date(2026, 4, 17)
SESSION_OPEN = time(9, 30)
SESSION_CLOSE = time(16, 0)
TREND_EVAL_BARS = 30          # 开盘后 30 分钟用于趋势判断
OUTPUT_ROOT_BASE = Path("artifacts/main_chain_chart")
OUTPUT_MIRROR_BASE = Path("D:/OneDrive/图片/Output")

EMA_CFG = IntradayLowConfig(
    ema_fast_span=5,
    ema10_span=10,
    ema_slow_span=20,
    dev20_window=10,
    s_dev20_window=10,
    valley_window=3,
)

DEV20_WINDOW   = EMA_CFG.dev20_window
S_DEV20_WINDOW = EMA_CFG.s_dev20_window
VALLEY_WINDOW  = EMA_CFG.valley_window

_ET = ZoneInfo("America/New_York")
_UTC = ZoneInfo("UTC")
_ET_OFFSET = timedelta(hours=4)   # EDT = UTC-4


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class DayPayload:
    trade_date: date
    bars: list[MinuteBar]          # UTC naive（原始）
    trend_signal: TrendSignal | None
    option_count: int              # 该日加载到的期权快照数量
    close_prev_day: float | None   # 前一交易日最后一根 bar 的 close（用于 dev20_cls）


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _trading_days(start: date, end: date) -> list[date]:
    days: list[date] = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def _utc_session_bounds(trade_date: date) -> tuple[datetime, datetime]:
    """返回该交易日 session_open / session_close 的 UTC naive 边界（用于 DB 查询）。"""
    open_utc = (
        datetime.combine(trade_date, SESSION_OPEN, tzinfo=_ET)
        .astimezone(_UTC)
        .replace(tzinfo=None)
    )
    close_utc = (
        datetime.combine(trade_date, SESSION_CLOSE, tzinfo=_ET)
        .astimezone(_UTC)
        .replace(tzinfo=None)
    )
    return open_utc, close_utc


def _to_et(bars: list[MinuteBar]) -> list[MinuteBar]:
    return [
        MinuteBar(
            timestamp=b.timestamp - _ET_OFFSET,
            open=b.open, high=b.high, low=b.low,
            close=b.close, volume=b.volume,
        )
        for b in bars
    ]


def _prev_close(all_bars: list[MinuteBar], open_utc: datetime) -> float | None:
    """前一交易日最后一根 bar 的 close（open_utc 之前的最后一条）。"""
    prev_bars = [b for b in all_bars if b.timestamp < open_utc]
    return prev_bars[-1].close if prev_bars else None


def _compute_dev20_cls(ema20: float | None, close_prev: float | None) -> float | None:
    """dev20_cls = 100 - 100 * ema20 / close_prev_day.

    Negative when ema20 > close_prev (price declines from previous close).
    Returns None when either input is unavailable or close_prev is zero.
    """
    if ema20 is None or not close_prev:
        return None
    return 100.0 - 100.0 * ema20 / close_prev


# ---------------------------------------------------------------------------
# 趋势分类（每日）
# ---------------------------------------------------------------------------
def _classify_day_trend(
    symbol: str,
    trade_date: date,
    et_bars: list[MinuteBar],
    option_quotes: list[OptionQuote],
    repository: MarketDataRepository,
    session_gateways: dict[str, MarketDataGateway],
) -> tuple[TrendSignal | None, int]:
    """用前 TREND_EVAL_BARS 根 ET 时间 bar + 已拉好的期权数据做趋势分类。

    Returns:
        (TrendSignal | None, option_count)
    """
    if len(et_bars) < TREND_EVAL_BARS:
        return None, 0

    eval_bars = et_bars[:TREND_EVAL_BARS]
    eval_time_et = eval_bars[-1].timestamp

    metrics = load_session_metrics(
        symbol=symbol,
        trade_date=trade_date.isoformat(),
        eval_time=eval_time_et,
        repository=repository,
        gateways=session_gateways,
        bars=eval_bars,
    )

    trend_input = TrendInput(
        symbol=symbol,
        eval_time=eval_time_et,
        official_open=metrics.official_open or eval_bars[0].open,
        last_price=metrics.last_price or eval_bars[-1].close,
        session_vwap=metrics.session_vwap or eval_bars[-1].close,
        minute_bars=eval_bars,
        option_quotes=option_quotes,
    )
    signal = TrendClassifier().classify(trend_input)
    return signal, len(option_quotes)


# ---------------------------------------------------------------------------
# 联合仿真回路：classify → select → 记录事件
#
# 撤单逻辑：仅当 SymbolSelector.select 返回 action="place_order" 且
# cancel_symbol 非空时记录撤单事件，代表旧单被更优标的替换。
# EMA5 < EMA10 不再作为独立撤单触发条件。
# ---------------------------------------------------------------------------
def _simulate_joint(
    symbol_et_bars: dict[str, list[MinuteBar]],
    symbol_trend_signals: dict[str, TrendSignal | None],
    config: IntradayLowConfig,
    close_prev_day_by_sym: dict[str, float | None] | None = None,
) -> tuple[dict[str, list[dict]], dict[str, dict[str, list]]]:
    """联合仿真：classify 阶段 + 1m 追踪阶段（跨标的共享 active_order 状态）。

    dev20_w 使用 dev20_cls = 100 - 100 * ema20 / close_prev_day 计算；
    SymbolSelector.select 调用前将 Dev20SignalResult.dev20 替换为 dev20_cls。

    Returns
    -------
    events_by_sym:
        每个标的的事件列表，事件类型：
        - "classify": classify 阶段结果（每标的一条，idx=TREND_EVAL_BARS-1）
        - "buy_now":  1m 追踪阶段出现买点信号（包含 select 判断结果）
        - "cancel":   旧单被新标的替换时记录在旧标的上
    indicators_by_sym:
        每个标的的逐 bar 指标序列（ema5/10/20/vwap/dev20/s_dev20/ss_dev20/valley/s_valley）
    """
    prev_close = close_prev_day_by_sym or {}
    symbols = list(symbol_et_bars.keys())

    # ── Phase A: 预计算所有 bar 的信号（O(n²) per symbol，脚本可接受）──────────
    results_grid: dict[str, list] = {}
    indicators_by_sym: dict[str, dict[str, list]] = {}

    for sym in symbols:
        bars = symbol_et_bars[sym]
        grid = [
            compute_intraday_low_signal(bars=bars, current_idx=idx, config=config)
            for idx in range(len(bars))
        ]
        results_grid[sym] = grid
        indicators_by_sym[sym] = {
            "ema5":     [r.ema5     for r in grid],
            "ema10":    [r.ema10    for r in grid],
            "ema20":    [r.ema20    for r in grid],
            "vwap":     [r.vwap     for r in grid],
            "dev20":    [r.dev20    for r in grid],
            "s_dev20":  [r.s_dev20  for r in grid],
            "ss_dev20": [r.ss_dev20 for r in grid],
            "valley":   [r.valley   for r in grid],
            "s_valley": [r.s_valley for r in grid],
        }

    events_by_sym: dict[str, list[dict]] = {sym: [] for sym in symbols}

    # ── Phase B: Classify 阶段（复现 TradingWorkflow.classify_and_select_initial）──
    classify_idx = TREND_EVAL_BARS - 1  # 第 29 根 bar（0-indexed）

    # 检查是否所有标的都有足够数据并为 EARLY_BUY
    active_syms_for_classify = [
        sym for sym in symbols
        if len(symbol_et_bars[sym]) > classify_idx
    ]

    classify_dev20: dict[str, float] = {}
    classify_dev20_w: dict[str, float] = {}
    classify_vwap: dict[str, float] = {}
    fail_reason: str | None = None
    best_sym: str | None = None
    initial_active_order: tuple[str, float] | None = None

    # EARLY_BUY 检查
    all_early_buy = all(
        symbol_trend_signals.get(sym) is not None
        and symbol_trend_signals[sym].regime is Regime.EARLY_BUY
        for sym in active_syms_for_classify
    ) and len(active_syms_for_classify) == len(symbols)

    if not all_early_buy:
        non_eb = [
            sym for sym in active_syms_for_classify
            if symbol_trend_signals.get(sym) is None
            or symbol_trend_signals[sym].regime is not Regime.EARLY_BUY
        ]
        fail_reason = f"not_all_EARLY_BUY: {non_eb}"
    else:
        # 计算各标的 classify 时点的 dev20_cls = 100 - 100*ema20/close_prev
        for sym in active_syms_for_classify:
            bars30 = symbol_et_bars[sym][:TREND_EVAL_BARS]
            vwap_series = _compute_vwap_series(bars30, len(bars30) - 1)
            vwap30 = vwap_series[-1] if vwap_series else 0.0
            ema20_30 = _compute_ema([b.close for b in bars30], 20)
            close_prev = prev_close.get(sym)
            dev20_cls_val = _compute_dev20_cls(ema20_30, close_prev)
            # fallback to vwap-based dev20 if no prev close available
            dev20 = dev20_cls_val if dev20_cls_val is not None else (
                (vwap30 - ema20_30) / vwap30 if vwap30 else 0.0
            )
            classify_dev20[sym] = dev20
            classify_dev20_w[sym] = dev20 * SymbolSelector._decay_fn(0)
            classify_vwap[sym] = vwap30

        not_negative = [sym for sym, d in classify_dev20.items() if d >= 0]
        if not_negative:
            fail_reason = f"dev20≥0: {not_negative}"
        else:
            best_sym = max(classify_dev20_w, key=lambda s: classify_dev20_w[s])
            initial_active_order = (best_sym, classify_dev20_w[best_sym])

    # 为每个标的写入 classify 事件
    for sym in symbols:
        sig = symbol_trend_signals.get(sym)
        events_by_sym[sym].append({
            "type": "classify",
            "idx": classify_idx,
            "price": symbol_et_bars[sym][classify_idx].close if len(symbol_et_bars[sym]) > classify_idx else 0.0,
            "regime": sig.regime.value if sig else "N/A",
            "score": sig.score if sig else None,
            "classify_dev20": classify_dev20.get(sym),
            "classify_dev20_w": classify_dev20_w.get(sym),
            "all_dev20s": dict(classify_dev20),
            "selected": sym == best_sym,
            "init_order": best_sym is not None,
            "best_sym": best_sym,
            "init_vwap": classify_vwap.get(best_sym) if best_sym else None,
            "fail_reason": fail_reason,
        })

    # ── Phase C: 1m 追踪阶段 ──────────────────────────────────────────────────
    selector = SymbolSelector()
    active_order: tuple[str, float] | None = initial_active_order
    max_bars = max((len(symbol_et_bars[s]) for s in symbols), default=0)

    for idx in range(TREND_EVAL_BARS, max_bars):
        intraday_signals = {
            sym: results_grid[sym][idx]
            for sym in symbols
            if idx < len(results_grid[sym])
        }
        if not intraday_signals:
            continue

        # 用 dev20_cls 替换 sig.dev20，供 selector 内部的 dev20_w 比较
        modified_signals: dict[str, Dev20SignalResult] = {}
        for s, sig in intraday_signals.items():
            cls_val = _compute_dev20_cls(sig.ema20, prev_close.get(s))
            modified_signals[s] = replace(sig, dev20=cls_val) if cls_val is not None else sig

        decision = selector.select(modified_signals, active_order=active_order)

        # buy_now 事件：对每个出现买点信号的标的记录 select 结果
        for sym, result in intraday_signals.items():
            if result.signal != "buy_now":
                continue
            result_modified = modified_signals[sym]
            dev20_w = (
                result_modified.dev20 * SymbolSelector._decay_fn(0)
                if result_modified.dev20 is not None else None
            )
            is_placed = (decision.action == "place_order" and decision.symbol == sym)
            # 比较集合：所有标的当前 dev20_cls_w + active_order 记录的 dev20_w
            comparison: dict[str, float] = {
                s: modified_signals[s].dev20 * SymbolSelector._decay_fn(0)
                for s in modified_signals
                if modified_signals[s].dev20 is not None
            }
            if active_order is not None:
                comparison[f"[active:{active_order[0]}]"] = active_order[1]
            global_max_w = max(comparison.values()) if comparison else None
            lp = (
                result.limit_price
                if result.limit_price is not None
                else symbol_et_bars[sym][idx].close
            )
            events_by_sym[sym].append({
                "type": "buy_now",
                "idx": idx,
                "price": lp,
                "limit_price": result.limit_price,
                "dev20": result.dev20,
                "dev20_cls": result_modified.dev20,
                "dev20_w": dev20_w,
                "decision": decision.action,   # "place_order" | "wait" | ...
                "is_placed": is_placed,
                "global_max_w": global_max_w,
                "comparison": comparison,
                "cancel_symbol": decision.cancel_symbol if is_placed else None,
            })

        # 撤单事件：仅来源于 select 因新下单替换旧单（cancel_symbol 非空）
        if decision.action == "place_order" and decision.cancel_symbol is not None:
            cancel_sym = decision.cancel_symbol
            if cancel_sym in intraday_signals:
                events_by_sym[cancel_sym].append({
                    "type": "cancel",
                    "idx": idx,
                    "price": symbol_et_bars[cancel_sym][idx].close,
                    "reason": f"replaced by {decision.symbol}",
                    "new_sym": decision.symbol,
                    "new_dev20_w": decision.dev20_at_order,
                    "old_dev20_w": active_order[1] if active_order else None,
                })

        # 更新共享的 active_order 状态
        if decision.action == "place_order":
            active_order = (decision.symbol, decision.dev20_at_order)

    return events_by_sym, indicators_by_sym


# ---------------------------------------------------------------------------
# 绘图
# ---------------------------------------------------------------------------
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


def _render_day_panel(
    price_ax,
    vol_ax,
    et_bars: list[MinuteBar],
    events: list[dict],
    indicators: dict[str, list],
    trade_date: date,
    trend_signal: TrendSignal | None,
    option_count: int,
) -> None:
    if not et_bars:
        price_ax.set_title(f"{trade_date} | no data", fontsize=8, loc="left")
        return

    times = [b.timestamp for b in et_bars]
    bar_width = 0.8 / (24 * 60)

    _plot_candlesticks(price_ax, et_bars, bar_width)

    def _plot_series(ax, times, series, **kwargs):
        xs = [t for t, v in zip(times, series) if v is not None]
        ys = [v for v in series if v is not None]
        if xs:
            ax.plot(xs, ys, **kwargs)

    _plot_series(price_ax, times, indicators["ema5"],
                 color="#e91e63", linewidth=1.0, label="EMA5", alpha=0.85)
    _plot_series(price_ax, times, indicators["ema10"],
                 color="#ff6f00", linewidth=1.0, label="EMA10", alpha=0.85)
    _plot_series(price_ax, times, indicators["ema20"],
                 color="#7b1fa2", linewidth=1.2, label="EMA20", alpha=0.85)
    _plot_series(price_ax, times, indicators["vwap"],
                 color="#1565c0", linewidth=1.2, label="VWAP", alpha=0.85, linestyle="--")

    # ── Classify 标注 ────────────────────────────────────────────────────────
    for ev in (e for e in events if e["type"] == "classify"):
        idx = ev["idx"]
        if idx >= len(times):
            continue
        x = times[idx]
        price_ax.axvline(x, color="#90a4ae", linewidth=1.0, linestyle="--", alpha=0.7, zorder=2)

        regime_str = ev["regime"]
        if ev["score"] is not None:
            regime_str += f" ({ev['score']:.2f})"
        d20 = ev["classify_dev20"]
        d20_str = f"dev20={d20:.4f}" if d20 is not None else "dev20=N/A"

        if ev["selected"]:
            vwap_v = ev["init_vwap"]
            decision_str = f"✓ INIT ORDER @ {vwap_v:.2f}" if vwap_v else "✓ INIT ORDER"
            box_fc = "#c8e6c9"
        elif ev["init_order"] and not ev["selected"]:
            best = ev["best_sym"] or "?"
            best_d20 = ev["all_dev20s"].get(best)
            best_str = f"{best} ({best_d20:.4f})" if best_d20 is not None else best
            decision_str = f"→ {best_str}"
            box_fc = "#f5f5f5"
        else:
            reason = ev["fail_reason"] or "classify failed"
            decision_str = f"✗ skip"
            box_fc = "#ffebee"
            regime_str += f"\n{reason}"

        ann_text = f"CLASSIFY\n{regime_str}\n{d20_str}\n{decision_str}"
        ymax = price_ax.get_ylim()[1] if price_ax.get_ylim()[1] != 1.0 else et_bars[idx].high
        price_ax.annotate(
            ann_text,
            xy=(x, ymax),
            xytext=(4, -4),
            textcoords="offset points",
            fontsize=6,
            color="#37474f",
            va="top",
            bbox={
                "boxstyle": "round,pad=0.3",
                "facecolor": box_fc,
                "alpha": 0.88,
                "edgecolor": "#90a4ae",
            },
            zorder=7,
        )

    # ── buy_now 标注 ─────────────────────────────────────────────────────────
    for ev in (e for e in events if e["type"] == "buy_now"):
        idx = ev["idx"]
        if idx >= len(times):
            continue
        x = times[idx]
        y = ev["price"]
        if ev["is_placed"]:
            color, size = "#00c853", 100
            cancel_note = (
                f" [cancel:{ev['cancel_symbol']}]" if ev["cancel_symbol"] else ""
            )
            lp_str = f"lim={ev['limit_price']:.2f}" if ev["limit_price"] else ""
            w_str = f"dev20_w={ev['dev20_w']:.4f}" if ev["dev20_w"] is not None else ""
            ann_lines = [l for l in [lp_str, w_str, f"▶ PLACE{cancel_note}"] if l]
            text_color = "#1b5e20"
            edge_color = "#00c853"
        else:
            color, size = "#ff8f00", 60
            w_str = f"dev20_w={ev['dev20_w']:.4f}" if ev["dev20_w"] is not None else ""
            max_w = ev["global_max_w"]
            max_str = f"max={max_w:.4f}" if max_w is not None else ""
            ann_lines = [l for l in [w_str, "✗ WAIT", max_str] if l]
            text_color = "#e65100"
            edge_color = "#ff8f00"

        price_ax.scatter([x], [y], marker="^", color=color, s=size, zorder=6)
        if ann_lines:
            price_ax.annotate(
                "\n".join(ann_lines),
                xy=(x, y),
                xytext=(6, 6),
                textcoords="offset points",
                fontsize=6,
                color=text_color,
                bbox={
                    "boxstyle": "round,pad=0.2",
                    "facecolor": "white",
                    "alpha": 0.75,
                    "edgecolor": edge_color,
                },
            )

    # ── cancel 标注（仅来源于新下单替换旧单）────────────────────────────────
    for ev in (e for e in events if e["type"] == "cancel"):
        idx = ev["idx"]
        if idx >= len(times):
            continue
        x = times[idx]
        y = ev["price"]
        price_ax.scatter([x], [y], marker="v", color="#d50000", s=70, zorder=6)
        new_sym = ev.get("new_sym", "?")
        old_w = ev.get("old_dev20_w")
        new_w = ev.get("new_dev20_w")
        old_str = f"old={old_w:.4f}" if old_w is not None else ""
        new_str = f"new={new_w:.4f}" if new_w is not None else ""
        ann_lines = [f"→{new_sym}", old_str, new_str]
        price_ax.annotate(
            "\n".join(l for l in ann_lines if l),
            xy=(x, y),
            xytext=(6, -16),
            textcoords="offset points",
            fontsize=6,
            color="#b71c1c",
            bbox={
                "boxstyle": "round,pad=0.2",
                "facecolor": "white",
                "alpha": 0.75,
                "edgecolor": "#d50000",
            },
        )

    # ── 标题 ─────────────────────────────────────────────────────────────────
    if trend_signal:
        ts = trend_signal
        opts_tag = f" [opts:{option_count}]" if option_count else " [no opts]"
        reason_short = ts.reason[:72] + "…" if len(ts.reason) > 72 else ts.reason
        title = f"{trade_date} | {ts.regime.value} ({ts.score:.2f}){opts_tag} | {reason_short}"
    else:
        title = f"{trade_date} | trend: N/A (< {TREND_EVAL_BARS} bars)"
    price_ax.set_title(title, fontsize=8, loc="left")
    price_ax.set_ylabel("Price")
    price_ax.grid(True, alpha=0.2)
    price_ax.legend(loc="upper left", fontsize=7, ncol=5)

    # ── 下方面板：s_dev20 / ss_dev20 / valley / s_valley×10 ─────────────────
    bot_ax = vol_ax

    def _xs_ys(series):
        xs = [t for t, v in zip(times, series) if v is not None]
        ys = [v for v in series if v is not None]
        return xs, ys

    sd_xs,   sd_ys   = _xs_ys(indicators["s_dev20"])
    ssd_xs,  ssd_ys  = _xs_ys(indicators["ss_dev20"])
    val_xs,  val_ys  = _xs_ys(indicators["valley"])
    sval_xs, sval_ys = _xs_ys(indicators["s_valley"])
    sval10_ys = [v * 10 for v in sval_ys]

    l1, = bot_ax.plot(sd_xs,   sd_ys,     color="#1565c0", linewidth=1.2,
                      label=f"s_dev20 (w{DEV20_WINDOW})")
    l2, = bot_ax.plot(ssd_xs,  ssd_ys,    color="#f57f17", linewidth=1.2, linestyle="--",
                      label=f"ss_dev20 (w{S_DEV20_WINDOW})")
    l3, = bot_ax.plot(val_xs,  val_ys,    color="#6a1b9a", linewidth=1.4,
                      label="valley=s_dev20+10·ss_dev20")
    l4, = bot_ax.plot(sval_xs, sval10_ys, color="#00897b", linewidth=1.4, linestyle="-.",
                      label=f"s_valley×10 (w{VALLEY_WINDOW})")
    bot_ax.axhline(0, color="gray", linewidth=0.8, linestyle="-", alpha=0.5)

    all_ys = sd_ys + ssd_ys + val_ys + sval10_ys
    if all_ys:
        ymin, ymax = min(all_ys), max(all_ys)
        pad = (ymax - ymin) * 0.08 if ymax != ymin else 0.0001
        bot_ax.set_ylim(ymin - pad, ymax + pad)

    bot_ax.set_ylabel("s_dev20 / ss_dev20 / valley / s_valley×10", color="#333333", fontsize=7)
    bot_ax.tick_params(axis="y", labelsize=6)
    bot_ax.legend([l1, l2, l3, l4], [l.get_label() for l in [l1, l2, l3, l4]],
                  loc="upper left", fontsize=6)
    bot_ax.grid(True, axis="y", alpha=0.15)
    bot_ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    bot_ax.set_xlabel("Time (ET)")
    bot_ax.set_xlim(price_ax.get_xlim())


def render_date_chart(
    output_path: Path,
    trade_date: date,
    symbol_payloads: list[tuple[str, DayPayload]],
    output_mirror: Path | None = None,
) -> None:
    """Render all symbols for one trading day into a single PNG (price+vol per symbol)."""
    valid = [(sym, p) for sym, p in symbol_payloads if p.bars]
    if not valid:
        print(f"[skip] {trade_date}: no bars for any symbol")
        return

    # 统一转换 UTC → ET
    et_bars_by_sym = {sym: _to_et(payload.bars) for sym, payload in valid}
    trend_signals_by_sym = {sym: payload.trend_signal for sym, payload in valid}

    # 联合仿真（classify + select 共享 active_order 状态）
    close_prev_day_by_sym = {sym: payload.close_prev_day for sym, payload in valid}
    events_by_sym, indicators_by_sym = _simulate_joint(
        et_bars_by_sym, trend_signals_by_sym, EMA_CFG, close_prev_day_by_sym
    )

    n = len(valid)
    fig, axes = plt.subplots(
        n * 2, 1,
        figsize=(18, max(8, n * 6)),
        sharex=False,
        gridspec_kw={"height_ratios": [4, 1] * n},
    )
    axes = list(axes.flat) if hasattr(axes, "flat") else [axes]

    fig.suptitle(f"{trade_date} | {' / '.join(sym for sym, _ in valid)}", fontsize=12)

    for i, (sym, payload) in enumerate(valid):
        _render_day_panel(
            axes[i * 2],
            axes[i * 2 + 1],
            et_bars=et_bars_by_sym[sym],
            events=events_by_sym[sym],
            indicators=indicators_by_sym[sym],
            trade_date=payload.trade_date,
            trend_signal=payload.trend_signal,
            option_count=payload.option_count,
        )
        current_title = axes[i * 2].get_title()
        axes[i * 2].set_title(f"[{sym}]  {current_title}", fontsize=8, loc="left")

    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    print(f"  saved -> {output_path}")

    if output_mirror is not None:
        mirror_path = output_mirror / output_path.name
        try:
            os.makedirs(os.fsdecode(os.fsencode(str(output_mirror))), exist_ok=True)
            shutil.copy2(str(output_path), os.fsdecode(os.fsencode(str(mirror_path))))
            # stdout is cp932 on this machine; print only the ASCII-safe folder/filename
            print(f"  mirrored -> OneDrive/.../{output_mirror.name}/{output_path.name}")
        except Exception as e:
            print(f"  [mirror skip] {e}")

    plt.close(fig)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> None:
    # 每次执行创建以出图时间命名的子文件夹（精确到分钟）
    run_ts = datetime.now().strftime("%Y%m%d_%H%M")
    output_root = OUTPUT_ROOT_BASE / run_ts
    output_mirror = OUTPUT_MIRROR_BASE / run_ts
    output_root.mkdir(parents=True, exist_ok=True)
    try:
        output_mirror.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass  # OneDrive 路径不可用时静默跳过，render_date_chart 内部会处理

    settings = load_settings("config/settings.toml")
    symbols = settings.symbol_groups.resolve("core").symbols
    repository = SqliteMarketDataRepository(settings.data.market_data_db)
    data_service = build_bar_data_service(settings)
    option_gateways = build_option_gateways(settings)
    session_gateways = build_option_gateways(settings)

    print(f"Symbols : {symbols}")
    print(f"Dates   : {START_DATE} -> {END_DATE}")
    print(f"Signal  : dev20_cls (EMA{EMA_CFG.ema_fast_span}/EMA{EMA_CFG.ema_slow_span})")
    print(f"Trend   : TrendClassifier (price + options from DB)")
    print(f"Cancel  : only via SymbolSelector.select (new order replaces old)")
    print(f"Output  : {output_root}")
    print()

    # 多拉 7 天历史数据，用于计算前一交易日收盘价（close_prev_day）
    bars_by_symbol = data_service.get_bars(symbols, "1m", START_DATE - timedelta(days=7), END_DATE)
    days = _trading_days(START_DATE, END_DATE)

    payloads_by_symbol: dict[str, list[DayPayload]] = {s: [] for s in symbols}

    for trade_date in days:
        open_utc, close_utc = _utc_session_bounds(trade_date)
        eval_time_utc = open_utc + timedelta(minutes=TREND_EVAL_BARS)

        options_by_symbol = load_option_quotes_batch(
            symbols=symbols,
            trade_date=trade_date.isoformat(),
            start_utc=open_utc,
            end_utc=eval_time_utc,
            repository=repository,
            gateways=option_gateways,
            eval_time=open_utc + timedelta(minutes=TREND_EVAL_BARS - 1),
        )

        for symbol in symbols:
            all_bars = bars_by_symbol.get(symbol, [])
            day_bars = [b for b in all_bars if open_utc <= b.timestamp < close_utc]
            et_bars = _to_et(day_bars)
            prev_close_price = _prev_close(all_bars, open_utc)

            trend_signal, option_count = _classify_day_trend(
                symbol, trade_date, et_bars,
                options_by_symbol.get(symbol, []),
                repository, session_gateways,
            )

            prev_close_str = f"{prev_close_price:.2f}" if prev_close_price else "N/A"
            print(
                f"  [{symbol}] {trade_date}: {len(day_bars)} bars | "
                f"opts={option_count} | "
                f"prev_close={prev_close_str} | "
                f"regime={trend_signal.regime.value if trend_signal else 'N/A'}"
                f"{f' ({trend_signal.score:.2f})' if trend_signal else ''}"
            )

            payloads_by_symbol[symbol].append(DayPayload(
                trade_date=trade_date,
                bars=day_bars,
                trend_signal=trend_signal,
                option_count=option_count,
                close_prev_day=prev_close_price,
            ))
        print()

    for trade_date in days:
        symbol_payloads = [
            (sym, next(p for p in payloads_by_symbol[sym] if p.trade_date == trade_date))
            for sym in symbols
        ]
        output_path = output_root / f"{trade_date}.png"
        render_date_chart(output_path, trade_date, symbol_payloads, output_mirror)

    print(f"\nDone. Charts saved to {output_root}/")


if __name__ == "__main__":
    main()
