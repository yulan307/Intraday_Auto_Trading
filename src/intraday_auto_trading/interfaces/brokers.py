from __future__ import annotations

from datetime import date, datetime
from typing import Protocol, Sequence

from intraday_auto_trading.models import (
    MinuteBar,
    OpeningImbalance,
    OptionQuote,
    OrderInstruction,
    ProviderCapabilities,
    SessionMetrics,
)


class MarketDataGateway(Protocol):
    provider_name: str

    def probe_capabilities(self) -> ProviderCapabilities: ...

    def get_official_open(self, symbol: str, at_time: datetime) -> float: ...

    def get_last_price(self, symbol: str, at_time: datetime) -> float: ...

    def get_session_vwap(self, symbol: str, at_time: datetime) -> float: ...

    def get_session_metrics(self, symbol: str, at_time: datetime) -> SessionMetrics | None: ...

    def get_minute_bars(self, symbol: str, start: datetime, end: datetime) -> list[MinuteBar]: ...

    def get_direct_fifteen_minute_bars(self, symbol: str, start: datetime, end: datetime) -> list[MinuteBar]: ...

    def get_opening_imbalance(self, symbol: str, trade_date: date) -> OpeningImbalance | None: ...

    def get_option_quotes(self, symbol: str, at_time: datetime) -> list[OptionQuote]: ...


class BatchMarketDataGateway(MarketDataGateway, Protocol):
    def get_minute_bars_batch(
        self,
        symbols: Sequence[str],
        start: datetime,
        end: datetime,
    ) -> dict[str, list[MinuteBar]]: ...

    def get_direct_fifteen_minute_bars_batch(
        self,
        symbols: Sequence[str],
        start: datetime,
        end: datetime,
    ) -> dict[str, list[MinuteBar]]: ...

    def get_option_quotes_batch(
        self,
        symbols: Sequence[str],
        at_time: datetime,
    ) -> dict[str, list[OptionQuote]]: ...


class BrokerGateway(Protocol):
    def place_order(self, instruction: OrderInstruction) -> str: ...

    def cancel_order(self, broker_order_id: str) -> None: ...


class AccountGateway(Protocol):
    def get_completed_orders_this_week(self, symbol: str) -> int: ...

    def has_open_position(self, symbol: str) -> bool: ...
