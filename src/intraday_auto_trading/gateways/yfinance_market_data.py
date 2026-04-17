from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol, Sequence

from intraday_auto_trading.models import (
    CapabilityStatus,
    MarketDataType,
    MinuteBar,
    OpeningImbalance,
    OptionQuote,
    ProviderCapabilities,
    ProviderCapability,
    SessionMetrics,
)


class YfinanceBackend(Protocol):
    def fetch_bars(
        self,
        symbols: Sequence[str],
        interval: str,
        start: datetime,
        end: datetime,
    ) -> dict[str, list[MinuteBar]]: ...


@dataclass
class RealYfinanceBackend:
    request_timeout_seconds: int = 30

    def fetch_bars(
        self,
        symbols: Sequence[str],
        interval: str,
        start: datetime,
        end: datetime,
    ) -> dict[str, list[MinuteBar]]:
        import yfinance as yf  # noqa: PLC0415

        tickers = list(symbols)
        df = yf.download(
            tickers=tickers,
            interval=interval,
            start=start,
            end=end,
            progress=False,
            timeout=self.request_timeout_seconds,
        )

        if df.empty:
            return {}

        result: dict[str, list[MinuteBar]] = {}

        if getattr(df.columns, "nlevels", 1) > 1:
            for symbol in tickers:
                try:
                    sub = df.xs(symbol, axis=1, level=1)
                except KeyError:
                    continue
                bars = _parse_flat_df(sub, symbol)
                if bars:
                    result[symbol] = bars
        else:
            symbol = tickers[0]
            bars = _parse_flat_df(df, symbol)
            if bars:
                result[symbol] = bars

        return result


def _parse_flat_df(df, symbol: str) -> list[MinuteBar]:
    bars: list[MinuteBar] = []
    for ts, row in df.iterrows():
        try:
            open_ = float(row["Open"])
            high = float(row["High"])
            low = float(row["Low"])
            close = float(row["Close"])
            volume = float(row["Volume"])
        except (KeyError, TypeError, ValueError):
            continue
        if any(v != v for v in (open_, high, low, close)):  # NaN check
            continue
        timestamp = ts.to_pydatetime().replace(tzinfo=None)
        bars.append(MinuteBar(timestamp=timestamp, open=open_, high=high, low=low, close=close, volume=volume))
    return bars


@dataclass
class YfinanceMarketDataGateway:
    provider_name: str = "yfinance"
    backend: YfinanceBackend | None = None

    def probe_capabilities(self) -> ProviderCapabilities:
        yfinance_available = importlib.util.find_spec("yfinance") is not None
        if not yfinance_available or self.backend is None:
            status = CapabilityStatus.UNAVAILABLE
            msg = "yfinance not installed" if not yfinance_available else "no backend"
        else:
            status = CapabilityStatus.AVAILABLE
            msg = ""

        return ProviderCapabilities(
            provider=self.provider_name,
            bars_1m=ProviderCapability(MarketDataType.BARS_1M, status, msg if status != CapabilityStatus.AVAILABLE else "max 7 days"),
            bars_15m_direct=ProviderCapability(MarketDataType.BARS_15M_DIRECT, status, msg if status != CapabilityStatus.AVAILABLE else "max 60 days"),
            bars_15m_derived=ProviderCapability(MarketDataType.BARS_15M_DERIVED, status, msg if status != CapabilityStatus.AVAILABLE else "max 7 days via 1m"),
            opening_imbalance=ProviderCapability(MarketDataType.OPENING_IMBALANCE, CapabilityStatus.UNSUPPORTED),
            options=ProviderCapability(MarketDataType.OPTIONS, CapabilityStatus.UNSUPPORTED),
        )

    # --- batch methods ---

    def get_minute_bars_batch(
        self,
        symbols: Sequence[str],
        start: datetime,
        end: datetime,
    ) -> dict[str, list[MinuteBar]]:
        if self.backend is None:
            return {}
        return self.backend.fetch_bars(symbols, "1m", start, end)

    def get_direct_fifteen_minute_bars_batch(
        self,
        symbols: Sequence[str],
        start: datetime,
        end: datetime,
    ) -> dict[str, list[MinuteBar]]:
        if self.backend is None:
            return {}
        return self.backend.fetch_bars(symbols, "15m", start, end)

    def get_option_quotes_batch(
        self,
        symbols: Sequence[str],
        at_time: datetime,
    ) -> dict[str, list[OptionQuote]]:
        return {}

    # --- single-symbol methods (delegate to batch) ---

    def get_minute_bars(self, symbol: str, start: datetime, end: datetime) -> list[MinuteBar]:
        return self.get_minute_bars_batch([symbol], start, end).get(symbol, [])

    def get_direct_fifteen_minute_bars(self, symbol: str, start: datetime, end: datetime) -> list[MinuteBar]:
        return self.get_direct_fifteen_minute_bars_batch([symbol], start, end).get(symbol, [])

    def get_option_quotes(self, symbol: str, at_time: datetime) -> list[OptionQuote]:
        return []

    def get_opening_imbalance(self, symbol: str, trade_date: date) -> OpeningImbalance | None:
        return None

    def get_session_metrics(self, symbol: str, at_time: datetime) -> SessionMetrics | None:
        return None

    def get_official_open(self, symbol: str, at_time: datetime) -> float:
        raise NotImplementedError("yfinance gateway does not support get_official_open")

    def get_last_price(self, symbol: str, at_time: datetime) -> float:
        raise NotImplementedError("yfinance gateway does not support get_last_price")

    def get_session_vwap(self, symbol: str, at_time: datetime) -> float:
        raise NotImplementedError("yfinance gateway does not support get_session_vwap")
