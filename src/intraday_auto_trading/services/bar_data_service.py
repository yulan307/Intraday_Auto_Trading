"""Unified bar data service.

Single entry point for fetching OHLCV bar data for any number of symbols and
date ranges, for both live trading and backtesting.

Flow per (trade_date):
1. Load bar_request_log for all symbols at once.
2. Symbols with is_complete → load bars from DB directly.
3. Remaining symbols → batch-fetch per source in source_order:
   - One gateway call per source covers ALL pending symbols for that day.
   - Stop per symbol once expected_bars reached; stop globally when all done.
4. Persist bars and update bar_request_log per symbol.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from intraday_auto_trading.gateways.yfinance_market_data import YfinanceMarketDataGateway
from intraday_auto_trading.interfaces.brokers import MarketDataGateway
from intraday_auto_trading.interfaces.repositories import MarketDataRepository
from intraday_auto_trading.models import BarRequestLog, CapabilityStatus, MinuteBar, SymbolInfo
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


@dataclass(slots=True)
class _FetchOutcome:
    bars: list[MinuteBar]
    source: str
    error_message: str | None = None


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
        source_order: list[str] | None = None,
        force_refresh: bool = False,
    ) -> dict[str, list[MinuteBar]]:
        """Fetch bars for all symbols across [start_date, end_date] (inclusive).

        Returns a dict mapping symbol → list of MinuteBar sorted by timestamp.
        Bar timestamps are naive datetimes in the exchange timezone.

        For each trading day, all symbols that need fetching are batched into a
        single gateway call per source, reducing API round-trips.
        """
        trading_days = [d for d in _date_range(start_date, end_date) if d.weekday() < 5]
        expected = _expected_bars(bar_size)
        today_et = datetime.now(ZoneInfo(self.exchange_timezone)).date()

        request_log_map = self.repository.load_bar_request_log_range(
            symbols, bar_size,
            start_date.isoformat(), end_date.isoformat(),
        )

        result: dict[str, list[MinuteBar]] = {s: [] for s in symbols}

        for trade_date in trading_days:
            date_str = trade_date.isoformat()
            day_start, day_end = _day_window(trade_date, bar_size, self.exchange_timezone)
            day_start_utc = day_start.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
            day_end_utc = day_end.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)

            # Partition symbols: terminal request log vs needs-fetch
            complete: list[str] = []
            confirmed_empty: list[str] = []
            pending: list[str] = []
            for symbol in symbols:
                log = request_log_map.get((symbol, date_str))
                if force_refresh:
                    pending.append(symbol)
                elif log and log.status == "success":
                    complete.append(symbol)
                elif log and log.status == "no_data":
                    confirmed_empty.append(symbol)
                else:
                    pending.append(symbol)

            # Load complete symbols from DB
            for symbol in complete:
                bars = self._load_db(symbol, bar_size, day_start_utc, day_end_utc)
                result[symbol].extend(bars)

            for symbol in confirmed_empty:
                result[symbol].extend([])

            if not pending:
                continue

            active_source_order = source_order or (
                self.policy.live_source_order
                if trade_date >= today_et
                else self.policy.history_source_order
            )

            # Batch-fetch all pending symbols; returns one outcome per symbol.
            fetched = self._fetch_and_save_batch(
                pending, bar_size, day_start_utc, day_end_utc,
                active_source_order, expected_bars=expected,
            )

            for symbol in pending:
                outcome = fetched.get(symbol, _FetchOutcome([], "none"))
                bars = outcome.bars
                if outcome.error_message:
                    status = "failed"
                    message = outcome.error_message
                elif len(bars) >= expected:
                    status = "success"
                    message = None
                elif bars:
                    status = "partial"
                    message = f"Fetched {len(bars)} of expected {expected} bars."
                else:
                    status = "no_data"
                    message = "No bars returned from any configured source."
                self.repository.save_bar_request_log(BarRequestLog(
                    symbol=symbol,
                    bar_size=bar_size,
                    trade_date=date_str,
                    source=outcome.source,
                    request_start_ts=day_start_utc,
                    request_end_ts=day_end_utc,
                    status=status,
                    expected_bars=expected,
                    actual_bars=len(bars),
                    message=message,
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

    def _fetch_and_save_batch(
        self,
        symbols: list[str],
        bar_size: str,
        start: datetime,
        end: datetime,
        source_order: list[str],
        expected_bars: int = 0,
    ) -> dict[str, _FetchOutcome]:
        """Batch-fetch bars for multiple symbols, trying sources in order.

        For each source, collects all symbols still below expected_bars and
        issues ONE gateway call covering all of them. Persists results immediately.

        Returns dict[symbol] → (best_bars, winning_source).
        """
        gateways: dict[str, MarketDataGateway] = {}
        if self.ibkr_gateway is not None:
            gateways["ibkr"] = self.ibkr_gateway
        if self.moomoo_gateway is not None:
            gateways["moomoo"] = self.moomoo_gateway

        # Track best result per symbol.
        best: dict[str, _FetchOutcome] = {s: _FetchOutcome([], "none") for s in symbols}
        errors: dict[str, list[str]] = {s: [] for s in symbols}

        for source_name in source_order:
            # Only fetch symbols that haven't reached expected_bars yet
            pending = [s for s in symbols if len(best[s].bars) < expected_bars]
            if not pending:
                break

            if source_name == "yfinance":
                fetched, source_errors = self._batch_from_yfinance(pending, bar_size, start, end)
            elif source_name in gateways:
                fetched, source_errors = self._batch_from_gateway(
                    gateways[source_name], pending, bar_size, start, end
                )
            else:
                continue

            for symbol, message in source_errors.items():
                if message:
                    errors.setdefault(symbol, []).append(f"{source_name}: {message}")

            for symbol, bars in fetched.items():
                if not bars:
                    continue
                self.repository.upsert_symbol(SymbolInfo(symbol=symbol))
                self.repository.save_price_bars(symbol, bar_size, bars, source_name)
                if len(bars) > len(best[symbol].bars):
                    best[symbol] = _FetchOutcome(bars, source_name)

        for symbol, messages in errors.items():
            if not best[symbol].bars and messages:
                best[symbol] = _FetchOutcome([], "none", "; ".join(messages))

        return best

    def _batch_from_gateway(
        self,
        gateway: MarketDataGateway,
        symbols: list[str],
        bar_size: str,
        start: datetime,
        end: datetime,
    ) -> tuple[dict[str, list[MinuteBar]], dict[str, str]]:
        """Call the gateway's bar API and return bars plus per-symbol errors."""
        try:
            caps = gateway.probe_capabilities()
        except Exception as exc:
            return {}, {symbol: f"Capability probe failed: {exc}" for symbol in symbols}

        try:
            if bar_size == "1m":
                if caps.bars_1m.status != CapabilityStatus.AVAILABLE:
                    return {}, {
                        symbol: caps.bars_1m.message or f"{gateway.provider_name} 1m bars unavailable."
                        for symbol in symbols
                    }
                if hasattr(gateway, "get_minute_bars_batch"):
                    return gateway.get_minute_bars_batch(symbols, start, end), {}  # type: ignore[attr-defined]
                return self._per_symbol_from_gateway(gateway, symbols, "get_minute_bars", start, end)
            if bar_size == "15m":
                if caps.bars_15m_direct.status != CapabilityStatus.AVAILABLE:
                    return {}, {
                        symbol: caps.bars_15m_direct.message or f"{gateway.provider_name} 15m bars unavailable."
                        for symbol in symbols
                    }
                if hasattr(gateway, "get_direct_fifteen_minute_bars_batch"):
                    return gateway.get_direct_fifteen_minute_bars_batch(symbols, start, end), {}  # type: ignore[attr-defined]
                return self._per_symbol_from_gateway(
                    gateway, symbols, "get_direct_fifteen_minute_bars", start, end
                )
            if bar_size == "1d":
                if hasattr(gateway, "get_daily_bars_batch"):
                    return gateway.get_daily_bars_batch(symbols, start, end), {}  # type: ignore[attr-defined]
                if hasattr(gateway, "get_daily_bars"):
                    return self._per_symbol_from_gateway(gateway, symbols, "get_daily_bars", start, end)
                return {}, {
                    symbol: f"{gateway.provider_name} does not implement daily bars."
                    for symbol in symbols
                }
        except Exception as exc:
            return self._fallback_per_symbol_from_gateway(gateway, symbols, bar_size, start, end, exc)
        return {}, {}

    def _fallback_per_symbol_from_gateway(
        self,
        gateway: MarketDataGateway,
        symbols: list[str],
        bar_size: str,
        start: datetime,
        end: datetime,
        batch_exception: Exception,
    ) -> tuple[dict[str, list[MinuteBar]], dict[str, str]]:
        method_by_bar_size = {
            "1m": "get_minute_bars",
            "15m": "get_direct_fifteen_minute_bars",
            "1d": "get_daily_bars",
        }
        method_name = method_by_bar_size.get(bar_size)
        if method_name is None or not hasattr(gateway, method_name):
            return {}, {symbol: str(batch_exception) for symbol in symbols}
        fetched, errors = self._per_symbol_from_gateway(gateway, symbols, method_name, start, end)
        for symbol in symbols:
            if symbol not in fetched and symbol not in errors:
                errors[symbol] = str(batch_exception)
        return fetched, errors

    @staticmethod
    def _per_symbol_from_gateway(
        gateway: MarketDataGateway,
        symbols: list[str],
        method_name: str,
        start: datetime,
        end: datetime,
    ) -> tuple[dict[str, list[MinuteBar]], dict[str, str]]:
        fetched: dict[str, list[MinuteBar]] = {}
        errors: dict[str, str] = {}
        method = getattr(gateway, method_name)
        for symbol in symbols:
            try:
                fetched[symbol] = method(symbol, start, end)
            except Exception as exc:
                errors[symbol] = str(exc)
        return fetched, errors

    def _batch_from_yfinance(
        self,
        symbols: list[str],
        bar_size: str,
        start: datetime,
        end: datetime,
    ) -> tuple[dict[str, list[MinuteBar]], dict[str, str]]:
        """Call yfinance batch API — one HTTP request for all symbols."""
        try:
            if bar_size == "1m":
                return self.yfinance_gateway.get_minute_bars_batch(symbols, start, end), {}
            elif bar_size == "15m":
                return self.yfinance_gateway.get_direct_fifteen_minute_bars_batch(symbols, start, end), {}
            elif bar_size == "1d":
                return self.yfinance_gateway.get_daily_bars_batch(symbols, start, end), {}
        except Exception as exc:
            return {}, {symbol: str(exc) for symbol in symbols}
        return {}, {}
