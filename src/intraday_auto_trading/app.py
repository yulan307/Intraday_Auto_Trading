from __future__ import annotations

from datetime import datetime

from intraday_auto_trading.config import Settings
from intraday_auto_trading.gateways.ibkr_market_data import IBKRMarketDataGateway, RealIBKRBackend
from intraday_auto_trading.gateways.moomoo_options import MoomooMarketDataGateway, RealMoomooBackend
from intraday_auto_trading.gateways.yfinance_market_data import RealYfinanceBackend, YfinanceMarketDataGateway
from intraday_auto_trading.models import AccountSymbolState, SelectionResult, TrendSignal
from intraday_auto_trading.persistence.market_data_repository import SqliteMarketDataRepository
from intraday_auto_trading.services.backtest_data_service import BacktestDataService
from intraday_auto_trading.services.executor import ExecutionPlanner
from intraday_auto_trading.services.market_data_sync import MarketDataSyncService
from intraday_auto_trading.services.selector import SymbolSelector
from intraday_auto_trading.services.trend_input_loader import BacktestTrendInputLoader, LiveTrendInputLoader


class TradingWorkflow:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.selector = SymbolSelector(settings.selection)
        self.execution_planner = ExecutionPlanner()

    def choose_symbol(
        self,
        signals: list[TrendSignal],
        account_states: dict[str, AccountSymbolState],
    ) -> SelectionResult:
        return self.selector.select(signals, account_states)


def build_market_data_sync_service(
    settings: Settings,
    ibkr_profile_override: str | None = None,
) -> MarketDataSyncService:
    profile_name, ibkr_profile = settings.ibkr.resolve_profile(ibkr_profile_override)
    repository = SqliteMarketDataRepository(settings.data.market_data_db)
    providers = {
        "ibkr": IBKRMarketDataGateway(
            profile_name=profile_name,
            profile=ibkr_profile,
            backend=RealIBKRBackend(
                profile=ibkr_profile,
                exchange_timezone=settings.project.timezone,
            ),
            exchange_timezone=settings.project.timezone,
        ),
        "moomoo": MoomooMarketDataGateway(
            settings.moomoo,
            backend=RealMoomooBackend(settings.moomoo),
        ),
    }
    return MarketDataSyncService(
        repository=repository,
        providers=providers,
        enabled_data_types=settings.data.data_types,
        enable_direct_15m=settings.data.enable_direct_15m,
        enable_derived_15m=settings.data.enable_derived_15m,
    )


def build_live_trend_input_loader(
    settings: Settings,
    session_open: datetime,
    ibkr_profile_override: str | None = None,
) -> LiveTrendInputLoader:
    profile_name, ibkr_profile = settings.ibkr.resolve_profile(ibkr_profile_override)
    gateway = IBKRMarketDataGateway(
        profile_name=profile_name,
        profile=ibkr_profile,
        backend=RealIBKRBackend(
            profile=ibkr_profile,
            exchange_timezone=settings.project.timezone,
        ),
        exchange_timezone=settings.project.timezone,
    )
    return LiveTrendInputLoader(gateway=gateway, session_open=session_open)


def build_backtest_trend_input_loader(
    settings: Settings,
    session_open: datetime,
    bar_source_priority: list[str] | None = None,
) -> BacktestTrendInputLoader:
    repository = SqliteMarketDataRepository(settings.data.market_data_db)
    return BacktestTrendInputLoader(
        repository=repository,
        session_open=session_open,
        bar_source_priority=bar_source_priority,
    )


def build_backtest_data_service(
    settings: Settings,
    ibkr_profile_override: str | None = None,
) -> BacktestDataService:
    profile_name, ibkr_profile = settings.ibkr.resolve_profile(ibkr_profile_override)
    repository = SqliteMarketDataRepository(settings.data.market_data_db)

    ibkr_gw = IBKRMarketDataGateway(
        profile_name=profile_name,
        profile=ibkr_profile,
        backend=RealIBKRBackend(
            profile=ibkr_profile,
            exchange_timezone=settings.project.timezone,
        ),
        exchange_timezone=settings.project.timezone,
    )

    moomoo_gw = MoomooMarketDataGateway(
        settings.moomoo,
        backend=RealMoomooBackend(settings.moomoo),
    )

    yfinance_backend = (
        RealYfinanceBackend(request_timeout_seconds=settings.yfinance.request_timeout_seconds)
        if settings.yfinance.enabled
        else None
    )
    yfinance_gw = YfinanceMarketDataGateway(backend=yfinance_backend)

    return BacktestDataService(
        repository=repository,
        ibkr_gateway=ibkr_gw,
        moomoo_gateway=moomoo_gw,
        yfinance_gateway=yfinance_gw,
    )
