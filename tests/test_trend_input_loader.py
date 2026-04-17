"""Unit tests for TrendInputLoader.

Scenarios covered:
- DB hit: bars/metrics/options already in DB → no gateway calls
- DB miss + historical eval_time → history_source_order gateways tried
- DB miss + live eval_time → live_source_order gateways tried
- Fallback within source order (first gateway fails, second succeeds)
- All sources fail → RuntimeError
- IBKR options skipped when ibkr_options_enabled=False
- Session metrics derived from bars when all gateways fail
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, call

import pytest

from intraday_auto_trading.models import MinuteBar, OptionQuote, SessionMetrics
from intraday_auto_trading.services.data_fetch_policy import DataFetchPolicy
from intraday_auto_trading.services.trend_input_loader import TrendInputLoader

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

# Historical date — will always be in the past
HIST_SESSION_OPEN = datetime(2026, 3, 15, 9, 30, tzinfo=timezone.utc)
HIST_EVAL_TIME = datetime(2026, 3, 15, 10, 0, tzinfo=timezone.utc)

# Future date — will always be "live"
LIVE_SESSION_OPEN = datetime(2099, 1, 1, 9, 30, tzinfo=timezone.utc)
LIVE_EVAL_TIME = datetime(2099, 1, 1, 10, 0, tzinfo=timezone.utc)

SYMBOL = "SPY"

BARS = [
    MinuteBar(
        timestamp=datetime(2026, 3, 15, 9, 30, tzinfo=timezone.utc),
        open=100.0, high=100.5, low=99.8, close=100.2, volume=5000.0,
    ),
    MinuteBar(
        timestamp=datetime(2026, 3, 15, 9, 31, tzinfo=timezone.utc),
        open=100.2, high=100.6, low=100.0, close=100.4, volume=3000.0,
    ),
]

METRICS = SessionMetrics(
    symbol=SYMBOL,
    timestamp=HIST_EVAL_TIME,
    source="moomoo",
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
    snapshot_time=HIST_EVAL_TIME,
)


def _repo(
    bars=BARS,
    winning_source="moomoo",
    metrics=METRICS,
    option_quotes=None,
):
    """Build a mock repository with configurable return values."""
    repo = MagicMock()
    repo.load_price_bars_with_source_priority.return_value = (bars, winning_source)
    repo.load_session_metrics.return_value = metrics
    repo.load_option_quotes.return_value = option_quotes if option_quotes is not None else []
    return repo


def _empty_repo():
    """Repository that returns nothing — forces fallback to gateways."""
    return _repo(bars=[], winning_source="", metrics=None, option_quotes=[])


def _gateway(bars=BARS, metrics=METRICS, options=None):
    gw = MagicMock()
    gw.get_minute_bars.return_value = bars
    gw.get_session_metrics.return_value = metrics
    gw.get_option_quotes.return_value = options if options is not None else []
    return gw


def _failing_gateway():
    gw = MagicMock()
    gw.get_minute_bars.return_value = []
    gw.get_session_metrics.return_value = None
    gw.get_option_quotes.return_value = []
    return gw


# ---------------------------------------------------------------------------
# DB hit — no gateway calls
# ---------------------------------------------------------------------------


def test_db_hit_returns_bars_without_gateway_call() -> None:
    repo = _repo(bars=BARS, metrics=METRICS)
    gw = _gateway()
    loader = TrendInputLoader(
        repository=repo,
        gateways={"ibkr": gw},
        session_open=HIST_SESSION_OPEN,
    )
    result = loader.load(SYMBOL, HIST_EVAL_TIME)

    assert len(result.minute_bars) == 2
    gw.get_minute_bars.assert_not_called()


def test_db_hit_returns_options_without_gateway_call() -> None:
    repo = _repo(bars=BARS, metrics=METRICS, option_quotes=[OPT_QUOTE])
    gw = _gateway()
    loader = TrendInputLoader(
        repository=repo,
        gateways={"moomoo": gw},
        session_open=HIST_SESSION_OPEN,
    )
    result = loader.load(SYMBOL, HIST_EVAL_TIME)

    assert len(result.option_quotes) == 1
    gw.get_option_quotes.assert_not_called()


def test_db_hit_assembles_correct_trend_input() -> None:
    repo = _repo()
    loader = TrendInputLoader(
        repository=repo,
        gateways={},
        session_open=HIST_SESSION_OPEN,
    )
    result = loader.load(SYMBOL, HIST_EVAL_TIME)

    assert result.symbol == SYMBOL
    assert result.official_open == 100.0
    assert result.last_price == 100.4
    assert result.session_vwap == 100.2


# ---------------------------------------------------------------------------
# DB miss + historical → history_source_order (yfinance → moomoo → ibkr)
# ---------------------------------------------------------------------------


def test_historical_db_miss_uses_history_source_order() -> None:
    repo = _empty_repo()
    yf_gw = _gateway(bars=BARS, metrics=METRICS)
    moomoo_gw = _gateway()
    policy = DataFetchPolicy(
        history_source_order=["yfinance", "moomoo"],
    )
    loader = TrendInputLoader(
        repository=repo,
        gateways={"yfinance": yf_gw, "moomoo": moomoo_gw},
        session_open=HIST_SESSION_OPEN,
        policy=policy,
    )
    loader.load(SYMBOL, HIST_EVAL_TIME)

    yf_gw.get_minute_bars.assert_called_once()
    moomoo_gw.get_minute_bars.assert_not_called()


def test_historical_fallback_to_second_source_when_first_fails() -> None:
    repo = _empty_repo()
    failing = _failing_gateway()
    succeeding = _gateway(bars=BARS, metrics=METRICS)
    policy = DataFetchPolicy(history_source_order=["yfinance", "moomoo"])
    loader = TrendInputLoader(
        repository=repo,
        gateways={"yfinance": failing, "moomoo": succeeding},
        session_open=HIST_SESSION_OPEN,
        policy=policy,
    )
    result = loader.load(SYMBOL, HIST_EVAL_TIME)

    assert len(result.minute_bars) == 2
    failing.get_minute_bars.assert_called_once()
    succeeding.get_minute_bars.assert_called_once()


def test_historical_all_sources_fail_raises_runtime_error() -> None:
    repo = _empty_repo()
    policy = DataFetchPolicy(history_source_order=["yfinance", "moomoo"])
    loader = TrendInputLoader(
        repository=repo,
        gateways={"yfinance": _failing_gateway(), "moomoo": _failing_gateway()},
        session_open=HIST_SESSION_OPEN,
        policy=policy,
    )
    with pytest.raises(RuntimeError, match="No 1m bars"):
        loader.load(SYMBOL, HIST_EVAL_TIME)


# ---------------------------------------------------------------------------
# DB miss + live → live_source_order (ibkr → moomoo)
# ---------------------------------------------------------------------------


def test_live_db_miss_uses_live_source_order() -> None:
    repo = _empty_repo()
    # Match metrics timestamp to LIVE_EVAL_TIME so it passes through
    live_metrics = SessionMetrics(
        symbol=SYMBOL,
        timestamp=LIVE_EVAL_TIME,
        source="ibkr",
        official_open=200.0,
        last_price=201.0,
        session_vwap=200.5,
    )
    live_bars = [
        MinuteBar(
            timestamp=LIVE_SESSION_OPEN,
            open=200.0, high=201.0, low=199.5, close=200.8, volume=4000.0,
        )
    ]
    ibkr_gw = _gateway(bars=live_bars, metrics=live_metrics)
    moomoo_gw = _gateway()
    policy = DataFetchPolicy(live_source_order=["ibkr", "moomoo"])
    loader = TrendInputLoader(
        repository=repo,
        gateways={"ibkr": ibkr_gw, "moomoo": moomoo_gw},
        session_open=LIVE_SESSION_OPEN,
        policy=policy,
    )
    loader.load(SYMBOL, LIVE_EVAL_TIME)

    ibkr_gw.get_minute_bars.assert_called_once()
    moomoo_gw.get_minute_bars.assert_not_called()


def test_live_ibkr_fail_falls_back_to_moomoo() -> None:
    repo = _empty_repo()
    live_metrics = SessionMetrics(
        symbol=SYMBOL, timestamp=LIVE_EVAL_TIME, source="moomoo",
        official_open=200.0, last_price=201.0, session_vwap=200.5,
    )
    live_bars = [
        MinuteBar(
            timestamp=LIVE_SESSION_OPEN,
            open=200.0, high=201.0, low=199.5, close=200.8, volume=4000.0,
        )
    ]
    ibkr_gw = _failing_gateway()
    moomoo_gw = _gateway(bars=live_bars, metrics=live_metrics)
    policy = DataFetchPolicy(live_source_order=["ibkr", "moomoo"])
    loader = TrendInputLoader(
        repository=repo,
        gateways={"ibkr": ibkr_gw, "moomoo": moomoo_gw},
        session_open=LIVE_SESSION_OPEN,
        policy=policy,
    )
    result = loader.load(SYMBOL, LIVE_EVAL_TIME)

    assert len(result.minute_bars) == 1
    ibkr_gw.get_minute_bars.assert_called_once()
    moomoo_gw.get_minute_bars.assert_called_once()


def test_live_all_sources_fail_raises_runtime_error() -> None:
    repo = _empty_repo()
    policy = DataFetchPolicy(live_source_order=["ibkr", "moomoo"])
    loader = TrendInputLoader(
        repository=repo,
        gateways={"ibkr": _failing_gateway(), "moomoo": _failing_gateway()},
        session_open=LIVE_SESSION_OPEN,
        policy=policy,
    )
    with pytest.raises(RuntimeError, match="No 1m bars"):
        loader.load(SYMBOL, LIVE_EVAL_TIME)


# ---------------------------------------------------------------------------
# IBKR options skipped when ibkr_options_enabled=False
# ---------------------------------------------------------------------------


def test_ibkr_options_skipped_when_disabled() -> None:
    repo = _empty_repo()
    # bars and metrics available so only option fetch is tested
    repo.load_price_bars_with_source_priority.return_value = (BARS, "moomoo")
    repo.load_session_metrics.return_value = METRICS

    ibkr_gw = _gateway(options=[OPT_QUOTE])
    moomoo_gw = _gateway(options=[OPT_QUOTE])
    policy = DataFetchPolicy(
        history_source_order=["ibkr", "moomoo"],
        ibkr_options_enabled=False,
    )
    loader = TrendInputLoader(
        repository=repo,
        gateways={"ibkr": ibkr_gw, "moomoo": moomoo_gw},
        session_open=HIST_SESSION_OPEN,
        policy=policy,
    )
    loader.load(SYMBOL, HIST_EVAL_TIME)

    ibkr_gw.get_option_quotes.assert_not_called()
    moomoo_gw.get_option_quotes.assert_called_once()


def test_ibkr_options_called_when_enabled() -> None:
    repo = _empty_repo()
    repo.load_price_bars_with_source_priority.return_value = (BARS, "moomoo")
    repo.load_session_metrics.return_value = METRICS

    ibkr_gw = _gateway(options=[OPT_QUOTE])
    policy = DataFetchPolicy(
        history_source_order=["ibkr"],
        ibkr_options_enabled=True,
    )
    loader = TrendInputLoader(
        repository=repo,
        gateways={"ibkr": ibkr_gw},
        session_open=HIST_SESSION_OPEN,
        policy=policy,
    )
    result = loader.load(SYMBOL, HIST_EVAL_TIME)

    ibkr_gw.get_option_quotes.assert_called_once()
    assert len(result.option_quotes) == 1


def test_options_empty_list_returned_when_all_sources_skip_or_fail() -> None:
    repo = _empty_repo()
    repo.load_price_bars_with_source_priority.return_value = (BARS, "moomoo")
    repo.load_session_metrics.return_value = METRICS

    policy = DataFetchPolicy(
        history_source_order=["ibkr"],
        ibkr_options_enabled=False,
    )
    loader = TrendInputLoader(
        repository=repo,
        gateways={},
        session_open=HIST_SESSION_OPEN,
        policy=policy,
    )
    result = loader.load(SYMBOL, HIST_EVAL_TIME)

    assert result.option_quotes == []


# ---------------------------------------------------------------------------
# Session metrics derived from bars when all gateways fail
# ---------------------------------------------------------------------------


def test_session_metrics_derived_from_bars_when_no_gateway_data() -> None:
    repo = _empty_repo()
    repo.load_price_bars_with_source_priority.return_value = (BARS, "moomoo")
    # session metrics: DB miss, gateway miss → derive from bars
    policy = DataFetchPolicy(history_source_order=[])  # no gateways to try
    loader = TrendInputLoader(
        repository=repo,
        gateways={},
        session_open=HIST_SESSION_OPEN,
        policy=policy,
    )
    result = loader.load(SYMBOL, HIST_EVAL_TIME)

    assert result.official_open == BARS[0].open
    assert result.last_price == BARS[-1].close


# ---------------------------------------------------------------------------
# DB write-back on gateway fetch
# ---------------------------------------------------------------------------


def test_fetched_bars_written_to_db() -> None:
    repo = _empty_repo()
    repo.load_session_metrics.return_value = METRICS
    gw = _gateway(bars=BARS, metrics=METRICS)
    policy = DataFetchPolicy(history_source_order=["moomoo"])
    loader = TrendInputLoader(
        repository=repo,
        gateways={"moomoo": gw},
        session_open=HIST_SESSION_OPEN,
        policy=policy,
    )
    loader.load(SYMBOL, HIST_EVAL_TIME)

    repo.save_price_bars.assert_called_once_with(SYMBOL, "1m", BARS, "moomoo")


def test_fetched_options_written_to_db() -> None:
    repo = _empty_repo()
    repo.load_price_bars_with_source_priority.return_value = (BARS, "moomoo")
    repo.load_session_metrics.return_value = METRICS
    gw = _gateway(options=[OPT_QUOTE])
    policy = DataFetchPolicy(
        history_source_order=["moomoo"],
        ibkr_options_enabled=False,
    )
    loader = TrendInputLoader(
        repository=repo,
        gateways={"moomoo": gw},
        session_open=HIST_SESSION_OPEN,
        policy=policy,
    )
    loader.load(SYMBOL, HIST_EVAL_TIME)

    repo.save_option_quotes.assert_called_once_with([OPT_QUOTE], "moomoo")
