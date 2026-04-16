from __future__ import annotations

from datetime import datetime, timedelta, timezone
from intraday_auto_trading.models import (
    AccountCapabilities,
    AccountSummary,
    CapabilityStatus,
    MinuteBar,
    Order,
    OrderInstruction,
    Position,
)


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _week_start(dt: datetime) -> datetime:
    """ISO 周一 00:00 UTC。"""
    monday = dt - timedelta(days=dt.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


class VirtualAccount:
    """内存虚拟账户，同时满足 AccountGateway 和 BrokerGateway 协议。

    用法：
        account = VirtualAccount(initial_cash=100_000.0)

        # 在回测主循环中：
        order_id = account.place_order(instruction)
        filled_ids = account.process_bar(bar)   # 自动撮合
        account.cancel_order(order_id)           # 手动撤单

    撮合规则：
        - MKT（limit_price=None）：立即以 bar.open 成交
        - LMT BUY：bar.low ≤ limit_price 时以 limit_price 成交
    """

    provider_name: str = "virtual"

    def __init__(
        self,
        account_id: str = "VIRTUAL",
        initial_cash: float = 100_000.0,
    ) -> None:
        self._account_id = account_id
        self._initial_cash = initial_cash
        self._cash = initial_cash
        self._positions: dict[str, Position] = {}
        self._orders: dict[str, Order] = {}
        self._order_counter = 0

    # ------------------------------------------------------------------
    # AccountGateway protocol
    # ------------------------------------------------------------------

    def probe_capabilities(self) -> AccountCapabilities:
        return AccountCapabilities(
            provider=self.provider_name,
            account_summary=CapabilityStatus.AVAILABLE,
            positions=CapabilityStatus.AVAILABLE,
            open_orders=CapabilityStatus.AVAILABLE,
        )

    def get_account_summary(self) -> AccountSummary:
        equity = self._cash + sum(
            p.market_value for p in self._positions.values()
        )
        return AccountSummary(
            account_id=self._account_id,
            net_liquidation=equity,
            cash_balance=self._cash,
            buying_power=self._cash,
            timestamp=_now(),
        )

    def get_positions(self) -> list[Position]:
        return [p for p in self._positions.values() if p.quantity > 0]

    def get_open_orders(self) -> list[Order]:
        return [o for o in self._orders.values() if o.status == "Submitted"]

    def get_completed_orders_this_week(self, symbol: str) -> int:
        week_start = _week_start(_now())
        return sum(
            1
            for o in self._orders.values()
            if o.symbol == symbol
            and o.status == "Filled"
            and o.timestamp >= week_start
        )

    def has_open_position(self, symbol: str) -> bool:
        pos = self._positions.get(symbol)
        return pos is not None and pos.quantity > 0

    # ------------------------------------------------------------------
    # BrokerGateway protocol
    # ------------------------------------------------------------------

    def place_order(self, instruction: OrderInstruction) -> str:
        self._order_counter += 1
        order_id = str(self._order_counter)
        order = Order(
            broker_order_id=order_id,
            account_id=self._account_id,
            symbol=instruction.symbol,
            action="BUY",
            total_qty=float(instruction.quantity),
            filled_qty=0.0,
            remaining_qty=float(instruction.quantity),
            status="Submitted",
            limit_price=instruction.limit_price,
            avg_fill_price=0.0,
            timestamp=_now(),
        )
        self._orders[order_id] = order
        return order_id

    def cancel_order(self, broker_order_id: str) -> None:
        order = self._orders.get(broker_order_id)
        if order is None:
            raise KeyError(f"Order {broker_order_id!r} not found")
        if order.status != "Submitted":
            raise ValueError(
                f"Cannot cancel order {broker_order_id!r} with status {order.status!r}"
            )
        self._orders[broker_order_id] = Order(
            broker_order_id=order.broker_order_id,
            account_id=order.account_id,
            symbol=order.symbol,
            action=order.action,
            total_qty=order.total_qty,
            filled_qty=order.filled_qty,
            remaining_qty=order.remaining_qty,
            status="Cancelled",
            limit_price=order.limit_price,
            avg_fill_price=order.avg_fill_price,
            timestamp=_now(),
        )

    # ------------------------------------------------------------------
    # Backtest-specific methods (not in protocol)
    # ------------------------------------------------------------------

    def process_bar(self, symbol: str, bar: MinuteBar) -> list[str]:
        """撮合指定 symbol 当前 bar 的所有挂单，返回本次成交的 order_id 列表。

        撮合规则：
        - MKT（limit_price=None）→ 以 bar.open 立即成交
        - LMT BUY → bar.low ≤ limit_price 时以 limit_price 成交
        """
        filled_ids: list[str] = []
        for order_id, order in list(self._orders.items()):
            if order.status != "Submitted" or order.symbol != symbol:
                continue
            fill_price = self._match_price(order, bar)
            if fill_price is None:
                continue
            self.fill_order(order_id, fill_price)
            filled_ids.append(order_id)
        return filled_ids

    def fill_order(
        self,
        order_id: str,
        fill_price: float,
        fill_qty: float | None = None,
    ) -> None:
        """手动强制成交（供特殊场景或单元测试使用）。"""
        order = self._orders.get(order_id)
        if order is None:
            raise KeyError(f"Order {order_id!r} not found")
        if order.status != "Submitted":
            raise ValueError(
                f"Cannot fill order {order_id!r} with status {order.status!r}"
            )

        qty = fill_qty if fill_qty is not None else order.remaining_qty
        cost = fill_price * qty
        if cost > self._cash:
            raise ValueError(
                f"Insufficient cash: need {cost:.2f}, have {self._cash:.2f}"
            )

        self._cash -= cost
        self._update_position(order.symbol, qty, fill_price)

        self._orders[order_id] = Order(
            broker_order_id=order.broker_order_id,
            account_id=order.account_id,
            symbol=order.symbol,
            action=order.action,
            total_qty=order.total_qty,
            filled_qty=order.filled_qty + qty,
            remaining_qty=order.remaining_qty - qty,
            status="Filled",
            limit_price=order.limit_price,
            avg_fill_price=fill_price,
            timestamp=_now(),
        )

    def reset(self) -> None:
        """重置到初始状态，供多轮回测复用同一实例。"""
        self._cash = self._initial_cash
        self._positions = {}
        self._orders = {}
        self._order_counter = 0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _match_price(order: Order, bar: MinuteBar) -> float | None:
        if order.limit_price is None:
            # MKT → bar.open
            return bar.open
        if bar.low <= order.limit_price:
            # LMT BUY → limit_price
            return order.limit_price
        return None

    def _update_position(self, symbol: str, qty: float, fill_price: float) -> None:
        existing = self._positions.get(symbol)
        if existing is None or existing.quantity == 0:
            new_qty = qty
            new_avg = fill_price
        else:
            new_qty = existing.quantity + qty
            new_avg = (
                (existing.avg_cost * existing.quantity + fill_price * qty) / new_qty
            )

        market_value = new_qty * fill_price
        self._positions[symbol] = Position(
            account_id=self._account_id,
            symbol=symbol,
            quantity=new_qty,
            avg_cost=new_avg,
            market_value=market_value,
            unrealized_pnl=market_value - new_avg * new_qty,
            realized_pnl=0.0,
            timestamp=_now(),
        )
