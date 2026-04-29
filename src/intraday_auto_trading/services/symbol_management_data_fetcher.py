from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from intraday_auto_trading.interfaces.brokers import MarketDataGateway
from intraday_auto_trading.interfaces.repositories import MarketDataRepository
from intraday_auto_trading.services.bar_data_service import BarDataService
from intraday_auto_trading.symbol_manager import SymbolGroupRegistry


DEFAULT_START_DATE = date(2026, 3, 31)
DEFAULT_END_DATE = date(2026, 4, 25)
FIXED_BAR_PROVIDERS = ["ibkr"]


@dataclass(slots=True)
class SymbolManagementDataFetchSummary:
    symbols: list[str]
    group_names: list[str]
    start_date: date
    end_date: date
    bar_providers: list[str]
    one_minute_bar_counts: dict[str, int]
    daily_bar_counts: dict[str, int]
    errors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SymbolManagementDataFetcher:
    repository: MarketDataRepository
    bar_data_service: BarDataService
    session_gateways: dict[str, MarketDataGateway]
    option_gateways: dict[str, MarketDataGateway]
    exchange_timezone: str = "America/New_York"

    def fetch_all_symbol_pool_data(
        self,
        symbol_groups: SymbolGroupRegistry,
        start_date: date = DEFAULT_START_DATE,
        end_date: date = DEFAULT_END_DATE,
        bar_providers: list[str] | None = None,
        force_refresh: bool = False,
    ) -> SymbolManagementDataFetchSummary:
        """Fetch bar data for every configured symbol pool.

        Option, session metrics, and opening imbalance data are intentionally not
        loaded in the bar-only market-data schema.
        """
        group_names = symbol_groups.list_names()
        symbols = _unique_symbols(symbol_groups)
        normalized_bar_providers = _normalize_fixed_bar_providers(bar_providers)

        one_minute_bars = self.bar_data_service.get_bars(
            symbols,
            "1m",
            start_date,
            end_date,
            source_order=normalized_bar_providers,
            force_refresh=force_refresh,
        )
        daily_bars = self.bar_data_service.get_bars(
            symbols,
            "1d",
            start_date,
            end_date,
            source_order=normalized_bar_providers,
            force_refresh=force_refresh,
        )

        return SymbolManagementDataFetchSummary(
            symbols=symbols,
            group_names=group_names,
            start_date=start_date,
            end_date=end_date,
            bar_providers=normalized_bar_providers,
            one_minute_bar_counts={
                symbol: len(one_minute_bars.get(symbol, [])) for symbol in symbols
            },
            daily_bar_counts={
                symbol: len(daily_bars.get(symbol, [])) for symbol in symbols
            },
            errors=[],
        )


def _unique_symbols(symbol_groups: SymbolGroupRegistry) -> list[str]:
    symbols: list[str] = []
    seen: set[str] = set()
    for group_name in symbol_groups.list_names():
        for symbol in symbol_groups.resolve(group_name).symbols:
            normalized = symbol.upper()
            if normalized in seen:
                continue
            seen.add(normalized)
            symbols.append(normalized)
    return symbols


def _normalize_fixed_bar_providers(bar_providers: list[str] | None) -> list[str]:
    normalized = [provider.lower() for provider in (bar_providers or FIXED_BAR_PROVIDERS)]
    if normalized != FIXED_BAR_PROVIDERS:
        raise ValueError("Bar data source is fixed to IB Gateway: ['ibkr'].")
    return FIXED_BAR_PROVIDERS.copy()
