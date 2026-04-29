"""Unit tests for SymbolSelector.select() — dev20 intraday signal comparison.

Logic under test:
1. order_filled=True → action="exit"
2. current_time >= force_buy_time → action="force_buy"
3. No buy_now signals → action="wait"
4. buy_now symbol's dev20_w is global max → action="place_order"
5. buy_now symbol's dev20_w is NOT global max → action="wait"
6. Multiple buy_now symbols → select the one with highest dev20_w
7. cancel_symbol is populated when active_order exists
8. active_order's dev20_w is included in global comparison
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from intraday_auto_trading.models import Dev20SignalResult
from intraday_auto_trading.services.selector import SymbolSelector


def _wait(dev20: float | None = None) -> Dev20SignalResult:
    return Dev20SignalResult(
        signal="wait",
        dev20=dev20,
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


def _buy(dev20: float, limit_price: float = 99.5) -> Dev20SignalResult:
    return Dev20SignalResult(
        signal="buy_now",
        dev20=dev20,
        s_dev20=0.001,
        ss_dev20=-0.0001,
        valley=0.0005,
        s_valley=-0.0002,
        ema5=98.0,
        ema10=99.0,
        ema20=99.5,
        vwap=100.0,
        limit_price=limit_price,
    )


sel = SymbolSelector()

T0 = datetime(2026, 4, 7, 10, 0)
FORCE_BUY_TIME = datetime(2026, 4, 7, 15, 59)


# ---------------------------------------------------------------------------
# 1. Exit: order filled
# ---------------------------------------------------------------------------

def test_exit_when_order_filled() -> None:
    signals = {"SPY": _buy(0.005), "QQQ": _wait(0.003)}
    result = sel.select(signals, order_filled=True)
    assert result.action == "exit"
    assert result.symbol is None


# ---------------------------------------------------------------------------
# 2. Force buy window
# ---------------------------------------------------------------------------

def test_force_buy_when_time_reached() -> None:
    signals = {"SPY": _wait(0.005)}
    result = sel.select(
        signals,
        current_time=FORCE_BUY_TIME + timedelta(seconds=1),
        force_buy_time=FORCE_BUY_TIME,
    )
    assert result.action == "force_buy"


def test_no_force_buy_before_time() -> None:
    signals = {"SPY": _wait(0.005)}
    result = sel.select(
        signals,
        current_time=FORCE_BUY_TIME - timedelta(minutes=1),
        force_buy_time=FORCE_BUY_TIME,
    )
    assert result.action == "wait"


# ---------------------------------------------------------------------------
# 3. No buy_now → wait
# ---------------------------------------------------------------------------

def test_wait_when_no_buy_signal() -> None:
    signals = {"SPY": _wait(0.01), "QQQ": _wait(0.02)}
    result = sel.select(signals)
    assert result.action == "wait"


def test_wait_when_all_dev20_none() -> None:
    signals = {"SPY": _wait(None), "QQQ": _wait(None)}
    result = sel.select(signals)
    assert result.action == "wait"


# ---------------------------------------------------------------------------
# 4. Single buy_now, dev20_w is global max → place_order
# ---------------------------------------------------------------------------

def test_place_order_when_single_buy_signal_is_max() -> None:
    signals = {
        "SPY": _buy(dev20=0.008, limit_price=99.5),
        "QQQ": _wait(dev20=0.003),
    }
    result = sel.select(signals)
    assert result.action == "place_order"
    assert result.symbol == "SPY"
    assert result.limit_price == 99.5
    assert result.dev20_at_order == pytest.approx(0.008)
    assert result.cancel_symbol is None


# ---------------------------------------------------------------------------
# 5. buy_now symbol's dev20_w < another symbol's dev20 → wait
# ---------------------------------------------------------------------------

def test_wait_when_buy_signal_not_global_max() -> None:
    signals = {
        "SPY": _buy(dev20=0.003),      # buy signal but lower dev20
        "QQQ": _wait(dev20=0.010),     # no buy signal but higher dev20
    }
    result = sel.select(signals)
    assert result.action == "wait"


# ---------------------------------------------------------------------------
# 6. Multiple buy_now → select highest dev20_w
# ---------------------------------------------------------------------------

def test_selects_highest_dev20_among_buy_candidates() -> None:
    signals = {
        "SPY": _buy(dev20=0.005, limit_price=98.0),
        "QQQ": _buy(dev20=0.012, limit_price=101.0),
        "AAPL": _wait(dev20=0.003),
    }
    result = sel.select(signals)
    assert result.action == "place_order"
    assert result.symbol == "QQQ"
    assert result.limit_price == 101.0
    assert result.dev20_at_order == pytest.approx(0.012)


# ---------------------------------------------------------------------------
# 7. cancel_symbol set when active_order exists
# ---------------------------------------------------------------------------

def test_cancel_symbol_populated_on_replace() -> None:
    signals = {
        "SPY": _buy(dev20=0.010, limit_price=99.5),
        "QQQ": _wait(dev20=0.003),
    }
    # Active order on QQQ with dev20_w = 0.004 (less than SPY's 0.010)
    result = sel.select(signals, active_order=("QQQ", 0.004))
    assert result.action == "place_order"
    assert result.symbol == "SPY"
    assert result.cancel_symbol == "QQQ"


# ---------------------------------------------------------------------------
# 8. active_order dev20_w included in global comparison
# ---------------------------------------------------------------------------

def test_wait_when_active_order_dev20_w_is_higher() -> None:
    signals = {
        "SPY": _buy(dev20=0.005),
        "QQQ": _wait(dev20=0.003),
    }
    # Active order on SCHD placed at dev20_w=0.020 (higher than SPY's 0.005)
    result = sel.select(signals, active_order=("SCHD", 0.020))
    assert result.action == "wait"


def test_place_order_when_buy_exceeds_active_order_dev20_w() -> None:
    signals = {
        "SPY": _buy(dev20=0.015, limit_price=99.0),
        "QQQ": _wait(dev20=0.003),
    }
    # Active order placed at dev20_w=0.010 (less than SPY's 0.015)
    result = sel.select(signals, active_order=("QQQ", 0.010))
    assert result.action == "place_order"
    assert result.symbol == "SPY"
    assert result.cancel_symbol == "QQQ"


# ---------------------------------------------------------------------------
# decay_fn interface
# ---------------------------------------------------------------------------

def test_decay_fn_returns_one_by_default() -> None:
    assert SymbolSelector._decay_fn(0) == 1.0
    assert SymbolSelector._decay_fn(5) == 1.0
    assert SymbolSelector._decay_fn(100) == 1.0


def test_completed_orders_applied_via_decay_fn() -> None:
    """With default decay=1.0, completed_orders does not affect ranking."""
    signals = {
        "SPY": _buy(dev20=0.010),
        "QQQ": _wait(dev20=0.003),
    }
    result_no_orders = sel.select(signals)
    result_with_orders = sel.select(signals, completed_orders={"SPY": 5, "QQQ": 0})
    # Both should produce same result since _decay_fn returns 1.0
    assert result_no_orders.action == result_with_orders.action
    assert result_no_orders.symbol == result_with_orders.symbol
