from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Mapping, Sequence, cast

import matplotlib.dates as mdates
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from intraday_auto_trading.interfaces.brokers import BatchMarketDataGateway, MarketDataGateway
from intraday_auto_trading.interfaces.repositories import MarketDataRepository
from intraday_auto_trading.models import (
    AccountSymbolState,
    BuyStrategy,
    CapabilityStatus,
    MinuteBar,
    OptionQuote,
    Regime,
    SelectionResult,
    TrendInput,
    TrendSignal,
)
from intraday_auto_trading.services.backtest_data_service import BacktestDataService
from intraday_auto_trading.services.executor import ExecutionPlanner
from intraday_auto_trading.services.intraday_low_signal import IntradayLowConfig, compute_intraday_low_signal
from intraday_auto_trading.services.selector import SymbolSelector
from intraday_auto_trading.services.trend_classifier import TrendClassifier
from intraday_auto_trading.services.trend_input_loader import TrendInputLoader


TEST_TRADE_DATE = date(2026, 4, 16)
SESSION_OPEN_TIME = time(9, 30)
EVAL_TIME = time(10, 0)
SESSION_CLOSE_TIME = time(16, 0)
TRACKING_START_DATE = date(2026, 4, 6)
TRACKING_END_DATE = date(2026, 4, 10)


@dataclass(slots=True)
class SymbolValidationResult:
    symbol: str
    bar_source: str
    bar_count: int
    option_source: str
    option_count: int
    chart_path: Path | None
    trend_signal: TrendSignal | None
    fifteen_minute_source: str
    fifteen_minute_bar_count: int
    fifteen_minute_chart_path: Path | None
    tracking_strategy: str
    tracking_event_count: int
    tracking_lowest_close: float | None
    tracking_lowest_timestamp: datetime | None
    option_csv_path: Path
    message: str = ""


@dataclass(slots=True)
class SelectionDiagnosticsRow:
    symbol: str
    regime: str
    signal_score: float | None
    signal_reason: str
    trend_weight: float | None
    completed_orders_this_week: int
    has_position: bool
    ownership_bonus: float | None
    frequency_penalty: float | None
    ranking_score: float | None
    strategy: str
    selected: bool
    selection_reason: str


@dataclass(slots=True)
class TrackingEvent:
    timestamp: datetime
    action: str
    limit_price: float | None
    bar_close: float
    reason: str


@dataclass(slots=True)
class TrackingLowPoint:
    timestamp: datetime
    close_price: float


@dataclass(slots=True)
class BacktestChainValidationSummary:
    group_name: str
    output_dir: Path
    trade_date: date
    session_open: datetime
    eval_time: datetime
    selected_symbol: str | None
    selection_csv_path: Path
    results: list[SymbolValidationResult]


@dataclass(slots=True)
class BacktestChainValidationService:
    repository: MarketDataRepository
    backtest_data_service: BacktestDataService
    option_gateways: Mapping[str, MarketDataGateway]
    selector: SymbolSelector
    ema_config: IntradayLowConfig
    execution_planner: ExecutionPlanner
    output_root: Path = Path("artifacts/backtest_chain_validation")

    def run(
        self,
        group_name: str,
        symbols: Sequence[str],
    ) -> BacktestChainValidationSummary:
        session_open = datetime.combine(TEST_TRADE_DATE, SESSION_OPEN_TIME)
        eval_time = datetime.combine(TEST_TRADE_DATE, EVAL_TIME)
        tracking_start = datetime.combine(TRACKING_START_DATE, SESSION_OPEN_TIME)
        tracking_end = datetime.combine(TRACKING_END_DATE, SESSION_CLOSE_TIME)
        normalized_symbols = [symbol.upper() for symbol in symbols]
        trend_classifier = TrendClassifier()

        # This validation path uses the local DB first, then broker gateways only.
        self.backtest_data_service.source_priority = ["ibkr", "moomoo"]
        bar_results = self.backtest_data_service.get_bars(
            symbols=normalized_symbols,
            bar_size="1m",
            start=session_open,
            end=eval_time,
        )
        bar_results_by_symbol = {result.symbol: result for result in bar_results}
        option_state_by_symbol = self._ensure_option_quotes(normalized_symbols, session_open, eval_time)

        output_dir = self.output_root / TEST_TRADE_DATE.isoformat() / group_name
        output_dir.mkdir(parents=True, exist_ok=True)

        gateways: dict[str, MarketDataGateway] = {}
        if self.backtest_data_service.ibkr_gateway is not None:
            gateways["ibkr"] = self.backtest_data_service.ibkr_gateway
        if self.backtest_data_service.moomoo_gateway is not None:
            gateways["moomoo"] = self.backtest_data_service.moomoo_gateway
        if self.backtest_data_service.yfinance_gateway is not None:
            gateways["yfinance"] = self.backtest_data_service.yfinance_gateway
        loader = TrendInputLoader(
            repository=self.repository,
            gateways=gateways,
            session_open=session_open,
        )

        results: list[SymbolValidationResult] = []
        for symbol in normalized_symbols:
            bar_result = bar_results_by_symbol[symbol]
            option_source, option_count = option_state_by_symbol.get(symbol, ("none", 0))
            option_quotes = self.repository.load_option_quotes(symbol, session_open, eval_time)
            csv_path = output_dir / f"{symbol}_options.csv"
            write_option_quotes_csv(csv_path, option_quotes)

            chart_path: Path | None = None
            fifteen_minute_chart_path: Path | None = None
            trend_signal: TrendSignal | None = None
            message = bar_result.message
            try:
                trend_input = loader.load(symbol, eval_time)
                trend_signal = trend_classifier.classify(trend_input)
                chart_path = output_dir / f"{symbol}_bars.png"
                render_symbol_validation_chart(chart_path, trend_input)
            except Exception as exc:
                message = f"{message}; chart skipped: {exc}" if message else f"chart skipped: {exc}"

            (
                fifteen_minute_bars,
                fifteen_minute_source,
                fifteen_minute_bar_count,
            ) = self._load_opening_window_one_minute_bars(
                symbol=symbol,
                tracking_start=tracking_start,
                tracking_end=tracking_end,
            )
            tracking_strategy = determine_tracking_strategy(trend_signal)
            tracking_events: list[TrackingEvent] = []
            tracking_low_points: list[TrackingLowPoint] = []
            if fifteen_minute_bars:
                if tracking_strategy == BuyStrategy.TRACKING_BUY.value:
                    tracking_events, tracking_low_points = self._simulate_tracking_events(
                        symbol,
                        fifteen_minute_bars,
                        tracking_start,
                    )
                fifteen_minute_chart_path = output_dir / f"{symbol}_trend_15m.png"
                render_trend_signal_fifteen_minute_chart(
                    fifteen_minute_chart_path,
                    symbol=symbol,
                    bars=fifteen_minute_bars,
                    trend_signal=trend_signal,
                    eval_time=eval_time,
                    tracking_strategy=tracking_strategy,
                    tracking_events=tracking_events,
                    tracking_low_points=tracking_low_points,
                )
            else:
                extra_message = "15m chart skipped: no 15m bars available in the tracking window."
                message = f"{message}; {extra_message}" if message else extra_message

            last_tracking_low = tracking_low_points[-1] if tracking_low_points else None

            results.append(
                SymbolValidationResult(
                    symbol=symbol,
                    bar_source=bar_result.source,
                    bar_count=bar_result.bar_count,
                    option_source=option_source,
                    option_count=option_count,
                    chart_path=chart_path,
                    trend_signal=trend_signal,
                    fifteen_minute_source=fifteen_minute_source,
                    fifteen_minute_bar_count=fifteen_minute_bar_count,
                    fifteen_minute_chart_path=fifteen_minute_chart_path,
                    tracking_strategy=tracking_strategy,
                    tracking_event_count=len(tracking_events),
                    tracking_lowest_close=last_tracking_low.close_price if last_tracking_low is not None else None,
                    tracking_lowest_timestamp=last_tracking_low.timestamp if last_tracking_low is not None else None,
                    option_csv_path=csv_path,
                    message=message,
                )
            )

        selection_csv_path = output_dir / "selection_diagnostics.csv"
        selection_rows, selected_symbol = self._build_selection_diagnostics(results)
        write_selection_diagnostics_csv(selection_csv_path, selection_rows)

        return BacktestChainValidationSummary(
            group_name=group_name,
            output_dir=output_dir,
            trade_date=TEST_TRADE_DATE,
            session_open=session_open,
            eval_time=eval_time,
            selected_symbol=selected_symbol,
            selection_csv_path=selection_csv_path,
            results=results,
        )

    def _ensure_option_quotes(
        self,
        symbols: Sequence[str],
        session_open: datetime,
        eval_time: datetime,
    ) -> dict[str, tuple[str, int]]:
        results: dict[str, tuple[str, int]] = {}
        missing_symbols: list[str] = []

        for symbol in symbols:
            local_quotes = self.repository.load_option_quotes(symbol, session_open, eval_time)
            if local_quotes:
                results[symbol] = ("db", len(local_quotes))
            else:
                missing_symbols.append(symbol)

        if not missing_symbols:
            return results

        for gateway_name, gateway in self.option_gateways.items():
            capability = gateway.probe_capabilities().options
            if capability.status is not CapabilityStatus.AVAILABLE:
                continue
            quotes_by_symbol = self._fetch_option_quotes(gateway, missing_symbols, eval_time)
            remaining_symbols: list[str] = []
            for symbol in missing_symbols:
                quotes = quotes_by_symbol.get(symbol, [])
                if quotes:
                    self.repository.save_option_quotes(quotes, source=gateway.provider_name)
                    results[symbol] = (gateway_name, len(quotes))
                else:
                    remaining_symbols.append(symbol)
            missing_symbols = remaining_symbols
            if not missing_symbols:
                break

        for symbol in missing_symbols:
            results[symbol] = ("none", 0)

        return results

    @staticmethod
    def _fetch_option_quotes(
        gateway: MarketDataGateway,
        symbols: Sequence[str],
        at_time: datetime,
    ) -> dict[str, list[OptionQuote]]:
        if hasattr(gateway, "get_option_quotes_batch"):
            return cast(BatchMarketDataGateway, gateway).get_option_quotes_batch(symbols, at_time)
        return {symbol: gateway.get_option_quotes(symbol, at_time) for symbol in symbols}

    def _build_selection_diagnostics(
        self,
        results: Sequence[SymbolValidationResult],
    ) -> tuple[list[SelectionDiagnosticsRow], str | None]:
        account_states = {
            result.symbol: AccountSymbolState(
                symbol=result.symbol,
                completed_orders_this_week=0,
                has_position=False,
            )
            for result in results
        }
        scored_results: dict[str, SelectionResult] = {}
        available_signals = [result.trend_signal for result in results if result.trend_signal is not None]
        selected_symbol: str | None = None

        if available_signals:
            selected = self.selector.select(available_signals, account_states)
            selected_symbol = selected.symbol
            for signal in available_signals:
                scored_results[signal.symbol] = self.selector._score_signal(signal, account_states.get(signal.symbol))

        rows: list[SelectionDiagnosticsRow] = []
        for result in results:
            state = account_states[result.symbol]
            trend_signal = result.trend_signal
            scored = scored_results.get(result.symbol)
            trend_weight = regime_weight(self.selector, trend_signal.regime) if trend_signal is not None else None
            ownership_bonus = 0.0 if state.has_position else self.selector.settings.unbought_bonus
            frequency_penalty = state.completed_orders_this_week * self.selector.settings.recent_fill_penalty_step
            strategy = scored.strategy.value if scored is not None else "UNAVAILABLE"
            selected = result.symbol == selected_symbol
            selection_reason = ascii_only_text(scored.rationale) if scored is not None else "TrendSignal unavailable"

            rows.append(
                SelectionDiagnosticsRow(
                    symbol=result.symbol,
                    regime=trend_signal.regime.value if trend_signal is not None else "UNAVAILABLE",
                    signal_score=trend_signal.score if trend_signal is not None else None,
                    signal_reason=ascii_only_text(trend_signal.reason) if trend_signal is not None else "TrendSignal unavailable",
                    trend_weight=trend_weight,
                    completed_orders_this_week=state.completed_orders_this_week,
                    has_position=state.has_position,
                    ownership_bonus=ownership_bonus if trend_signal is not None else None,
                    frequency_penalty=frequency_penalty if trend_signal is not None else None,
                    ranking_score=scored.ranking_score if scored is not None else None,
                    strategy=strategy,
                    selected=selected,
                    selection_reason=selection_reason,
                )
            )

        rows.sort(key=lambda row: (row.ranking_score is not None, row.ranking_score or -999999.0), reverse=True)
        return rows, selected_symbol

    def _load_opening_window_one_minute_bars(
        self,
        *,
        symbol: str,
        tracking_start: datetime,
        tracking_end: datetime,
    ) -> tuple[list[MinuteBar], str, int]:
        combined_bars: list[MinuteBar] = []
        observed_sources: list[str] = []
        priority = ["ibkr_direct", "ibkr", "ibkr_derived", "moomoo", "yfinance"]
        expected_regular_session_bars = 390

        current_date = tracking_start.date()
        while current_date <= tracking_end.date():
            if current_date.weekday() < 5:
                day_start = datetime.combine(current_date, SESSION_OPEN_TIME)
                day_end = datetime.combine(current_date, SESSION_CLOSE_TIME)
                day_bars, day_source = self.repository.load_price_bars_with_source_priority(
                    symbol=symbol,
                    bar_size="1m",
                    start=day_start,
                    end=day_end,
                    source_priority=priority,
                )
                if len(day_bars) < expected_regular_session_bars:
                    refreshed_bars, refreshed_source = self._refresh_one_minute_bars(
                        symbol=symbol,
                        start=day_start,
                        end=day_end,
                    )
                    if refreshed_bars and len(refreshed_bars) >= len(day_bars):
                        day_bars = refreshed_bars
                        day_source = refreshed_source
                combined_bars.extend(day_bars)
                if day_bars:
                    observed_sources.append(day_source)
            current_date += timedelta(days=1)

        if not combined_bars:
            return [], "none", 0

        unique_sources = {source for source in observed_sources if source}
        if len(unique_sources) == 1:
            source_label = next(iter(unique_sources))
        elif unique_sources:
            source_label = "mixed"
        else:
            source_label = "unknown"

        combined_bars.sort(key=lambda bar: bar.timestamp)
        return combined_bars, source_label, len(combined_bars)

    def _refresh_one_minute_bars(
        self,
        *,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> tuple[list[MinuteBar], str]:
        source_priority = list(self.backtest_data_service.source_priority)
        live_gateways: dict[str, MarketDataGateway] = {}
        if self.backtest_data_service.ibkr_gateway is not None:
            live_gateways["ibkr"] = self.backtest_data_service.ibkr_gateway
        if self.backtest_data_service.moomoo_gateway is not None:
            live_gateways["moomoo"] = self.backtest_data_service.moomoo_gateway

        for source_name in source_priority:
            if source_name == "yfinance":
                bars = self.backtest_data_service._fetch_from_yfinance(symbol, "1m", start, end)
            else:
                gateway = live_gateways.get(source_name)
                if gateway is None:
                    continue
                bars = self.backtest_data_service._fetch_from_gateway(gateway, symbol, "1m", start, end)
            if not bars:
                continue
            self.repository.save_price_bars(symbol, "1m", bars, source_name)
            return bars, source_name
        return [], "none"

    def _simulate_tracking_events(
        self,
        symbol: str,
        bars: Sequence[MinuteBar],
        tracking_start: datetime,
    ) -> tuple[list[TrackingEvent], list[TrackingLowPoint]]:
        events: list[TrackingEvent] = []

        tracking_bars = [bar for bar in bars if bar.timestamp >= tracking_start]
        if not tracking_bars:
            return events, []

        tracking_low_points: list[TrackingLowPoint] = []
        bars_by_day: dict[date, list[MinuteBar]] = {}
        for bar in tracking_bars:
            bars_by_day.setdefault(bar.timestamp.date(), []).append(bar)

        for trade_date in sorted(bars_by_day):
            day_bars = bars_by_day[trade_date]

            lowest_bar = min(day_bars, key=lambda bar: bar.low)
            tracking_low_points.append(
                TrackingLowPoint(
                    timestamp=lowest_bar.timestamp,
                    close_price=lowest_bar.low,
                )
            )

            session_close = datetime.combine(trade_date, SESSION_CLOSE_TIME)
            force_buy_time = session_close - timedelta(
                minutes=self.ema_config.force_buy_minutes_before_close
            )

            already_bought_today = False
            for idx in range(len(day_bars)):
                result = compute_intraday_low_signal(
                    bars=day_bars,
                    current_idx=idx,
                    force_buy_time=force_buy_time,
                    already_bought_today=already_bought_today,
                    config=self.ema_config,
                )

                if already_bought_today:
                    continue

                if result.signal == "buy_now":
                    reversal_types = []
                    if result.reversal_ok_a:
                        reversal_types.append("A")
                    if result.reversal_ok_b:
                        reversal_types.append("B")
                    if result.reversal_ok_c:
                        reversal_types.append("C")
                    reason = f"V2 buy_now reversal={'|'.join(reversal_types) or 'ok'}"
                    order = self.execution_planner.build_tracking_order(
                        symbol=symbol,
                        quantity=1,
                        limit_price=result.limit_price,
                    )
                    events.append(
                        TrackingEvent(
                            timestamp=day_bars[idx].timestamp,
                            action="PLACE",
                            limit_price=order.limit_price,
                            bar_close=day_bars[idx].close,
                            reason=ascii_only_text(reason),
                        )
                    )
                    already_bought_today = True

                elif result.signal == "force_buy":
                    bar = day_bars[idx]
                    events.append(
                        TrackingEvent(
                            timestamp=bar.timestamp,
                            action="PLACE",
                            limit_price=bar.close,
                            bar_close=bar.close,
                            reason="force_buy",
                        )
                    )
                    already_bought_today = True

        return events, tracking_low_points


def render_symbol_validation_chart(path: Path, payload: TrendInput) -> None:
    if not payload.minute_bars:
        raise ValueError(f"No minute bars available for {payload.symbol}")

    times = [bar.timestamp for bar in payload.minute_bars]
    volumes = [bar.volume for bar in payload.minute_bars]
    vwap_series = build_intraday_vwap_series(payload.minute_bars)
    colors = ["#0a7f39" if bar.close >= bar.open else "#c0392b" for bar in payload.minute_bars]

    fig, (price_ax, volume_ax) = plt.subplots(
        2,
        1,
        figsize=(12, 7),
        sharex=True,
        gridspec_kw={"height_ratios": [3, 1]},
    )
    plot_candlesticks(price_ax, payload.minute_bars)
    price_ax.plot(times, vwap_series, color="#1f4e79", linewidth=1.5, label="VWAP")
    price_ax.set_title(f"{payload.symbol} 1m Bars | {payload.eval_time:%Y-%m-%d %H:%M}")
    price_ax.set_ylabel("Price")
    price_ax.grid(True, alpha=0.25)
    price_ax.legend(loc="upper left")

    bar_width = 0.8 / (24 * 60)
    volume_ax.bar(times, volumes, width=bar_width, color=colors, alpha=0.8)
    volume_ax.set_ylabel("Vol")
    volume_ax.grid(True, axis="y", alpha=0.25)
    volume_ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    volume_ax.set_xlabel("Time")

    fig.autofmt_xdate()
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def plot_candlesticks(ax, bars: Sequence[MinuteBar]) -> None:
    width = 0.8 / (24 * 60)
    for bar in bars:
        x = mdates.date2num(bar.timestamp)
        color = "#0a7f39" if bar.close >= bar.open else "#c0392b"
        ax.vlines(x, bar.low, bar.high, color=color, linewidth=1.0)
        lower = min(bar.open, bar.close)
        height = max(abs(bar.close - bar.open), 0.0001)
        rect = Rectangle(
            (x - width / 2, lower),
            width,
            height,
            facecolor=color,
            edgecolor=color,
            linewidth=0.8,
        )
        ax.add_patch(rect)
    ax.xaxis_date()


def render_trend_signal_fifteen_minute_chart(
    path: Path,
    *,
    symbol: str,
    bars: Sequence[MinuteBar],
    trend_signal: TrendSignal | None,
    eval_time: datetime,
    tracking_strategy: str,
    tracking_events: Sequence[TrackingEvent],
    tracking_low_points: Sequence[TrackingLowPoint],
) -> None:
    if not bars:
        raise ValueError(f"No 15m bars available for {symbol}")

    fig, ax = plt.subplots(figsize=(12, 6))
    plot_candlesticks(ax, bars)
    ax.set_title(f"{symbol} 15m Bars | {bars[0].timestamp:%Y-%m-%d} -> {bars[-1].timestamp:%Y-%m-%d}")
    ax.set_xlabel("Time")
    ax.set_ylabel("Price")
    ax.grid(True, alpha=0.25)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))

    info_text = build_trend_signal_annotation(
        trend_signal,
        eval_time,
        tracking_strategy=tracking_strategy,
        tracking_events=tracking_events,
        tracking_low_points=tracking_low_points,
    )
    ax.text(
        0.98,
        0.98,
        info_text,
        transform=ax.transAxes,
        va="top",
        ha="right",
        fontsize=9,
        bbox={"boxstyle": "round,pad=0.4", "facecolor": "white", "alpha": 0.9, "edgecolor": "#555555"},
    )

    for tracking_low_point in tracking_low_points:
        ax.scatter(
            [tracking_low_point.timestamp],
            [tracking_low_point.close_price],
            marker="o",
            color="#1565c0",
            s=70,
            zorder=5,
        )
        ax.annotate(
            f"LOW {tracking_low_point.close_price:.2f}",
            (tracking_low_point.timestamp, tracking_low_point.close_price),
            textcoords="offset points",
            xytext=(0, -16),
            ha="center",
            fontsize=8,
            color="#0d47a1",
        )

    for event in tracking_events:
        x = event.timestamp
        y = event.limit_price if event.limit_price is not None else event.bar_close
        if event.action == "PLACE":
            ax.scatter([x], [y], marker="^", color="#2e7d32", s=90, zorder=5)
            ax.annotate(
                f"PLACE {y:.2f}",
                (x, y),
                textcoords="offset points",
                xytext=(0, -18),
                ha="center",
                fontsize=8,
                color="#1b5e20",
            )
        else:
            ax.scatter([x], [y], marker="x", color="#c62828", s=80, zorder=5)
            ax.annotate(
                f"CANCEL {event.reason}",
                (x, y),
                textcoords="offset points",
                xytext=(0, 10),
                ha="center",
                fontsize=8,
                color="#8e0000",
            )

    fig.autofmt_xdate()
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def build_trend_signal_annotation(
    trend_signal: TrendSignal | None,
    eval_time: datetime,
    *,
    tracking_strategy: str,
    tracking_events: Sequence[TrackingEvent],
    tracking_low_points: Sequence[TrackingLowPoint] = (),
) -> str:
    if trend_signal is None:
        return f"TrendSignal: unavailable\nEval time: {eval_time:%H:%M}"

    reason = ascii_only_text(trend_signal.reason.replace("\n", " ").strip())
    if not reason:
        reason = "reason unavailable"
    if len(reason) > 220:
        reason = reason[:217] + "..."

    tracking_line = f"Tracking strategy: {tracking_strategy}"
    if tracking_low_points:
        last_low_point = tracking_low_points[-1]
        tracking_line += (
            f"\nDaily lows marked: {len(tracking_low_points)}"
            f"\nLast daily low: {last_low_point.close_price:.2f} "
            f"at {last_low_point.timestamp:%Y-%m-%d %H:%M}"
        )
    if tracking_strategy == BuyStrategy.TRACKING_BUY.value:
        if tracking_events:
            last_event = tracking_events[-1]
            event_price = f"{last_event.limit_price:.2f}" if last_event.limit_price is not None else "n/a"
            tracking_line += (
                f"\nLast tracking event: {last_event.action} @ {last_event.timestamp:%H:%M} "
                f"price={event_price}"
            )
        else:
            tracking_line += "\nLast tracking event: no order or cancel signal triggered"
    else:
        tracking_line += "\nLast tracking event: tracking skipped for non-tracking strategy"

    return (
        f"TrendSignal\n"
        f"Eval time: {trend_signal.eval_time:%H:%M}\n"
        f"Regime: {trend_signal.regime.value}\n"
        f"Score: {trend_signal.score:.4f}\n"
        f"Reason: {reason}\n"
        f"{tracking_line}"
    )


def ascii_only_text(value: str) -> str:
    normalized = value.encode("ascii", "ignore").decode("ascii")
    normalized = " ".join(normalized.split())
    return normalized


def determine_tracking_strategy(trend_signal: TrendSignal | None) -> str:
    if trend_signal is None:
        return "UNAVAILABLE"
    if trend_signal.regime is Regime.EARLY_BUY:
        return BuyStrategy.IMMEDIATE_BUY.value
    return BuyStrategy.TRACKING_BUY.value


def regime_weight(selector: SymbolSelector, regime: Regime) -> float:
    return {
        Regime.WEAK_TAIL: selector.settings.weak_tail_weight,
        Regime.RANGE_TRACK_15M: selector.settings.range_track_weight,
        Regime.EARLY_BUY: selector.settings.early_buy_weight,
    }[regime]


def build_intraday_vwap_series(bars: Sequence[MinuteBar]) -> list[float]:
    result: list[float] = []
    cumulative_price_volume = 0.0
    cumulative_volume = 0.0
    last_close = 0.0

    for bar in bars:
        cumulative_price_volume += bar.close * bar.volume
        cumulative_volume += bar.volume
        last_close = bar.close
        if cumulative_volume <= 0:
            result.append(last_close)
        else:
            result.append(cumulative_price_volume / cumulative_volume)
    return result


def write_option_quotes_csv(path: Path, quotes: Sequence[OptionQuote]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "symbol",
                "contract_id",
                "expiry",
                "side",
                "strike",
                "bid",
                "ask",
                "bid_size",
                "ask_size",
                "last",
                "volume",
                "iv",
                "delta",
                "gamma",
                "exchange",
                "multiplier",
                "snapshot_time",
            ],
        )
        writer.writeheader()
        for quote in quotes:
            writer.writerow(
                {
                    "symbol": quote.symbol,
                    "contract_id": quote.contract_id or "",
                    "expiry": quote.expiry or "",
                    "side": quote.side,
                    "strike": quote.strike,
                    "bid": quote.bid,
                    "ask": quote.ask,
                    "bid_size": quote.bid_size,
                    "ask_size": quote.ask_size,
                    "last": quote.last,
                    "volume": quote.volume,
                    "iv": quote.iv,
                    "delta": quote.delta,
                    "gamma": quote.gamma,
                    "exchange": quote.exchange or "",
                    "multiplier": quote.multiplier or "",
                    "snapshot_time": quote.snapshot_time.isoformat() if quote.snapshot_time else "",
                }
            )


def write_selection_diagnostics_csv(path: Path, rows: Sequence[SelectionDiagnosticsRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "symbol",
                "regime",
                "signal_score",
                "signal_reason",
                "trend_weight",
                "completed_orders_this_week",
                "has_position",
                "ownership_bonus",
                "frequency_penalty",
                "ranking_score",
                "strategy",
                "selected",
                "selection_reason",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "symbol": row.symbol,
                    "regime": row.regime,
                    "signal_score": "" if row.signal_score is None else f"{row.signal_score:.4f}",
                    "signal_reason": row.signal_reason,
                    "trend_weight": "" if row.trend_weight is None else f"{row.trend_weight:.4f}",
                    "completed_orders_this_week": row.completed_orders_this_week,
                    "has_position": row.has_position,
                    "ownership_bonus": "" if row.ownership_bonus is None else f"{row.ownership_bonus:.4f}",
                    "frequency_penalty": "" if row.frequency_penalty is None else f"{row.frequency_penalty:.4f}",
                    "ranking_score": "" if row.ranking_score is None else f"{row.ranking_score:.4f}",
                    "strategy": row.strategy,
                    "selected": row.selected,
                    "selection_reason": row.selection_reason,
                }
            )
