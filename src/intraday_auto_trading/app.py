from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path

from intraday_auto_trading.config import Settings
from intraday_auto_trading.gateways.ibkr_market_data import IBKRMarketDataGateway, RealIBKRBackend
from intraday_auto_trading.gateways.moomoo_options import MoomooMarketDataGateway, RealMoomooBackend
from intraday_auto_trading.gateways.yfinance_market_data import RealYfinanceBackend, YfinanceMarketDataGateway
from intraday_auto_trading.models import (
    Dev20SignalResult,
    IntradayOrderDecision,
    OrderInstruction,
    Regime,
    TrendInput,
    TrendSignal,
)
from intraday_auto_trading.persistence.market_data_repository import SqliteMarketDataRepository
from intraday_auto_trading.services.backtest_data_service import BacktestDataService
from intraday_auto_trading.services.bar_data_service import BarDataService
from intraday_auto_trading.services.backtest_chain_validation import BacktestChainValidationService
from intraday_auto_trading.services.data_fetch_policy import DataFetchPolicy, default_policy
from intraday_auto_trading.services.intraday_low_signal import IntradayLowConfig, _compute_ema
from intraday_auto_trading.services.executor import ExecutionPlanner
from intraday_auto_trading.services.market_data_sync import MarketDataSyncService
from intraday_auto_trading.services.selector import SymbolSelector
from intraday_auto_trading.services.symbol_management_data_fetcher import SymbolManagementDataFetcher
from intraday_auto_trading.services.trend_input_loader import TrendInputLoader
from intraday_auto_trading.gateways.virtual_account import VirtualAccount


class TradingWorkflow:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.selector = SymbolSelector()
        self.execution_planner = ExecutionPlanner()

    def classify_and_select_initial(
        self,
        signals: list[TrendSignal],
        trend_inputs: dict[str, TrendInput],
        quantity: int,
        completed_orders: dict[str, int] | None = None,
    ) -> OrderInstruction | None:
        """Phase 1 (classify): place initial order when all symbols are EARLY_BUY + dev20_w < 0.

        Parameters
        ----------
        signals:
            TrendSignal list (one per symbol) from TrendClassifier.
        trend_inputs:
            Mapping of symbol → TrendInput used for classify, needed to compute EMA20 and VWAP.
        quantity:
            Number of shares to order.
        completed_orders:
            Mapping of symbol → completed order count this week (used by decay function).

        Returns
        -------
        OrderInstruction if the condition is met, otherwise None.
        All symbols still enter 1m tracking regardless of the return value.
        """
        if not signals:
            return None
        if not all(s.regime is Regime.EARLY_BUY for s in signals):
            return None

        orders = completed_orders or {}

        # Compute dev20 = (session_vwap - ema20_from_bars) / session_vwap per symbol
        dev20s: dict[str, float] = {}
        for s in signals:
            inp = trend_inputs[s.symbol]
            vwap = inp.session_vwap
            closes = [b.close for b in inp.minute_bars]
            ema20 = _compute_ema(closes, 20)
            dev20s[s.symbol] = (vwap - ema20) / vwap if vwap else 0.0

        # All dev20 must be negative (ema20 > vwap)
        if not all(d < 0 for d in dev20s.values()):
            return None

        # Apply decay weighting; select symbol with highest (least negative) dev20_w
        dev20_ws: dict[str, float] = {
            sym: dev20s[sym] * SymbolSelector._decay_fn(orders.get(sym, 0))
            for sym in dev20s
        }
        best_sym = max(dev20_ws, key=lambda sym: dev20_ws[sym])
        vwap = trend_inputs[best_sym].session_vwap

        return self.execution_planner.build_vwap_early_buy_order(
            symbol=best_sym,
            quantity=quantity,
            vwap=vwap,
            dev20_w=dev20_ws[best_sym],
        )

    def choose_symbol(
        self,
        intraday_signals: dict[str, Dev20SignalResult],
        active_order: tuple[str, float] | None = None,
        completed_orders: dict[str, int] | None = None,
        order_filled: bool = False,
        current_time: datetime | None = None,
        force_buy_time: datetime | None = None,
    ) -> IntradayOrderDecision:
        """Phase 2 (1m tracking): cross-symbol comparison; return next action.

        Parameters
        ----------
        intraday_signals:
            Mapping of symbol → Dev20SignalResult for the current 1m bar.
        active_order:
            (symbol, dev20_w_at_order) of the open order, or None.
        completed_orders:
            Mapping of symbol → completed order count this week.
        order_filled:
            True when the broker confirms the active order is filled → exit tracking.
        current_time:
            Timestamp of the current bar (for force-buy window check).
        force_buy_time:
            Deadline after which force_buy is triggered.
        """
        return self.selector.select(
            intraday_signals,
            active_order=active_order,
            completed_orders=completed_orders,
            order_filled=order_filled,
            current_time=current_time,
            force_buy_time=force_buy_time,
        )


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
            backend=RealMoomooBackend(settings.moomoo, exchange_timezone=settings.project.timezone),
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
        backend=RealMoomooBackend(settings.moomoo, exchange_timezone=settings.project.timezone),
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
        backend=RealMoomooBackend(settings.moomoo, exchange_timezone=settings.project.timezone),
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


def build_bar_data_service(
    settings: Settings,
    ibkr_profile_override: str | None = None,
) -> BarDataService:
    """Build the unified BarDataService with DB-first caching and live/historical routing."""
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
        backend=RealMoomooBackend(settings.moomoo, exchange_timezone=settings.project.timezone),
    )

    yfinance_backend = (
        RealYfinanceBackend(request_timeout_seconds=settings.yfinance.request_timeout_seconds)
        if settings.yfinance.enabled
        else None
    )
    yfinance_gw = YfinanceMarketDataGateway(backend=yfinance_backend)

    return BarDataService(
        repository=repository,
        policy=default_policy(),
        ibkr_gateway=ibkr_gw,
        moomoo_gateway=moomoo_gw,
        yfinance_gateway=yfinance_gw,
        exchange_timezone=settings.project.timezone,
    )


def build_option_gateways(settings: Settings) -> dict:
    """构建支持期权的 gateway（目前只有 Moomoo）。"""
    return {
        "moomoo": MoomooMarketDataGateway(
            settings.moomoo,
            backend=RealMoomooBackend(settings.moomoo, exchange_timezone=settings.project.timezone),
        )
    }


def build_symbol_management_data_fetcher(
    settings: Settings,
    ibkr_profile_override: str | None = None,
) -> SymbolManagementDataFetcher:
    """Build the batch fetcher for every configured symbol-management pool."""
    bar_data_service = build_bar_data_service(
        settings,
        ibkr_profile_override=ibkr_profile_override,
    )
    session_gateways = {
        "ibkr": bar_data_service.ibkr_gateway,
        "moomoo": bar_data_service.moomoo_gateway,
        "yfinance": bar_data_service.yfinance_gateway,
    }
    return SymbolManagementDataFetcher(
        repository=bar_data_service.repository,
        bar_data_service=bar_data_service,
        session_gateways={
            name: gateway for name, gateway in session_gateways.items() if gateway is not None
        },
        option_gateways=build_option_gateways(settings),
        exchange_timezone=settings.project.timezone,
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
    ema_config = IntradayLowConfig(
        ema_fast_span=settings.strategy.ema_fast_span,
        ema10_span=settings.strategy.ema10_span,
        ema_slow_span=settings.strategy.ema_slow_span,
        dev20_window=settings.strategy.dev20_window,
        s_dev20_window=settings.strategy.s_dev20_window,
        valley_window=settings.strategy.valley_window,
    )
    return BacktestChainValidationService(
        repository=repository,
        backtest_data_service=backtest_data_service,
        option_gateways={name: gateway for name, gateway in option_gateways.items() if gateway is not None},
        selector=SymbolSelector(),
        ema_config=ema_config,
        execution_planner=ExecutionPlanner(),
        output_root=Path(output_root) if output_root is not None else Path("artifacts/backtest_chain_validation"),
    )
