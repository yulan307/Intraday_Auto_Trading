"""Unit tests for VirtualAccount (AccountGateway + BrokerGateway)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from intraday_auto_trading.gateways.virtual_account import VirtualAccount
from intraday_auto_trading.models import BuyStrategy, CapabilityStatus, MinuteBar, OrderInstruction

SYMBOL = "SPY"
NOW = datetime(2026, 4, 16, 9, 35, tzinfo=timezone.utc)


def make_bar(
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float = 1000.0,
) -> MinuteBar:
    return MinuteBar(timestamp=NOW, open=open_, high=high, low=low, close=close, volume=volume)


def make_instruction(
    symbol: str = SYMBOL,
    quantity: int = 10,
    limit_price: float | None = None,
) -> OrderInstruction:
    return OrderInstruction(
        symbol=symbol,
        strategy=BuyStrategy.IMMEDIATE_BUY,
        quantity=quantity,
        limit_price=limit_price,
    )


# ---------------------------------------------------------------------------
# 初始状态
# ---------------------------------------------------------------------------


def test_initial_state() -> None:
    account = VirtualAccount(initial_cash=50_000.0)
    summary = account.get_account_summary()

    assert summary.cash_balance == 50_000.0
    assert summary.net_liquidation == 50_000.0
    assert account.get_positions() == []
    assert account.get_open_orders() == []


def test_probe_capabilities_all_available() -> None:
    account = VirtualAccount()
    caps = account.probe_capabilities()

    assert caps.account_summary == CapabilityStatus.AVAILABLE
    assert caps.positions == CapabilityStatus.AVAILABLE
    assert caps.open_orders == CapabilityStatus.AVAILABLE


# ---------------------------------------------------------------------------
# place_order / cancel_order
# ---------------------------------------------------------------------------


def test_place_order_returns_unique_ids() -> None:
    account = VirtualAccount()
    id1 = account.place_order(make_instruction())
    id2 = account.place_order(make_instruction())
    assert id1 != id2


def test_placed_order_appears_in_open_orders() -> None:
    account = VirtualAccount()
    order_id = account.place_order(make_instruction(quantity=5, limit_price=100.0))

    open_orders = account.get_open_orders()
    assert len(open_orders) == 1
    assert open_orders[0].broker_order_id == order_id
    assert open_orders[0].status == "Submitted"
    assert open_orders[0].limit_price == 100.0


def test_cancel_order_removes_from_open_orders() -> None:
    account = VirtualAccount()
    order_id = account.place_order(make_instruction())
    account.cancel_order(order_id)

    assert account.get_open_orders() == []


def test_cancel_nonexistent_order_raises() -> None:
    account = VirtualAccount()
    with pytest.raises(KeyError):
        account.cancel_order("999")


def test_cancel_already_filled_order_raises() -> None:
    account = VirtualAccount()
    order_id = account.place_order(make_instruction(quantity=1, limit_price=None))
    bar = make_bar(open_=100.0, high=101.0, low=99.0, close=100.5)
    account.process_bar(SYMBOL, bar)

    with pytest.raises(ValueError, match="Cannot cancel"):
        account.cancel_order(order_id)


# ---------------------------------------------------------------------------
# process_bar — MKT 单
# ---------------------------------------------------------------------------


def test_mkt_order_fills_at_bar_open() -> None:
    account = VirtualAccount(initial_cash=10_000.0)
    account.place_order(make_instruction(quantity=10, limit_price=None))
    bar = make_bar(open_=100.0, high=102.0, low=99.0, close=101.0)

    filled = account.process_bar(SYMBOL, bar)

    assert len(filled) == 1
    assert account.get_open_orders() == []
    assert account.get_account_summary().cash_balance == 10_000.0 - 100.0 * 10


def test_mkt_order_creates_position() -> None:
    account = VirtualAccount(initial_cash=10_000.0)
    account.place_order(make_instruction(quantity=5, limit_price=None))
    bar = make_bar(open_=200.0, high=202.0, low=198.0, close=201.0)
    account.process_bar(SYMBOL, bar)

    positions = account.get_positions()
    assert len(positions) == 1
    assert positions[0].symbol == SYMBOL
    assert positions[0].quantity == 5.0
    assert positions[0].avg_cost == 200.0


# ---------------------------------------------------------------------------
# process_bar — LMT 单
# ---------------------------------------------------------------------------


def test_lmt_order_fills_when_bar_low_reaches_limit() -> None:
    account = VirtualAccount(initial_cash=10_000.0)
    account.place_order(make_instruction(quantity=10, limit_price=99.5))
    bar = make_bar(open_=100.0, high=101.0, low=99.0, close=100.5)  # low=99.0 ≤ 99.5

    filled = account.process_bar(SYMBOL, bar)

    assert len(filled) == 1
    filled_orders = [o for o in account._orders.values() if o.status == "Filled"]
    assert filled_orders[0].avg_fill_price == 99.5


def test_lmt_order_does_not_fill_when_bar_low_above_limit() -> None:
    account = VirtualAccount(initial_cash=10_000.0)
    account.place_order(make_instruction(quantity=10, limit_price=98.0))
    bar = make_bar(open_=100.0, high=101.0, low=99.0, close=100.5)  # low=99.0 > 98.0

    filled = account.process_bar(SYMBOL, bar)

    assert filled == []
    assert len(account.get_open_orders()) == 1


def test_lmt_order_fills_when_bar_low_equals_limit() -> None:
    account = VirtualAccount(initial_cash=10_000.0)
    account.place_order(make_instruction(quantity=2, limit_price=99.0))
    bar = make_bar(open_=100.0, high=101.0, low=99.0, close=100.0)  # low == limit

    filled = account.process_bar(SYMBOL, bar)

    assert len(filled) == 1


# ---------------------------------------------------------------------------
# avg_cost 加权平均
# ---------------------------------------------------------------------------


def test_avg_cost_weighted_average_on_multiple_fills() -> None:
    account = VirtualAccount(initial_cash=50_000.0)

    # 第一笔：10 股 @ 100
    account.place_order(make_instruction(quantity=10, limit_price=None))
    account.process_bar(SYMBOL, make_bar(open_=100.0, high=101.0, low=99.0, close=100.0))

    # 第二笔：10 股 @ 110
    account.place_order(make_instruction(quantity=10, limit_price=None))
    account.process_bar(SYMBOL, make_bar(open_=110.0, high=111.0, low=109.0, close=110.0))

    pos = account.get_positions()[0]
    assert pos.quantity == 20.0
    assert pos.avg_cost == pytest.approx(105.0)


# ---------------------------------------------------------------------------
# get_completed_orders_this_week / has_open_position
# ---------------------------------------------------------------------------


def test_completed_orders_this_week_counts_filled() -> None:
    account = VirtualAccount(initial_cash=10_000.0)
    account.place_order(make_instruction(quantity=1, limit_price=None))
    account.process_bar(SYMBOL, make_bar(open_=100.0, high=101.0, low=99.0, close=100.0))

    assert account.get_completed_orders_this_week(SYMBOL) == 1
    assert account.get_completed_orders_this_week("AAPL") == 0


def test_has_open_position_true_after_fill() -> None:
    account = VirtualAccount(initial_cash=10_000.0)
    account.place_order(make_instruction(quantity=1, limit_price=None))
    account.process_bar(SYMBOL, make_bar(open_=100.0, high=101.0, low=99.0, close=100.0))

    assert account.has_open_position(SYMBOL) is True
    assert account.has_open_position("AAPL") is False


def test_has_open_position_false_before_fill() -> None:
    account = VirtualAccount()
    account.place_order(make_instruction())
    assert account.has_open_position(SYMBOL) is False


# ---------------------------------------------------------------------------
# fill_order (手动)
# ---------------------------------------------------------------------------


def test_manual_fill_order() -> None:
    account = VirtualAccount(initial_cash=10_000.0)
    order_id = account.place_order(make_instruction(quantity=3, limit_price=50.0))
    account.fill_order(order_id, fill_price=49.0)

    assert account.get_open_orders() == []
    assert account.get_account_summary().cash_balance == pytest.approx(10_000.0 - 49.0 * 3)


def test_manual_fill_raises_if_insufficient_cash() -> None:
    account = VirtualAccount(initial_cash=100.0)
    order_id = account.place_order(make_instruction(quantity=10, limit_price=None))
    with pytest.raises(ValueError, match="Insufficient cash"):
        account.fill_order(order_id, fill_price=100.0)


# ---------------------------------------------------------------------------
# reset
# ---------------------------------------------------------------------------


def test_reset_restores_initial_state() -> None:
    account = VirtualAccount(initial_cash=5_000.0)
    account.place_order(make_instruction(quantity=1, limit_price=None))
    account.process_bar(SYMBOL, make_bar(open_=100.0, high=101.0, low=99.0, close=100.0))

    account.reset()

    assert account.get_account_summary().cash_balance == 5_000.0
    assert account.get_positions() == []
    assert account.get_open_orders() == []


# ---------------------------------------------------------------------------
# process_bar — 多 symbol
# ---------------------------------------------------------------------------


def test_process_bar_only_fills_matching_symbol() -> None:
    account = VirtualAccount(initial_cash=50_000.0)
    account.place_order(make_instruction(symbol="SPY", quantity=1, limit_price=None))
    account.place_order(make_instruction(symbol="QQQ", quantity=1, limit_price=None))

    bar = make_bar(open_=100.0, high=101.0, low=99.0, close=100.0)
    filled = account.process_bar("SPY", bar)

    assert len(filled) == 1
    # QQQ 单仍然挂着
    open_orders = account.get_open_orders()
    assert len(open_orders) == 1
    assert open_orders[0].symbol == "QQQ"
