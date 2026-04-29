from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
import importlib.util
import socket
from typing import Any, Protocol, Sequence
from zoneinfo import ZoneInfo

from intraday_auto_trading.config import MoomooSettings
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


class MoomooBackend(Protocol):
    def probe(self) -> tuple[CapabilityStatus, str]: ...

    def fetch_minute_bars(
        self,
        symbols: Sequence[str],
        start: datetime,
        end: datetime,
    ) -> dict[str, list[MinuteBar]]: ...

    def fetch_direct_fifteen_minute_bars(
        self,
        symbols: Sequence[str],
        start: datetime,
        end: datetime,
    ) -> dict[str, list[MinuteBar]]: ...

    def fetch_daily_bars(
        self,
        symbols: Sequence[str],
        start: datetime,
        end: datetime,
    ) -> dict[str, list[MinuteBar]]: ...

    def fetch_session_metrics(
        self,
        symbols: Sequence[str],
        at_time: datetime,
    ) -> dict[str, SessionMetrics]: ...

    def fetch_option_quotes(
        self,
        symbols: Sequence[str],
        at_time: datetime,
    ) -> dict[str, list[OptionQuote]]: ...


@dataclass(slots=True)
class RealMoomooBackend:
    settings: MoomooSettings
    snapshot_chunk_size: int = 200
    exchange_timezone: str = "America/New_York"

    def probe(self) -> tuple[CapabilityStatus, str]:
        try:
            with self._open_quote_context() as quote_ctx:
                ret, data = quote_ctx.get_global_state()
        except Exception as exc:  # pragma: no cover - defensive for runtime integrations
            return CapabilityStatus.UNAVAILABLE, f"Failed to connect to OpenD: {exc}"

        if ret != 0:
            return CapabilityStatus.UNAVAILABLE, f"OpenD get_global_state failed: {data}"

        if not data.get("qot_logined", False):
            return CapabilityStatus.UNAVAILABLE, "OpenD is reachable, but quote login is not ready."

        return CapabilityStatus.AVAILABLE, "Using Moomoo OpenD bars and option quotes."

    def fetch_minute_bars(
        self,
        symbols: Sequence[str],
        start: datetime,
        end: datetime,
    ) -> dict[str, list[MinuteBar]]:
        return self._fetch_history_bars(symbols, start, end, ktype_name="K_1M")

    def fetch_direct_fifteen_minute_bars(
        self,
        symbols: Sequence[str],
        start: datetime,
        end: datetime,
    ) -> dict[str, list[MinuteBar]]:
        return self._fetch_history_bars(symbols, start, end, ktype_name="K_15M")

    def fetch_daily_bars(
        self,
        symbols: Sequence[str],
        start: datetime,
        end: datetime,
    ) -> dict[str, list[MinuteBar]]:
        return self._fetch_history_bars(symbols, start, end, ktype_name="K_DAY")

    def fetch_session_metrics(
        self,
        symbols: Sequence[str],
        at_time: datetime,
    ) -> dict[str, SessionMetrics]:
        result: dict[str, SessionMetrics] = {}
        with self._open_quote_context() as quote_ctx:
            ret, state = quote_ctx.get_global_state()
            if ret != 0:
                raise RuntimeError(f"OpenD get_global_state failed: {state}")
            if not state.get("qot_logined", False):
                raise RuntimeError("OpenD quote session is not logged in.")

            normalized_codes = [self._underlying_code(symbol.upper()) for symbol in symbols]
            ret_code, snapshot = quote_ctx.get_market_snapshot(normalized_codes)
            if ret_code != 0:
                raise RuntimeError(f"get_market_snapshot failed: {snapshot}")

            for _, row in snapshot.iterrows():
                symbol = self._extract_symbol(row.get("code"))
                open_price = self._as_float(row.get("open_price"))
                last_price = self._as_float(row.get("last_price"))
                avg_price = self._as_float(row.get("avg_price"))
                turnover = self._as_float(row.get("turnover"))
                volume = self._as_float(row.get("volume"))
                session_vwap = avg_price
                if session_vwap is None and turnover is not None and volume not in (None, 0):
                    session_vwap = turnover / volume
                result[symbol] = SessionMetrics(
                    symbol=symbol,
                    timestamp=at_time,
                    source="moomoo",
                    official_open=open_price,
                    last_price=last_price,
                    session_vwap=session_vwap,
                )
        return result

    def fetch_option_quotes(
        self,
        symbols: Sequence[str],
        at_time: datetime,
    ) -> dict[str, list[OptionQuote]]:
        result: dict[str, list[OptionQuote]] = {}
        with self._open_quote_context() as quote_ctx:
            ret, state = quote_ctx.get_global_state()
            if ret != 0:
                raise RuntimeError(f"OpenD get_global_state failed: {state}")
            if not state.get("qot_logined", False):
                raise RuntimeError("OpenD quote session is not logged in.")

            for symbol in symbols:
                normalized_symbol = symbol.upper()
                option_code = self._underlying_code(normalized_symbol)
                ret_code, chain = quote_ctx.get_option_chain(option_code)
                if ret_code != 0:
                    raise RuntimeError(f"get_option_chain failed for {option_code}: {chain}")

                if getattr(chain, "empty", False):
                    result[normalized_symbol] = []
                    continue

                snapshot_frames: list[Any] = []
                option_codes = chain["code"].dropna().tolist()
                for offset in range(0, len(option_codes), self.snapshot_chunk_size):
                    codes_chunk = option_codes[offset : offset + self.snapshot_chunk_size]
                    ret_code, snapshot = quote_ctx.get_market_snapshot(codes_chunk)
                    if ret_code != 0:
                        raise RuntimeError(f"get_market_snapshot failed for {option_code}: {snapshot}")
                    snapshot_frames.append(snapshot)

                if not snapshot_frames:
                    result[normalized_symbol] = []
                    continue

                dataframe = self._concat_frames(snapshot_frames)
                result[normalized_symbol] = [
                    self._to_option_quote(row, normalized_symbol, at_time)
                    for _, row in dataframe.iterrows()
                ]

        return result

    def _fetch_history_bars(
        self,
        symbols: Sequence[str],
        start: datetime,
        end: datetime,
        *,
        ktype_name: str,
    ) -> dict[str, list[MinuteBar]]:
        result: dict[str, list[MinuteBar]] = {}
        module = _load_moomoo_module()
        ktype = getattr(module.KLType, ktype_name)

        with self._open_quote_context() as quote_ctx:
            ret, state = quote_ctx.get_global_state()
            if ret != 0:
                raise RuntimeError(f"OpenD get_global_state failed: {state}")
            if not state.get("qot_logined", False):
                raise RuntimeError("OpenD quote session is not logged in.")

            for symbol in symbols:
                normalized_symbol = symbol.upper()
                code = self._underlying_code(normalized_symbol)
                ret_code, dataframe, _ = quote_ctx.request_history_kline(
                    code=code,
                    start=start.strftime("%Y-%m-%d"),
                    end=end.strftime("%Y-%m-%d"),
                    ktype=ktype,
                    max_count=1000,
                )
                if ret_code != 0:
                    raise RuntimeError(f"request_history_kline failed for {code}: {dataframe}")
                if getattr(dataframe, "empty", False):
                    result[normalized_symbol] = []
                    continue
                result[normalized_symbol] = self._to_minute_bars(dataframe, normalized_symbol, start, end)
        return result

    @contextmanager
    def _open_quote_context(self):
        module = _load_moomoo_module()
        quote_ctx = module.OpenQuoteContext(host=self.settings.host, port=self.settings.port)
        try:
            yield quote_ctx
        finally:
            quote_ctx.close()

    @staticmethod
    def _concat_frames(frames: Sequence[Any]) -> Any:
        if len(frames) == 1:
            return frames[0]

        import pandas as pd

        return pd.concat(frames, ignore_index=True)

    def _underlying_code(self, symbol: str) -> str:
        if "." in symbol:
            return symbol.upper()
        return f"{self.settings.market.upper()}.{symbol.upper()}"

    @staticmethod
    def _extract_symbol(raw_code: Any) -> str:
        text = RealMoomooBackend._as_text(raw_code) or ""
        return text.split(".")[-1].upper()

    def _to_minute_bars(
        self,
        dataframe: Any,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> list[MinuteBar]:
        bars: list[MinuteBar] = []
        for _, row in dataframe.iterrows():
            timestamp = self._parse_time_key(row.get("time_key"), self.exchange_timezone)
            if timestamp is None or not (start <= timestamp <= end):
                continue
            open_price = self._as_float(row.get("open"))
            high_price = self._as_float(row.get("high"))
            low_price = self._as_float(row.get("low"))
            close_price = self._as_float(row.get("close"))
            volume = self._as_float(row.get("volume"))
            if None in {open_price, high_price, low_price, close_price}:
                continue
            bars.append(
                MinuteBar(
                    timestamp=timestamp,
                    open=float(open_price),
                    high=float(high_price),
                    low=float(low_price),
                    close=float(close_price),
                    volume=float(volume or 0.0),
                )
            )
        bars.sort(key=lambda bar: bar.timestamp)
        return bars

    def _to_option_quote(self, row: Any, symbol: str, fallback_time: datetime) -> OptionQuote:
        contract_id = self._as_text(row.get("code"))
        expiry = self._as_text(row.get("strike_time"))
        exchange = self._as_text(row.get("stock_owner")) or self._underlying_code(symbol)
        snapshot_time = self._parse_snapshot_time(row.get("update_time"), fallback_time)

        return OptionQuote(
            symbol=symbol,
            strike=self._as_float(row.get("option_strike_price")) or self._as_float(row.get("strike_price")) or 0.0,
            side=(self._as_text(row.get("option_type")) or "UNKNOWN").upper(),
            bid=self._as_float(row.get("bid_price")) or 0.0,
            ask=self._as_float(row.get("ask_price")) or 0.0,
            bid_size=self._as_int(row.get("bid_vol")),
            ask_size=self._as_int(row.get("ask_vol")),
            last=self._as_float(row.get("last_price")) or 0.0,
            volume=self._as_int(row.get("volume")),
            iv=self._as_float(row.get("option_implied_volatility")),
            delta=self._as_float(row.get("option_delta")),
            gamma=self._as_float(row.get("option_gamma")),
            contract_id=contract_id,
            expiry=expiry,
            exchange=exchange,
            multiplier=self._as_int(row.get("option_contract_multiplier")) or self._as_int(row.get("option_contract_size")) or None,
            snapshot_time=snapshot_time,
        )

    @staticmethod
    def _parse_snapshot_time(raw_value: Any, fallback_time: datetime) -> datetime:
        raw_text = RealMoomooBackend._as_text(raw_value)
        if not raw_text:
            return fallback_time
        try:
            return datetime.fromisoformat(raw_text)
        except ValueError:
            return fallback_time

    @staticmethod
    def _parse_time_key(raw_value: Any, exchange_timezone: str = "America/New_York") -> datetime | None:
        raw_text = RealMoomooBackend._as_text(raw_value)
        if not raw_text:
            return None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                local_time = datetime.strptime(raw_text, fmt).replace(tzinfo=ZoneInfo(exchange_timezone))
                return local_time.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
            except ValueError:
                continue
        return None

    @staticmethod
    def _as_text(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text or text.upper() == "N/A" or text.lower() == "nan":
            return None
        return text

    @staticmethod
    def _as_float(value: Any) -> float | None:
        if _is_missing(value):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _as_int(value: Any) -> int:
        if _is_missing(value):
            return 0
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0


@dataclass(slots=True)
class MoomooMarketDataGateway:
    settings: MoomooSettings
    backend: MoomooBackend | None = None
    socket_timeout_seconds: float = 0.5

    provider_name: str = "moomoo"

    def probe_capabilities(self) -> ProviderCapabilities:
        base_status, base_message = self._base_status()
        return ProviderCapabilities(
            provider=self.provider_name,
            bars_1m=ProviderCapability(MarketDataType.BARS_1M, base_status, base_message),
            bars_15m_direct=ProviderCapability(MarketDataType.BARS_15M_DIRECT, base_status, base_message),
            bars_15m_derived=ProviderCapability(
                MarketDataType.BARS_15M_DERIVED,
                CapabilityStatus.AVAILABLE if base_status is CapabilityStatus.AVAILABLE else base_status,
                "Derived from Moomoo 1m bars inside the sync service."
                if base_status is CapabilityStatus.AVAILABLE
                else base_message,
            ),
            opening_imbalance=ProviderCapability(
                MarketDataType.OPENING_IMBALANCE,
                CapabilityStatus.UNSUPPORTED,
                "Moomoo OpenD does not expose opening imbalance in this pipeline.",
            ),
            options=ProviderCapability(MarketDataType.OPTIONS, base_status, base_message),
        )

    def get_official_open(self, symbol: str, at_time: datetime) -> float:
        metrics = self.get_session_metrics(symbol, at_time)
        if metrics is None or metrics.official_open is None:
            raise RuntimeError(f"Moomoo returned no official open for {symbol}.")
        return metrics.official_open

    def get_last_price(self, symbol: str, at_time: datetime) -> float:
        metrics = self.get_session_metrics(symbol, at_time)
        if metrics is None or metrics.last_price is None:
            raise RuntimeError(f"Moomoo returned no last price for {symbol}.")
        return metrics.last_price

    def get_session_vwap(self, symbol: str, at_time: datetime) -> float:
        metrics = self.get_session_metrics(symbol, at_time)
        if metrics is None or metrics.session_vwap is None:
            raise RuntimeError(f"Moomoo returned no session VWAP for {symbol}.")
        return metrics.session_vwap

    def get_session_metrics(self, symbol: str, at_time: datetime) -> SessionMetrics | None:
        self._require_backend()
        return self.backend.fetch_session_metrics([symbol], at_time).get(symbol.upper())

    def get_minute_bars(self, symbol: str, start: datetime, end: datetime) -> list[MinuteBar]:
        return self.get_minute_bars_batch([symbol], start, end).get(symbol.upper(), [])

    def get_direct_fifteen_minute_bars(self, symbol: str, start: datetime, end: datetime) -> list[MinuteBar]:
        return self.get_direct_fifteen_minute_bars_batch([symbol], start, end).get(symbol.upper(), [])

    def get_daily_bars(self, symbol: str, start: datetime, end: datetime) -> list[MinuteBar]:
        return self.get_daily_bars_batch([symbol], start, end).get(symbol.upper(), [])

    def get_opening_imbalance(self, symbol: str, trade_date: date) -> OpeningImbalance | None:
        return None

    def get_option_quotes(self, symbol: str, at_time: datetime) -> list[OptionQuote]:
        return self.get_option_quotes_batch([symbol], at_time).get(symbol, [])

    def get_option_quotes_batch(
        self,
        symbols: Sequence[str],
        at_time: datetime,
    ) -> dict[str, list[OptionQuote]]:
        self._require_backend()
        return self.backend.fetch_option_quotes(symbols, at_time)

    def get_minute_bars_batch(
        self,
        symbols: Sequence[str],
        start: datetime,
        end: datetime,
    ) -> dict[str, list[MinuteBar]]:
        self._require_backend()
        return self.backend.fetch_minute_bars(symbols, start, end)

    def get_direct_fifteen_minute_bars_batch(
        self,
        symbols: Sequence[str],
        start: datetime,
        end: datetime,
    ) -> dict[str, list[MinuteBar]]:
        self._require_backend()
        return self.backend.fetch_direct_fifteen_minute_bars(symbols, start, end)

    def get_daily_bars_batch(
        self,
        symbols: Sequence[str],
        start: datetime,
        end: datetime,
    ) -> dict[str, list[MinuteBar]]:
        self._require_backend()
        return self.backend.fetch_daily_bars(symbols, start, end)

    def _base_status(self) -> tuple[CapabilityStatus, str]:
        if not self.settings.enabled:
            return CapabilityStatus.UNAVAILABLE, "Moomoo is disabled in the current settings."
        if importlib.util.find_spec("moomoo") is None and importlib.util.find_spec("futu") is None:
            return CapabilityStatus.UNAVAILABLE, "Optional dependency moomoo-api is not installed."
        if not self._is_socket_reachable():
            return CapabilityStatus.UNAVAILABLE, (
                f"OpenD is not reachable at {self.settings.host}:{self.settings.port}."
            )
        if self.backend is None:
            return CapabilityStatus.UNTESTED, "OpenD is reachable, but no backend adapter is configured."
        return self.backend.probe()

    def _is_socket_reachable(self) -> bool:
        try:
            with socket.create_connection(
                (self.settings.host, self.settings.port),
                timeout=self.socket_timeout_seconds,
            ):
                return True
        except OSError:
            return False

    def _require_backend(self) -> None:
        status, message = self._base_status()
        if status is not CapabilityStatus.AVAILABLE or self.backend is None:
            raise RuntimeError(message)


def _load_moomoo_module():
    try:
        import moomoo as module

        return module
    except ModuleNotFoundError:
        import futu as module

        return module


def _is_missing(value: Any) -> bool:
    if value is None:
        return True

    try:
        import pandas as pd

        if pd.isna(value):
            return True
    except Exception:  # pragma: no cover - defensive fallback for environments without pandas
        pass

    if isinstance(value, str) and value.strip().upper() == "N/A":
        return True
    return False
