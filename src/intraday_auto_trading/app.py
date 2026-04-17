from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path

from intraday_auto_trading.config import Settings
from intraday_auto_trading.gateways.ibkr_market_data import IBKRMarketDataGateway, RealIBKRBackend
from intraday_auto_trading.gateways.moomoo_options import MoomooMarketDataGateway, RealMoomooBackend
from intraday_auto_trading.gateways.yfinance_market_data import RealYfinanceBackend, YfinanceMarketDataGateway
from intraday_auto_trading.models import AccountSymbolState, SelectionResult, TrendSignal
from intraday_auto_trading.persistence.market_data_repository import SqliteMarketDataRepository
from intraday_auto_trading.services.backtest_data_service import BacktestDataService
from intraday_auto_trading.services.backtest_chain_validation import BacktestChainValidationService
from intraday_auto_trading.services.data_fetch_policy import DataFetchPolicy, default_policy
from intraday_auto_trading.services.executor import ExecutionPlanner
from intraday_auto_trading.services.market_data_sync import MarketDataSyncService
from intraday_auto_trading.services.selector import SymbolSelector
from intraday_auto_trading.services.trend_input_loader import TrendInputLoader
from intraday_auto_trading.gateways.virtual_account import VirtualAccount


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


def build_trend_input_loader(
    settings: Settings,
    session_open: datetime,
    ibkr_profile_override: str | None = None,
    policy: DataFetchPolicy | None = None,
) -> TrendInputLoader:
    """Build the unified TrendInputLoader with all available market data gateways.

    The loader applies DB-first caching and automatically selects live vs historical
    source order based on whether eval_time falls on today (ET) or a past date.
    Pass a custom policy to override default source priority rules.
    """
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

    gateways = {"ibkr": ibkr_gw, "moomoo": moomoo_gw, "yfinance": yfinance_gw}
    return TrendInputLoader(
        repository=repository,
        gateways=gateways,
        session_open=session_open,
        policy=policy or default_policy(),
    )


def build_virtual_account(
    account_id: str = "VIRTUAL",
    initial_cash: float = 100_000.0,
) -> VirtualAccount:
    """构造内存虚拟账户，同时满足 AccountGateway 和 BrokerGateway 协议。"""
    return VirtualAccount(account_id=account_id, initial_cash=initial_cash)


def build_backtest_data_service(
    settings: Settings,
    ibkr_profile_override: str | None = None,
    ibkr_client_id_offset: int = 0,
) -> BacktestDataService:
    profile_name, ibkr_profile = settings.ibkr.resolve_profile(ibkr_profile_override)
    if ibkr_client_id_offset:
        ibkr_profile = replace(ibkr_profile, client_id=ibkr_profile.client_id + ibkr_client_id_offset)
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


def build_backtest_chain_validation_service(
    settings: Settings,
    ibkr_profile_override: str | None = None,
    output_root: str | Path | None = None,
) -> BacktestChainValidationService:
    backtest_data_service = build_backtest_data_service(
        settings,
        ibkr_profile_override=ibkr_profile_override,
        ibkr_client_id_offset=90,
    )
    repository = SqliteMarketDataRepository(settings.data.market_data_db)
    option_gateways = {
        "ibkr": backtest_data_service.ibkr_gateway,
        "moomoo": backtest_data_service.moomoo_gateway,
    }
    return BacktestChainValidationService(
        repository=repository,
        backtest_data_service=backtest_data_service,
        option_gateways={name: gateway for name, gateway in option_gateways.items() if gateway is not None},
        selector=SymbolSelector(settings.selection),
        confirmation_bars=settings.strategy.tracking_confirmation_bars,
        limit_price_factor=settings.strategy.tracking_limit_price_factor,
        execution_planner=ExecutionPlanner(),
        output_root=Path(output_root) if output_root is not None else Path("artifacts/backtest_chain_validation"),
    )
