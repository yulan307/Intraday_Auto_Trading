from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from intraday_auto_trading.gateways.yfinance_market_data import YfinanceMarketDataGateway
from intraday_auto_trading.interfaces.brokers import MarketDataGateway
from intraday_auto_trading.interfaces.repositories import MarketDataRepository
from intraday_auto_trading.models import CapabilityStatus, MinuteBar, SymbolInfo


@dataclass
class FetchResult:
    symbol: str
    bar_size: str
    source: str  # "db:<source>" / "ibkr" / "moomoo" / "yfinance" / "none"
    bar_count: int
    message: str = ""


@dataclass
class BacktestDataService:
    repository: MarketDataRepository
    yfinance_gateway: YfinanceMarketDataGateway
    ibkr_gateway: MarketDataGateway | None = None
    moomoo_gateway: MarketDataGateway | None = None
    source_priority: list[str] = field(default_factory=lambda: ["ibkr", "moomoo", "yfinance"])

    def get_bars(
        self,
        symbols: list[str],
        bar_size: str,
        start: datetime,
        end: datetime,
    ) -> list[FetchResult]:
        """Fetch bars for the given symbols.

        For each symbol:
        1. Try DB first (with source priority deduplication).
        2. If missing, try live gateways in priority order (ibkr → moomoo).
        3. Fall back to yfinance.
        Fetched data is persisted to DB so subsequent calls use the cache.
        """
        results: list[FetchResult] = []
        for symbol in symbols:
            result = self._fetch_one(symbol, bar_size, start, end)
            results.append(result)
        return results

    def _fetch_one(self, symbol: str, bar_size: str, start: datetime, end: datetime) -> FetchResult:
        # Step 1: try DB
        bars, winning_source = self.repository.load_price_bars_with_source_priority(
            symbol, bar_size, start, end, self.source_priority
        )
        if bars:
            return FetchResult(
                symbol=symbol,
                bar_size=bar_size,
                source=f"db:{winning_source}",
                bar_count=len(bars),
            )

        # Step 2: try ibkr and moomoo (in source_priority order)
        live_gateways: dict[str, MarketDataGateway] = {}
        if self.ibkr_gateway is not None:
            live_gateways["ibkr"] = self.ibkr_gateway
        if self.moomoo_gateway is not None:
            live_gateways["moomoo"] = self.moomoo_gateway

        for source_name in self.source_priority:
            if source_name not in live_gateways:
                continue
            gateway = live_gateways[source_name]
            bars = self._fetch_from_gateway(gateway, symbol, bar_size, start, end)
            if bars:
                self.repository.upsert_symbol(SymbolInfo(symbol=symbol))
                self.repository.save_price_bars(symbol, bar_size, bars, source_name)
                return FetchResult(
                    symbol=symbol,
                    bar_size=bar_size,
                    source=source_name,
                    bar_count=len(bars),
                    message="written to db",
                )

        # Step 3: yfinance fallback
        bars = self._fetch_from_yfinance(symbol, bar_size, start, end)
        if bars:
            self.repository.upsert_symbol(SymbolInfo(symbol=symbol))
            self.repository.save_price_bars(symbol, bar_size, bars, "yfinance")
            return FetchResult(
                symbol=symbol,
                bar_size=bar_size,
                source="yfinance",
                bar_count=len(bars),
                message="written to db",
            )

        return FetchResult(symbol=symbol, bar_size=bar_size, source="none", bar_count=0)

    def _fetch_from_gateway(
        self,
        gateway: MarketDataGateway,
        symbol: str,
        bar_size: str,
        start: datetime,
        end: datetime,
    ) -> list[MinuteBar]:
        try:
            caps = gateway.probe_capabilities()
        except Exception:
            return []

        if bar_size == "1m":
            cap_status = caps.bars_1m.status
            if cap_status != CapabilityStatus.AVAILABLE:
                return []
            try:
                if hasattr(gateway, "get_minute_bars_batch"):
                    return gateway.get_minute_bars_batch([symbol], start, end).get(symbol, [])  # type: ignore[attr-defined]
                return gateway.get_minute_bars(symbol, start, end)
            except Exception:
                return []
        elif bar_size == "15m":
            cap_status = caps.bars_15m_direct.status
            if cap_status != CapabilityStatus.AVAILABLE:
                return []
            try:
                if hasattr(gateway, "get_direct_fifteen_minute_bars_batch"):
                    return gateway.get_direct_fifteen_minute_bars_batch([symbol], start, end).get(symbol, [])  # type: ignore[attr-defined]
                return gateway.get_direct_fifteen_minute_bars(symbol, start, end)
            except Exception:
                return []
        return []

    def _fetch_from_yfinance(self, symbol: str, bar_size: str, start: datetime, end: datetime) -> list[MinuteBar]:
        try:
            if bar_size == "1m":
                return self.yfinance_gateway.get_minute_bars(symbol, start, end)
            elif bar_size == "15m":
                return self.yfinance_gateway.get_direct_fifteen_minute_bars(symbol, start, end)
        except Exception:
            pass
        return []
