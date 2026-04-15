from __future__ import annotations

from datetime import datetime
from typing import Protocol, Sequence

from intraday_auto_trading.models import (
    MinuteBar,
    OpeningImbalance,
    OptionQuote,
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

    def save_session_metrics(self, metrics: SessionMetrics) -> None: ...

    def save_opening_imbalance(self, imbalance: OpeningImbalance) -> None: ...

    def save_option_quotes(self, quotes: Sequence[OptionQuote], source: str) -> None: ...

    def save_trend_snapshot(self, snapshot: TrendSnapshot) -> None: ...


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

