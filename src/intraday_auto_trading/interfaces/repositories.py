from __future__ import annotations

from datetime import datetime
from typing import Protocol, Sequence

from intraday_auto_trading.models import (
    DailyCoverage,
    MinuteBar,
    OpeningImbalance,
    OptionQuote,
    Order,
    SessionMetrics,
    SymbolInfo,
    TrendSnapshot,
)


class MarketDataRepository(Protocol):
    def initialize(self) -> None: ...

    def upsert_symbol(self, symbol_info: SymbolInfo) -> None: ...

    def save_price_bars(
        self,
        symbol: str,
        bar_size: str,
        bars: Sequence[MinuteBar],
        source: str,
    ) -> None: ...

    def load_price_bars(
        self,
        symbol: str,
        bar_size: str,
        start: datetime,
        end: datetime,
    ) -> list[MinuteBar]: ...

    def load_price_bars_with_source_priority(
        self,
        symbol: str,
        bar_size: str,
        start: datetime,
        end: datetime,
        source_priority: list[str],
    ) -> tuple[list[MinuteBar], str]: ...

    def save_session_metrics(self, metrics: SessionMetrics) -> None: ...

    def load_session_metrics(self, symbol: str, at_time: datetime) -> SessionMetrics | None: ...

    def load_option_quotes(self, symbol: str, start: datetime, end: datetime) -> list[OptionQuote]: ...

    def save_opening_imbalance(self, imbalance: OpeningImbalance) -> None: ...

    def save_option_quotes(self, quotes: Sequence[OptionQuote], source: str) -> None: ...

    def save_trend_snapshot(self, snapshot: TrendSnapshot) -> None: ...

    def save_daily_coverage(self, coverage: DailyCoverage) -> None: ...

    def load_daily_coverage(
        self, symbol: str, bar_size: str, trade_date: str
    ) -> DailyCoverage | None: ...

    def load_daily_coverage_range(
        self, symbols: list[str], bar_size: str, start_date: str, end_date: str
    ) -> dict[tuple[str, str], DailyCoverage]: ...


class BacktestAccountRepository(Protocol):
    def initialize(self) -> None: ...

    def create_run(
        self,
        run_id: str,
        name: str,
        symbols: list[str],
        start_date: str,
        end_date: str,
        initial_cash: float,
        config_snapshot: str,
    ) -> None: ...

    def save_order(self, run_id: str, order: Order, strategy: str) -> None: ...

    def load_orders(self, run_id: str) -> list[Order]: ...

