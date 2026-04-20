"""Unified bar data service.

Single entry point for fetching OHLCV bar data for any number of symbols and
date ranges, for both live trading and backtesting.

Flow per (symbol, trade_date):
1. Check daily_coverage — if is_complete, load bars from DB and return.
2. Determine source order: today → live_source_order; past → history_source_order.
3. Attempt fetch from each source in order; persist first successful result.
4. Update daily_coverage (is_complete=True when bars >= expected OR all sources empty).
5. Return bars.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from intraday_auto_trading.gateways.yfinance_market_data import YfinanceMarketDataGateway
from intraday_auto_trading.interfaces.brokers import MarketDataGateway
from intraday_auto_trading.interfaces.repositories import MarketDataRepository
from intraday_auto_trading.models import CapabilityStatus, DailyCoverage, MinuteBar, SymbolInfo
from intraday_auto_trading.services.data_fetch_policy import DataFetchPolicy


_SESSION_OPEN = time(9, 30)
_SESSION_CLOSE = time(16, 0)


def _date_range(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _expected_bars(bar_size: str) -> int:
    return {"1m": 390, "15m": 26, "1d": 1}.get(bar_size, 1)


def _day_window(trade_date: date, bar_size: str, tz: str) -> tuple[datetime, datetime]:
    """Return (start, end) datetimes (timezone-aware, in given tz) for one trading day."""
    zone = ZoneInfo(tz)
    if bar_size == "1d":
        start = datetime(trade_date.year, trade_date.month, trade_date.day, 0, 0, tzinfo=zone)
        end = datetime(trade_date.year, trade_date.month, trade_date.day, 23, 59, tzinfo=zone)
    else:
        start = datetime.combine(trade_date, _SESSION_OPEN, tzinfo=zone)
        end = datetime.combine(trade_date, _SESSION_CLOSE, tzinfo=zone)
    return start, end


@dataclass
class BarDataService:
    """Unified bar data service for live trading and backtesting."""

    repository: MarketDataRepository
    policy: DataFetchPolicy
    ibkr_gateway: MarketDataGateway | None
    moomoo_gateway: MarketDataGateway | None
    yfinance_gateway: YfinanceMarketDataGateway
    exchange_timezone: str = "America/New_York"

    def get_bars(
        self,
        symbols: list[str],
        bar_size: str,
        start_date: date,
        end_date: date,
    ) -> dict[str, list[MinuteBar]]:
        """Fetch bars for all symbols across [start_date, end_date] (inclusive).

        Returns a dict mapping symbol → list of MinuteBar sorted by timestamp.
        Bar timestamps are naive datetimes in the exchange timezone.
        """
        trading_days = [d for d in _date_range(start_date, end_date) if d.weekday() < 5]
        expected = _expected_bars(bar_size)
        today_et = datetime.now(ZoneInfo(self.exchange_timezone)).date()

        coverage_map = self.repository.load_daily_coverage_range(
            symbols, bar_size,
            start_date.isoformat(), end_date.isoformat(),
        )

        result: dict[str, list[MinuteBar]] = {s: [] for s in symbols}

        for trade_date in trading_days:
            date_str = trade_date.isoformat()
            day_start, day_end = _day_window(trade_date, bar_size, self.exchange_timezone)
            # Convert to naive UTC for DB queries (existing bars stored as UTC ISO strings)
            day_start_utc = day_start.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
            day_end_utc = day_end.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)

            for symbol in symbols:
                cov = coverage_map.get((symbol, date_str))
                if cov and cov.is_complete:
                    bars = self._load_db(symbol, bar_size, day_start_utc, day_end_utc)
                    result[symbol].extend(bars)
                    continue

                source_order = (
                    self.policy.live_source_order
                    if trade_date >= today_et
                    else self.policy.history_source_order
                )

                bars, source = self._fetch_and_save(
                    symbol, bar_size, day_start_utc, day_end_utc, source_order
                )

                # Mark complete when we have enough bars OR all sources returned nothing
                is_complete = len(bars) >= expected or source == "none"
                self.repository.save_daily_coverage(DailyCoverage(
                    symbol=symbol,
                    bar_size=bar_size,
                    trade_date=date_str,
                    source=source,
                    expected_bars=expected,
                    actual_bars=len(bars),
                    is_complete=is_complete,
                ))
                result[symbol].extend(bars)

        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load_db(
        self, symbol: str, bar_size: str, start: datetime, end: datetime
    ) -> list[MinuteBar]:
        bars, _ = self.repository.load_price_bars_with_source_priority(
            symbol, bar_size, start, end, self.policy.db_source_priority
        )
        return bars

    def _fetch_and_save(
        self,
        symbol: str,
        bar_size: str,
        start: datetime,
        end: datetime,
        source_order: list[str],
    ) -> tuple[list[MinuteBar], str]:
        gateways: dict[str, MarketDataGateway] = {}
        if self.ibkr_gateway is not None:
            gateways["ibkr"] = self.ibkr_gateway
        if self.moomoo_gateway is not None:
            gateways["moomoo"] = self.moomoo_gateway

        for source_name in source_order:
            bars: list[MinuteBar] = []

            if source_name == "yfinance":
                bars = self._fetch_from_yfinance(symbol, bar_size, start, end)
            elif source_name in gateways:
                bars = self._fetch_from_gateway(gateways[source_name], symbol, bar_size, start, end)
            else:
                continue

            if bars:
                self.repository.upsert_symbol(SymbolInfo(symbol=symbol))
                self.repository.save_price_bars(symbol, bar_size, bars, source_name)
                return bars, source_name

        return [], "none"

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

        try:
            if bar_size == "1m":
                if caps.bars_1m.status != CapabilityStatus.AVAILABLE:
                    return []
                if hasattr(gateway, "get_minute_bars_batch"):
                    return gateway.get_minute_bars_batch([symbol], start, end).get(symbol, [])  # type: ignore[attr-defined]
                return gateway.get_minute_bars(symbol, start, end)
            elif bar_size == "15m":
                if caps.bars_15m_direct.status != CapabilityStatus.AVAILABLE:
                    return []
                if hasattr(gateway, "get_direct_fifteen_minute_bars_batch"):
                    return gateway.get_direct_fifteen_minute_bars_batch([symbol], start, end).get(symbol, [])  # type: ignore[attr-defined]
                return gateway.get_direct_fifteen_minute_bars(symbol, start, end)
        except Exception:
            return []
        return []

    def _fetch_from_yfinance(
        self, symbol: str, bar_size: str, start: datetime, end: datetime
    ) -> list[MinuteBar]:
        try:
            if bar_size == "1m":
                return self.yfinance_gateway.get_minute_bars(symbol, start, end)
            elif bar_size == "15m":
                return self.yfinance_gateway.get_direct_fifteen_minute_bars(symbol, start, end)
        except Exception:
            pass
        return []
