from __future__ import annotations

from datetime import datetime
from typing import Protocol

from intraday_auto_trading.models import MinuteBar, OptionQuote, OrderInstruction


class MarketDataGateway(Protocol):
    def get_official_open(self, symbol: str, at_time: datetime) -> float: ...

    def get_last_price(self, symbol: str, at_time: datetime) -> float: ...

    def get_session_vwap(self, symbol: str, at_time: datetime) -> float: ...

    def get_minute_bars(self, symbol: str, start: datetime, end: datetime) -> list[MinuteBar]: ...

    def get_option_quotes(self, symbol: str, at_time: datetime) -> list[OptionQuote]: ...


class BrokerGateway(Protocol):
    def place_order(self, instruction: OrderInstruction) -> str: ...

    def cancel_order(self, broker_order_id: str) -> None: ...


class AccountGateway(Protocol):
    def get_completed_orders_this_week(self, symbol: str) -> int: ...

    def has_open_position(self, symbol: str) -> bool: ...

