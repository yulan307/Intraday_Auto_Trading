from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
import importlib.util
import socket
from threading import Event, Lock, Thread
from typing import Iterator

from intraday_auto_trading.config import IBKRProfileSettings
from intraday_auto_trading.models import (
    AccountCapabilities,
    AccountSummary,
    CapabilityStatus,
    Order,
    OrderInstruction,
    Position,
)

try:
    from ibapi.client import EClient
    from ibapi.contract import Contract
    from ibapi.order import Order as IBOrder
    from ibapi.wrapper import EWrapper
except ModuleNotFoundError:  # pragma: no cover - exercised by capability checks
    EClient = object  # type: ignore[assignment]
    EWrapper = object  # type: ignore[assignment]
    Contract = None  # type: ignore[assignment]
    IBOrder = None  # type: ignore[assignment]


INFO_ERROR_CODES = {2104, 2106, 2107, 2108, 2158, 2176}

# AccountSummary tags to request from IBKR
_ACCOUNT_TAGS = "NetLiquidation,TotalCashValue,BuyingPower"


# ---------------------------------------------------------------------------
# Internal request state dataclasses
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _AccountSummaryRequest:
    done: Event = field(default_factory=Event)
    net_liquidation: float = 0.0
    cash_balance: float = 0.0
    buying_power: float = 0.0
    error_message: str | None = None


@dataclass(slots=True)
class _PositionsRequest:
    done: Event = field(default_factory=Event)
    positions: list[Position] = field(default_factory=list)
    error_message: str | None = None


@dataclass(slots=True)
class _OpenOrdersRequest:
    done: Event = field(default_factory=Event)
    orders: dict[int, Order] = field(default_factory=dict)
    error_message: str | None = None


@dataclass(slots=True)
class _PlaceOrderRequest:
    done: Event = field(default_factory=Event)
    order_id: int = 0
    status: str = ""
    error_message: str | None = None


# ---------------------------------------------------------------------------
# EWrapper/EClient implementations
# ---------------------------------------------------------------------------


class _IBAccountApp(EWrapper, EClient):  # type: ignore[misc]
    """EWrapper for account summary and position queries."""

    def __init__(self) -> None:
        EClient.__init__(self, self)
        self.connected_event = Event()
        self.connection_error: str | None = None
        self._next_order_id: int = 1
        self._account_requests: dict[int, _AccountSummaryRequest] = {}
        self._position_request: _PositionsRequest | None = None
        self._open_orders_request: _OpenOrdersRequest | None = None
        self._place_requests: dict[int, _PlaceOrderRequest] = {}
        self._lock = Lock()

    def nextValidId(self, orderId: int) -> None:  # noqa: N802
        self._next_order_id = orderId
        self.connected_event.set()

    def error(self, reqId: int, errorCode: int, errorString: str, advancedOrderRejectJson: str = "") -> None:  # noqa: N802
        if errorCode in INFO_ERROR_CODES:
            return

        message = f"IBKR error {errorCode}: {errorString}"
        if reqId in {-1, 0}:
            self.connection_error = message
            self.connected_event.set()
            return

        with self._lock:
            acct_req = self._account_requests.get(reqId)
            place_req = self._place_requests.get(reqId)

        if acct_req is not None:
            acct_req.error_message = message
            acct_req.done.set()
        if place_req is not None:
            place_req.error_message = message
            place_req.done.set()

    # -- Account summary callbacks --

    def accountSummary(self, reqId: int, account: str, tag: str, value: str, currency: str) -> None:  # noqa: N802
        with self._lock:
            req = self._account_requests.get(reqId)
        if req is None:
            return
        try:
            fval = float(value)
        except (ValueError, TypeError):
            return
        if tag == "NetLiquidation":
            req.net_liquidation = fval
        elif tag == "TotalCashValue":
            req.cash_balance = fval
        elif tag == "BuyingPower":
            req.buying_power = fval

    def accountSummaryEnd(self, reqId: int) -> None:  # noqa: N802
        with self._lock:
            req = self._account_requests.get(reqId)
        if req is not None:
            req.done.set()

    # -- Position callbacks --

    def position(self, account: str, contract, pos: float, avgCost: float) -> None:  # noqa: N802
        with self._lock:
            req = self._position_request
        if req is None or contract is None:
            return
        symbol = getattr(contract, "symbol", "")
        req.positions.append(
            Position(
                account_id=account,
                symbol=symbol,
                quantity=float(pos),
                avg_cost=float(avgCost),
                market_value=0.0,
                unrealized_pnl=0.0,
                realized_pnl=0.0,
                timestamp=datetime.now(),
            )
        )

    def positionEnd(self) -> None:  # noqa: N802
        with self._lock:
            req = self._position_request
        if req is not None:
            req.done.set()

    # -- Open order callbacks --

    def openOrder(self, orderId: int, contract, order, orderState) -> None:  # noqa: N802
        with self._lock:
            req = self._open_orders_request
        if req is None or contract is None or order is None:
            return
        symbol = getattr(contract, "symbol", "")
        action = getattr(order, "action", "")
        total_qty = float(getattr(order, "totalQuantity", 0))
        lmt_price_raw = getattr(order, "lmtPrice", None)
        limit_price = float(lmt_price_raw) if lmt_price_raw and float(lmt_price_raw) > 0 else None
        account = getattr(order, "account", "")
        status_str = getattr(orderState, "status", "")
        req.orders[orderId] = Order(
            broker_order_id=str(orderId),
            account_id=account,
            symbol=symbol,
            action=action,
            total_qty=total_qty,
            filled_qty=0.0,
            remaining_qty=total_qty,
            status=status_str,
            limit_price=limit_price,
            avg_fill_price=0.0,
            timestamp=datetime.now(),
        )

    def orderStatus(  # noqa: N802
        self,
        orderId: int,
        status: str,
        filled: float,
        remaining: float,
        avgFillPrice: float,
        permId: int,
        parentId: int,
        lastFillPrice: float,
        clientId: int,
        whyHeld: str,
        mktCapPrice: float = 0.0,
    ) -> None:
        with self._lock:
            oo_req = self._open_orders_request
            place_req = self._place_requests.get(orderId)

        if oo_req is not None and orderId in oo_req.orders:
            existing = oo_req.orders[orderId]
            oo_req.orders[orderId] = Order(
                broker_order_id=existing.broker_order_id,
                account_id=existing.account_id,
                symbol=existing.symbol,
                action=existing.action,
                total_qty=existing.total_qty,
                filled_qty=float(filled),
                remaining_qty=float(remaining),
                status=status,
                limit_price=existing.limit_price,
                avg_fill_price=float(avgFillPrice),
                timestamp=existing.timestamp,
            )

        if place_req is not None:
            place_req.status = status
            if status in {"Submitted", "PreSubmitted", "Filled"}:
                place_req.done.set()

    def openOrderEnd(self) -> None:  # noqa: N802
        with self._lock:
            req = self._open_orders_request
        if req is not None:
            req.done.set()

    # -- Request registration helpers --

    def register_account_request(self, req_id: int) -> _AccountSummaryRequest:
        req = _AccountSummaryRequest()
        with self._lock:
            self._account_requests[req_id] = req
        return req

    def pop_account_request(self, req_id: int) -> None:
        with self._lock:
            self._account_requests.pop(req_id, None)

    def register_position_request(self) -> _PositionsRequest:
        req = _PositionsRequest()
        with self._lock:
            self._position_request = req
        return req

    def clear_position_request(self) -> None:
        with self._lock:
            self._position_request = None

    def register_open_orders_request(self) -> _OpenOrdersRequest:
        req = _OpenOrdersRequest()
        with self._lock:
            self._open_orders_request = req
        return req

    def clear_open_orders_request(self) -> None:
        with self._lock:
            self._open_orders_request = None

    def register_place_request(self, order_id: int) -> _PlaceOrderRequest:
        req = _PlaceOrderRequest(order_id=order_id)
        with self._lock:
            self._place_requests[order_id] = req
        return req

    def pop_place_request(self, order_id: int) -> None:
        with self._lock:
            self._place_requests.pop(order_id, None)


# ---------------------------------------------------------------------------
# IBKRAccountGateway
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class IBKRAccountGateway:
    profile_name: str
    profile: IBKRProfileSettings
    socket_timeout_seconds: float = 0.5
    connect_timeout_seconds: float = 5.0
    request_timeout_seconds: float = 30.0

    def probe_capabilities(self) -> AccountCapabilities:
        status = self._base_status()
        return AccountCapabilities(
            provider=f"ibkr-{self.profile_name}",
            account_summary=status,
            positions=status,
            open_orders=status,
        )

    def get_account_summary(self) -> AccountSummary:
        self._require_available()
        req_id = 1
        with self._connected_app() as app:
            req = app.register_account_request(req_id)
            app.reqAccountSummary(req_id, "All", _ACCOUNT_TAGS)
            try:
                if not req.done.wait(self.request_timeout_seconds):
                    raise RuntimeError("Timed out waiting for account summary.")
            finally:
                app.cancelAccountSummary(req_id)
                app.pop_account_request(req_id)
        if req.error_message:
            raise RuntimeError(req.error_message)
        return AccountSummary(
            account_id=self.profile.account_id,
            net_liquidation=req.net_liquidation,
            cash_balance=req.cash_balance,
            buying_power=req.buying_power,
            timestamp=datetime.now(),
        )

    def get_positions(self) -> list[Position]:
        self._require_available()
        with self._connected_app() as app:
            req = app.register_position_request()
            app.reqPositions()
            try:
                if not req.done.wait(self.request_timeout_seconds):
                    raise RuntimeError("Timed out waiting for positions.")
            finally:
                app.cancelPositions()
                app.clear_position_request()
        if req.error_message:
            raise RuntimeError(req.error_message)
        return req.positions

    def get_open_orders(self) -> list[Order]:
        self._require_available()
        with self._connected_app() as app:
            req = app.register_open_orders_request()
            app.reqAllOpenOrders()
            try:
                if not req.done.wait(self.request_timeout_seconds):
                    raise RuntimeError("Timed out waiting for open orders.")
            finally:
                app.clear_open_orders_request()
        if req.error_message:
            raise RuntimeError(req.error_message)
        return list(req.orders.values())

    def get_completed_orders_this_week(self, symbol: str) -> int:
        orders = self.get_open_orders()
        return sum(
            1 for o in orders
            if o.symbol == symbol and o.status == "Filled"
        )

    def has_open_position(self, symbol: str) -> bool:
        positions = self.get_positions()
        return any(p.symbol == symbol and p.quantity != 0 for p in positions)

    def _base_status(self) -> CapabilityStatus:
        if importlib.util.find_spec("ibapi") is None:
            return CapabilityStatus.UNAVAILABLE
        if not self._is_socket_reachable():
            return CapabilityStatus.UNAVAILABLE
        return CapabilityStatus.AVAILABLE

    def _require_available(self) -> None:
        if self._base_status() is not CapabilityStatus.AVAILABLE:
            raise RuntimeError(
                f"IB Gateway {self.profile_name} profile is not reachable at "
                f"{self.profile.host}:{self.profile.port}."
            )

    def _is_socket_reachable(self) -> bool:
        try:
            with socket.create_connection(
                (self.profile.host, self.profile.port),
                timeout=self.socket_timeout_seconds,
            ):
                return True
        except OSError:
            return False

    @contextmanager
    def _connected_app(self) -> Iterator[_IBAccountApp]:
        app = _IBAccountApp()
        app.connect(self.profile.host, self.profile.port, self.profile.account_client_id)
        worker = Thread(target=app.run, daemon=True)
        worker.start()
        if not app.connected_event.wait(self.connect_timeout_seconds):
            app.disconnect()
            raise RuntimeError("Timed out connecting to IB Gateway.")
        if app.connection_error:
            app.disconnect()
            raise RuntimeError(app.connection_error)
        try:
            yield app
        finally:
            app.disconnect()
            worker.join(timeout=1.0)


# ---------------------------------------------------------------------------
# IBKRBrokerGateway
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class IBKRBrokerGateway:
    profile_name: str
    profile: IBKRProfileSettings
    socket_timeout_seconds: float = 0.5
    connect_timeout_seconds: float = 5.0
    request_timeout_seconds: float = 30.0

    def place_order(self, instruction: OrderInstruction) -> str:
        if self.profile.readonly:
            raise RuntimeError(
                "IBKRBrokerGateway is in readonly mode. Set readonly=false to place orders."
            )
        self._require_available()
        with self._connected_app() as app:
            order_id = app._next_order_id
            req = app.register_place_request(order_id)
            contract = self._stock_contract(instruction.symbol)
            order = self._build_order(instruction)
            app.placeOrder(order_id, contract, order)
            if not req.done.wait(self.request_timeout_seconds):
                app.pop_place_request(order_id)
                raise RuntimeError(
                    f"Timed out waiting for order acknowledgment for {instruction.symbol}."
                )
            app.pop_place_request(order_id)
        if req.error_message:
            raise RuntimeError(req.error_message)
        return str(order_id)

    def cancel_order(self, broker_order_id: str) -> None:
        if self.profile.readonly:
            raise RuntimeError(
                "IBKRBrokerGateway is in readonly mode. Set readonly=false to cancel orders."
            )
        self._require_available()
        with self._connected_app() as app:
            app.cancelOrder(int(broker_order_id), "")

    def _build_order(self, instruction: OrderInstruction) -> IBOrder:
        order = IBOrder()
        order.action = "BUY"
        order.totalQuantity = instruction.quantity
        order.tif = "DAY"
        order.transmit = True
        if self.profile.account_id:
            order.account = self.profile.account_id
        if instruction.limit_price is not None:
            order.orderType = "LMT"
            order.lmtPrice = instruction.limit_price
        else:
            order.orderType = "MKT"
        return order

    def _stock_contract(self, symbol: str) -> Contract:
        contract = Contract()
        contract.symbol = symbol
        contract.secType = "STK"
        contract.exchange = "SMART"
        contract.currency = "USD"
        return contract

    def _base_status(self) -> CapabilityStatus:
        if importlib.util.find_spec("ibapi") is None:
            return CapabilityStatus.UNAVAILABLE
        if not self._is_socket_reachable():
            return CapabilityStatus.UNAVAILABLE
        return CapabilityStatus.AVAILABLE

    def _require_available(self) -> None:
        if self._base_status() is not CapabilityStatus.AVAILABLE:
            raise RuntimeError(
                f"IB Gateway {self.profile_name} profile is not reachable at "
                f"{self.profile.host}:{self.profile.port}."
            )

    def _is_socket_reachable(self) -> bool:
        try:
            with socket.create_connection(
                (self.profile.host, self.profile.port),
                timeout=self.socket_timeout_seconds,
            ):
                return True
        except OSError:
            return False

    @contextmanager
    def _connected_app(self) -> Iterator[_IBAccountApp]:
        app = _IBAccountApp()
        app.connect(self.profile.host, self.profile.port, self.profile.broker_client_id)
        worker = Thread(target=app.run, daemon=True)
        worker.start()
        if not app.connected_event.wait(self.connect_timeout_seconds):
            app.disconnect()
            raise RuntimeError("Timed out connecting to IB Gateway.")
        if app.connection_error:
            app.disconnect()
            raise RuntimeError(app.connection_error)
        try:
            yield app
        finally:
            app.disconnect()
            worker.join(timeout=1.0)
