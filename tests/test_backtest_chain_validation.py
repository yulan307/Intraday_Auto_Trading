from __future__ import annotations

from datetime import datetime

from intraday_auto_trading.models import MinuteBar, OptionQuote, Regime, TrendInput, TrendSignal
from intraday_auto_trading.services.backtest_chain_validation import (
    SelectionDiagnosticsRow,
    TrackingEvent,
    TrackingLowPoint,
    build_trend_signal_annotation,
    build_intraday_vwap_series,
    render_symbol_validation_chart,
    render_trend_signal_fifteen_minute_chart,
    write_option_quotes_csv,
    write_selection_diagnostics_csv,
)


def test_build_intraday_vwap_series_uses_cumulative_volume_weighting() -> None:
    bars = [
        MinuteBar(timestamp=datetime(2026, 4, 16, 9, 30), open=10, high=11, low=9, close=10, volume=100),
        MinuteBar(timestamp=datetime(2026, 4, 16, 9, 31), open=10, high=12, low=10, close=12, volume=50),
    ]

    vwap_series = build_intraday_vwap_series(bars)

    assert vwap_series == [10.0, (10 * 100 + 12 * 50) / 150]


def test_render_symbol_validation_chart_and_csv_create_files(tmp_path) -> None:
    bars = [
        MinuteBar(timestamp=datetime(2026, 4, 16, 9, 30), open=10, high=11, low=9, close=10.5, volume=100),
        MinuteBar(timestamp=datetime(2026, 4, 16, 9, 31), open=10.5, high=11.5, low=10, close=11, volume=150),
    ]
    payload = TrendInput(
        symbol="SPY",
        eval_time=datetime(2026, 4, 16, 10, 0),
        official_open=10,
        last_price=11,
        session_vwap=10.75,
        minute_bars=bars,
        option_quotes=[],
    )
    chart_path = tmp_path / "spy.png"
    csv_path = tmp_path / "spy_options.csv"
    quotes = [
        OptionQuote(
            symbol="SPY",
            strike=500,
            side="CALL",
            bid=1.2,
            ask=1.3,
            last=1.25,
            volume=42,
            contract_id="SPY:2026-04-16:500:C",
            expiry="2026-04-16",
            snapshot_time=datetime(2026, 4, 16, 10, 0),
        )
    ]

    render_symbol_validation_chart(chart_path, payload)
    write_option_quotes_csv(csv_path, quotes)

    assert chart_path.exists()
    assert chart_path.stat().st_size > 0
    assert csv_path.exists()
    assert "SPY:2026-04-16:500:C" in csv_path.read_text(encoding="utf-8")


def test_render_trend_signal_fifteen_minute_chart_creates_file(tmp_path) -> None:
    bars = [
        MinuteBar(timestamp=datetime(2026, 4, 16, 9, 30), open=10, high=11, low=9, close=10.5, volume=100),
        MinuteBar(timestamp=datetime(2026, 4, 16, 9, 45), open=10.5, high=11.5, low=10, close=11, volume=150),
    ]
    signal = TrendSignal(
        symbol="SPY",
        eval_time=datetime(2026, 4, 16, 10, 0),
        regime=Regime.RANGE_TRACK_15M,
        score=0.62,
        reason="price action neutral with balanced options",
    )
    chart_path = tmp_path / "spy_trend_15m.png"

    render_trend_signal_fifteen_minute_chart(
        chart_path,
        symbol="SPY",
        bars=bars,
        trend_signal=signal,
        eval_time=datetime(2026, 4, 16, 10, 0),
        tracking_strategy="TRACKING_BUY",
        tracking_events=[
            TrackingEvent(
                timestamp=datetime(2026, 4, 16, 10, 30),
                action="PLACE",
                limit_price=10.6,
                bar_close=11.0,
                reason="confirmation complete",
            )
        ],
        tracking_low_points=[],
    )

    assert chart_path.exists()
    assert chart_path.stat().st_size > 0


def test_build_trend_signal_annotation_includes_key_fields() -> None:
    signal = TrendSignal(
        symbol="SPY",
        eval_time=datetime(2026, 4, 16, 10, 0),
        regime=Regime.EARLY_BUY,
        score=0.91,
        reason="strong open and favorable option skew",
    )

    annotation = build_trend_signal_annotation(
        signal,
        datetime(2026, 4, 16, 10, 0),
        tracking_strategy="TRACKING_BUY",
        tracking_events=[
            TrackingEvent(
                timestamp=datetime(2026, 4, 16, 10, 30),
                action="PLACE",
                limit_price=10.6,
                bar_close=11.0,
                reason="confirmation complete",
            )
        ],
        tracking_low_points=[],
    )

    assert "Regime: EARLY_BUY" in annotation
    assert "Score: 0.9100" in annotation
    assert "Reason: strong open and favorable option skew" in annotation
    assert "Tracking strategy: TRACKING_BUY" in annotation
    assert "Last tracking event: PLACE @ 10:30 price=10.60" in annotation


def test_build_trend_signal_annotation_reports_daily_lows() -> None:
    signal = TrendSignal(
        symbol="SPY",
        eval_time=datetime(2026, 4, 16, 10, 0),
        regime=Regime.RANGE_TRACK_15M,
        score=0.62,
        reason="price action neutral with balanced options",
    )

    annotation = build_trend_signal_annotation(
        signal,
        datetime(2026, 4, 16, 10, 0),
        tracking_strategy="TRACKING_BUY",
        tracking_events=[],
        tracking_low_points=[
            TrackingLowPoint(
                timestamp=datetime(2026, 4, 7, 11, 0),
                close_price=98.5,
            ),
            TrackingLowPoint(
                timestamp=datetime(2026, 4, 8, 10, 45),
                close_price=99.1,
            ),
        ],
    )

    assert "Daily lows marked: 2" in annotation
    assert "Last daily low: 99.10 at 2026-04-08 10:45" in annotation


def test_write_selection_diagnostics_csv_creates_file_with_selected_row(tmp_path) -> None:
    csv_path = tmp_path / "selection.csv"
    rows = [
        SelectionDiagnosticsRow(
            symbol="JEPI",
            regime="RANGE_TRACK_15M",
            signal_score=0.6283,
            signal_reason="balanced price action",
            trend_weight=2.0,
            completed_orders_this_week=0,
            has_position=False,
            ownership_bonus=2.0,
            frequency_penalty=0.0,
            ranking_score=4.6283,
            strategy="TRACKING_BUY",
            selected=True,
            selection_reason="balanced price action trend_weight=2.00, ownership_bonus=2.00, frequency_penalty=0.00",
        )
    ]

    write_selection_diagnostics_csv(csv_path, rows)

    content = csv_path.read_text(encoding="utf-8")
    assert csv_path.exists()
    assert "JEPI" in content
    assert "TRACKING_BUY" in content
    assert "True" in content
