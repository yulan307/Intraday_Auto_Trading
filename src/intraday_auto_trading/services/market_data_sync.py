from __future__ import annotations

from datetime import date, datetime
from typing import Mapping, Sequence, cast

from intraday_auto_trading.interfaces.brokers import BatchMarketDataGateway, MarketDataGateway
from intraday_auto_trading.interfaces.repositories import MarketDataRepository
from intraday_auto_trading.models import (
    CapabilityStatus,
    MarketDataType,
    MinuteBar,
    ProviderCapabilities,
    SessionMetrics,
    SyncResult,
    SyncStatus,
    SyncSummary,
    SymbolInfo,
)


class MarketDataSyncService:
    def __init__(
        self,
        repository: MarketDataRepository,
        providers: Mapping[str, MarketDataGateway],
        enabled_data_types: Sequence[str],
        enable_direct_15m: bool,
        enable_derived_15m: bool,
    ) -> None:
        self.repository = repository
        self.providers = dict(providers)
        self.enabled_data_types = {data_type.lower() for data_type in enabled_data_types}
        self.enable_direct_15m = enable_direct_15m
        self.enable_derived_15m = enable_derived_15m

    def sync_market_data(
        self,
        symbols: Sequence[str],
        providers: Sequence[str],
        start: datetime,
        end: datetime,
    ) -> SyncSummary:
        normalized_symbols = [symbol.upper() for symbol in symbols]
        capability_reports: list[ProviderCapabilities] = []
        results: list[SyncResult] = []

        for provider_name in providers:
            gateway = self.providers.get(provider_name.lower())
            if gateway is None:
                for symbol in normalized_symbols:
                    results.append(
                        SyncResult(
                            provider=provider_name.lower(),
                            symbol=symbol,
                            data_type=MarketDataType.BARS,
                            status=SyncStatus.FAILED,
                            message="Provider is not configured.",
                        )
                    )
                continue

            capabilities = gateway.probe_capabilities()
            capability_reports.append(capabilities)

            if "bars" in self.enabled_data_types:
                results.extend(self._sync_bars(gateway, capabilities, normalized_symbols, start, end))
            if "opening_imbalance" in self.enabled_data_types:
                results.extend(self._sync_opening_imbalance(gateway, capabilities, normalized_symbols, end.date()))
            if "options" in self.enabled_data_types:
                results.extend(self._sync_option_quotes(gateway, capabilities, normalized_symbols, end))

        return SyncSummary(provider_capabilities=capability_reports, results=results)

    def _sync_bars(
        self,
        gateway: MarketDataGateway,
        capabilities: ProviderCapabilities,
        symbols: Sequence[str],
        start: datetime,
        end: datetime,
    ) -> list[SyncResult]:
        results: list[SyncResult] = []
        minute_capability = capabilities.bars_1m
        if minute_capability.status is not CapabilityStatus.AVAILABLE:
            return [
                SyncResult(
                    provider=gateway.provider_name,
                    symbol=symbol,
                    data_type=MarketDataType.BARS_1M,
                    status=_sync_status_for(minute_capability.status),
                    message=minute_capability.message,
                )
                for symbol in symbols
            ]

        try:
            minute_bars_by_symbol = self._fetch_minute_bars(gateway, symbols, start, end)
        except Exception as exc:
            return [
                SyncResult(
                    provider=gateway.provider_name,
                    symbol=symbol,
                    data_type=MarketDataType.BARS_1M,
                    status=SyncStatus.FAILED,
                    message=str(exc),
                )
                for symbol in symbols
            ]

        for symbol in symbols:
            bars = minute_bars_by_symbol.get(symbol, [])
            if not bars:
                results.append(
                    SyncResult(
                        provider=gateway.provider_name,
                        symbol=symbol,
                        data_type=MarketDataType.BARS_1M,
                        status=SyncStatus.FAILED,
                        message="No 1m bars returned.",
                    )
                )
                continue

            self.repository.upsert_symbol(SymbolInfo(symbol=symbol))
            self.repository.save_price_bars(symbol, "1m", bars, source=gateway.provider_name)
            results.append(
                SyncResult(
                    provider=gateway.provider_name,
                    symbol=symbol,
                    data_type=MarketDataType.BARS_1M,
                    status=SyncStatus.SUCCESS,
                    saved_row_count=len(bars),
                    message="Saved 1m bars.",
                )
            )

        return results

    def _sync_direct_fifteen_minute_bars(
        self,
        gateway: MarketDataGateway,
        capabilities: ProviderCapabilities,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> SyncResult:
        direct_capability = capabilities.bars_15m_direct
        if direct_capability.status is not CapabilityStatus.AVAILABLE:
            return SyncResult(
                provider=gateway.provider_name,
                symbol=symbol,
                data_type=MarketDataType.BARS_15M_DIRECT,
                status=_sync_status_for(direct_capability.status),
                message=direct_capability.message,
            )

        try:
            bars = gateway.get_direct_fifteen_minute_bars(symbol, start, end)
        except Exception as exc:
            return SyncResult(
                provider=gateway.provider_name,
                symbol=symbol,
                data_type=MarketDataType.BARS_15M_DIRECT,
                status=SyncStatus.FAILED,
                message=str(exc),
            )
        if not bars:
            return SyncResult(
                provider=gateway.provider_name,
                symbol=symbol,
                data_type=MarketDataType.BARS_15M_DIRECT,
                status=SyncStatus.SKIPPED,
                message="Provider returned no direct 15m bars.",
            )

        self.repository.save_price_bars(symbol, "15m", bars, source=f"{gateway.provider_name}_direct")
        return SyncResult(
            provider=gateway.provider_name,
            symbol=symbol,
            data_type=MarketDataType.BARS_15M_DIRECT,
            status=SyncStatus.SUCCESS,
            saved_row_count=len(bars),
            message="Saved provider direct 15m bars.",
        )

    def _sync_opening_imbalance(
        self,
        gateway: MarketDataGateway,
        capabilities: ProviderCapabilities,
        symbols: Sequence[str],
        trade_date: date,
    ) -> list[SyncResult]:
        return [
            SyncResult(
                provider=gateway.provider_name,
                symbol=symbol,
                data_type=MarketDataType.OPENING_IMBALANCE,
                status=SyncStatus.UNSUPPORTED,
                message="Opening imbalance is not stored in the bar-only market-data schema.",
            )
            for symbol in symbols
        ]

    def _sync_option_quotes(
        self,
        gateway: MarketDataGateway,
        capabilities: ProviderCapabilities,
        symbols: Sequence[str],
        at_time: datetime,
    ) -> list[SyncResult]:
        return [
            SyncResult(
                provider=gateway.provider_name,
                symbol=symbol,
                data_type=MarketDataType.OPTIONS,
                status=SyncStatus.UNSUPPORTED,
                message="Options are not stored in the bar-only market-data schema.",
            )
            for symbol in symbols
        ]

    def _fetch_minute_bars(
        self,
        gateway: MarketDataGateway,
        symbols: Sequence[str],
        start: datetime,
        end: datetime,
    ) -> dict[str, list[MinuteBar]]:
        if _supports_batch(gateway):
            return cast(BatchMarketDataGateway, gateway).get_minute_bars_batch(symbols, start, end)
        return {symbol: gateway.get_minute_bars(symbol, start, end) for symbol in symbols}

    def _fetch_option_quotes(
        self,
        gateway: MarketDataGateway,
        symbols: Sequence[str],
        at_time: datetime,
    ) -> dict[str, list]:
        if hasattr(gateway, "get_option_quotes_batch"):
            return cast(BatchMarketDataGateway, gateway).get_option_quotes_batch(symbols, at_time)
        return {symbol: gateway.get_option_quotes(symbol, at_time) for symbol in symbols}

    def _resolve_session_metrics(
        self,
        gateway: MarketDataGateway,
        symbol: str,
        end: datetime,
        bars: Sequence[MinuteBar],
    ) -> SessionMetrics:
        metrics = gateway.get_session_metrics(symbol, end)
        if metrics is not None:
            return metrics

        total_volume = sum(bar.volume for bar in bars)
        session_vwap = bars[-1].close if total_volume <= 0 else sum(bar.close * bar.volume for bar in bars) / total_volume
        return SessionMetrics(
            symbol=symbol,
            timestamp=bars[-1].timestamp,
            source=gateway.provider_name,
            official_open=bars[0].open,
            last_price=bars[-1].close,
            session_vwap=session_vwap,
        )

    def _aggregate_bars(self, bars: Sequence[MinuteBar], bucket_minutes: int) -> list[MinuteBar]:
        aggregated: list[MinuteBar] = []
        current_bucket: list[MinuteBar] = []
        current_bucket_start: datetime | None = None

        for bar in bars:
            bucket_start = bar.timestamp.replace(
                minute=(bar.timestamp.minute // bucket_minutes) * bucket_minutes,
                second=0,
                microsecond=0,
            )
            if current_bucket_start is None:
                current_bucket_start = bucket_start

            if bucket_start != current_bucket_start:
                aggregated.append(self._rollup_bucket(current_bucket_start, current_bucket))
                current_bucket = []
                current_bucket_start = bucket_start

            current_bucket.append(bar)

        if current_bucket_start is not None and current_bucket:
            aggregated.append(self._rollup_bucket(current_bucket_start, current_bucket))

        return aggregated

    @staticmethod
    def _rollup_bucket(bucket_start: datetime, bars: Sequence[MinuteBar]) -> MinuteBar:
        return MinuteBar(
            timestamp=bucket_start,
            open=bars[0].open,
            high=max(bar.high for bar in bars),
            low=min(bar.low for bar in bars),
            close=bars[-1].close,
            volume=sum(bar.volume for bar in bars),
        )


def _supports_batch(gateway: MarketDataGateway) -> bool:
    return hasattr(gateway, "get_minute_bars_batch")


def _sync_status_for(capability_status: CapabilityStatus) -> SyncStatus:
    mapping = {
        CapabilityStatus.AVAILABLE: SyncStatus.SUCCESS,
        CapabilityStatus.UNAVAILABLE: SyncStatus.UNAVAILABLE,
        CapabilityStatus.UNSUPPORTED: SyncStatus.UNSUPPORTED,
        CapabilityStatus.UNTESTED: SyncStatus.SKIPPED,
    }
    return mapping[capability_status]
