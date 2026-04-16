"""Unit tests for LiveTrendInputLoader and BacktestTrendInputLoader.

Uses lightweight mock objects — no real DB, no real network.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from intraday_auto_trading.models import (
    MinuteBar,
    OptionQuote,
    SessionMetrics,
)
from intraday_auto_trading.services.trend_input_loader import (
    BacktestTrendInputLoader,
    LiveTrendInputLoader,
)

SESSION_OPEN = datetime(2026, 4, 16, 9, 30, tzinfo=timezone.utc)
EVAL_TIME = datetime(2026, 4, 16, 9, 35, tzinfo=timezone.utc)
SYMBOL = "SPY"

BARS = [
    MinuteBar(
        timestamp=datetime(2026, 4, 16, 9, 30, tzinfo=timezone.utc),
        open=100.0, high=100.5, low=99.8, close=100.2, volume=5000.0,
    ),
    MinuteBar(
        timestamp=datetime(2026, 4, 16, 9, 31, tzinfo=timezone.utc),
        open=100.2, high=100.6, low=100.0, close=100.4, volume=3000.0,
    ),
]

METRICS = SessionMetrics(
    symbol=SYMBOL,
    timestamp=EVAL_TIME,
    source="ibkr",
    official_open=100.0,
    last_price=100.4,
    session_vwap=100.2,
)

OPT_QUOTE = OptionQuote(
    symbol=SYMBOL,
    strike=100.0,
    side="CALL",
    bid=1.0,
    ask=1.2,
    snapshot_time=EVAL_TIME,
)


# ---------------------------------------------------------------------------
# LiveTrendInputLoader
# ---------------------------------------------------------------------------


def _live_gateway(metrics=METRICS, bars=BARS, quotes=None):
    gw = MagicMock()
    gw.get_session_metrics.return_value = metrics
    gw.get_minute_bars.return_value = bars
    gw.get_option_quotes.return_value = quotes or []
    return gw


def test_live_loader_assembles_trend_input() -> None:
    loader = LiveTrendInputLoader(gateway=_live_gateway(), session_open=SESSION_OPEN)
    result = loader.load(SYMBOL, EVAL_TIME)

    assert result.symbol == SYMBOL
    assert result.official_open == 100.0
    assert result.last_price == 100.4
    assert result.session_vwap == 100.2
    assert len(result.minute_bars) == 2
    assert result.option_quotes == []


def test_live_loader_includes_option_quotes() -> None:
    loader = LiveTrendInputLoader(
        gateway=_live_gateway(quotes=[OPT_QUOTE]),
        session_open=SESSION_OPEN,
    )
    result = loader.load(SYMBOL, EVAL_TIME)
    assert len(result.option_quotes) == 1
    assert result.option_quotes[0].side == "CALL"


def test_live_loader_raises_if_no_metrics() -> None:
    gw = _live_gateway(metrics=None)
    loader = LiveTrendInputLoader(gateway=gw, session_open=SESSION_OPEN)
    with pytest.raises(ValueError, match="no session metrics"):
        loader.load(SYMBOL, EVAL_TIME)


def test_live_loader_raises_if_no_bars() -> None:
    gw = _live_gateway(bars=[])
    loader = LiveTrendInputLoader(gateway=gw, session_open=SESSION_OPEN)
    with pytest.raises(ValueError, match="no minute bars"):
        loader.load(SYMBOL, EVAL_TIME)


def test_live_loader_raises_if_incomplete_metrics() -> None:
    incomplete = SessionMetrics(
        symbol=SYMBOL,
        timestamp=EVAL_TIME,
        source="ibkr",
        official_open=None,   # 缺失
        last_price=100.0,
        session_vwap=100.0,
    )
    gw = _live_gateway(metrics=incomplete)
    loader = LiveTrendInputLoader(gateway=gw, session_open=SESSION_OPEN)
    with pytest.raises(ValueError, match="Incomplete session metrics"):
        loader.load(SYMBOL, EVAL_TIME)


# ---------------------------------------------------------------------------
# BacktestTrendInputLoader
# ---------------------------------------------------------------------------


def _backtest_repo(
    bars=BARS,
    winning_source="ibkr",
    metrics=METRICS,
    option_quotes=None,
):
    repo = MagicMock()
    repo.load_price_bars_with_source_priority.return_value = (bars, winning_source)
    repo.load_session_metrics.return_value = metrics
    repo.load_option_quotes.return_value = option_quotes or []
    return repo


def test_backtest_loader_assembles_trend_input() -> None:
    loader = BacktestTrendInputLoader(
        repository=_backtest_repo(), session_open=SESSION_OPEN
    )
    result = loader.load(SYMBOL, EVAL_TIME)

    assert result.symbol == SYMBOL
    assert result.official_open == 100.0
    assert result.last_price == 100.4
    assert result.session_vwap == 100.2
    assert len(result.minute_bars) == 2


def test_backtest_loader_falls_back_to_bars_when_no_metrics() -> None:
    """metrics 为 None → 从 bars 自行推算 official_open / last_price / session_vwap。"""
    loader = BacktestTrendInputLoader(
        repository=_backtest_repo(metrics=None), session_open=SESSION_OPEN
    )
    result = loader.load(SYMBOL, EVAL_TIME)
    assert result.official_open == BARS[0].open         # bars[0].open
    assert result.last_price == BARS[-1].close          # bars[-1].close


def test_backtest_loader_raises_if_no_bars() -> None:
    loader = BacktestTrendInputLoader(
        repository=_backtest_repo(bars=[], winning_source=""),
        session_open=SESSION_OPEN,
    )
    with pytest.raises(ValueError, match="No 1m bars in DB"):
        loader.load(SYMBOL, EVAL_TIME)


def test_backtest_loader_passes_source_priority() -> None:
    repo = _backtest_repo()
    priority = ["moomoo", "ibkr"]
    loader = BacktestTrendInputLoader(
        repository=repo,
        session_open=SESSION_OPEN,
        bar_source_priority=priority,
    )
    loader.load(SYMBOL, EVAL_TIME)
    repo.load_price_bars_with_source_priority.assert_called_once_with(
        symbol=SYMBOL,
        bar_size="1m",
        start=SESSION_OPEN,
        end=EVAL_TIME,
        source_priority=priority,
    )


def test_backtest_loader_includes_option_quotes() -> None:
    loader = BacktestTrendInputLoader(
        repository=_backtest_repo(option_quotes=[OPT_QUOTE]),
        session_open=SESSION_OPEN,
    )
    result = loader.load(SYMBOL, EVAL_TIME)
    assert len(result.option_quotes) == 1
