from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import importlib.util
from math import fabs
import socket
from time import monotonic, sleep
from threading import Event, Lock, Thread
from typing import Iterator, Protocol, Sequence
from zoneinfo import ZoneInfo

from intraday_auto_trading.config import IBKRProfileSettings
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

try:
    from ibapi.client import EClient
    from ibapi.contract import Contract
    from ibapi.wrapper import EWrapper
except ModuleNotFoundError:  # pragma: no cover - exercised by capability checks
    EClient = object  # type: ignore[assignment]
    EWrapper = object  # type: ignore[assignment]
    Contract = None  # type: ignore[assignment]


INFO_ERROR_CODES = {2104, 2106, 2107, 2108, 2158, 2176}
AUCTION_VOLUME_TICK = 34
AUCTION_PRICE_TICK = 35
AUCTION_IMBALANCE_TICK = 36
REGULATORY_IMBALANCE_TICK = 61


class IBKRBackend(Protocol):
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

    def fetch_session_metrics(self, symbol: str, at_time: datetime) -> SessionMetrics | None: ...

    def fetch_opening_imbalance(self, symbol: str, trade_date: date) -> OpeningImbalance | None: ...


@dataclass(slots=True)
class _HistoricalRequest:
    done: Event = field(default_factory=Event)
    bars: list[MinuteBar] = field(default_factory=list)
    error_message: str | None = None


@dataclass(slots=True)
class _OpeningImbalanceRequest:
    error_message: str | None = None
    paired_shares: float | None = None
    indicative_open_price: float | None = None
    imbalance_qty: float | None = None
    regulatory_imbalance_qty: float | None = None
    first_update_at: float | None = None
    last_update_at: float | None = None

    def mark_update(self) -> None:
        now = monotonic()
        if self.first_update_at is None:
            self.first_update_at = now
        self.last_update_at = now

    def has_data(self) -> bool:
        return any(
            value is not None
            for value in (
                self.paired_shares,
                self.indicative_open_price,
                self.imbalance_qty,
                self.regulatory_imbalance_qty,
            )
        )


class _IBHistoricalDataApp(EWrapper, EClient):  # type: ignore[misc]
    def __init__(self, exchange_timezone: str) -> None:
        EClient.__init__(self, self)
        self.exchange_timezone = ZoneInfo(exchange_timezone)
        self.connected_event = Event()
        self.connection_error: str | None = None
        self._requests: dict[int, _HistoricalRequest] = {}
        self._lock = Lock()

    def nextValidId(self, orderId: int) -> None:  # noqa: N802
        self.connected_event.set()

    def error(self, reqId: int, errorCode: int, errorString: str, advancedOrderRejectJson: str = "") -> None:  # noqa: N802
        if errorCode in INFO_ERROR_CODES:
            return

        message = f"IBKR error {errorCode}: {errorString}"
        if reqId in {-1, 0}:
            self.connection_error = message
            self.connected_event.set()
            return

        with self._lock:
            request = self._requests.get(reqId)
        if request is not None:
            request.error_message = message
            request.done.set()

    def historicalData(self, reqId: int, bar) -> None:  # noqa: N802
        timestamp = self._parse_bar_time(bar.date)
        minute_bar = MinuteBar(
            timestamp=timestamp,
            open=float(bar.open),
            high=float(bar.high),
            low=float(bar.low),
            close=float(bar.close),
            volume=float(bar.volume),
        )
        with self._lock:
            request = self._requests.get(reqId)
        if request is not None:
            request.bars.append(minute_bar)

    def historicalDataEnd(self, reqId: int, start: str, end: str) -> None:  # noqa: N802
        with self._lock:
            request = self._requests.get(reqId)
        if request is not None:
            request.done.set()

    def register_request(self, req_id: int) -> _HistoricalRequest:
        request = _HistoricalRequest()
        with self._lock:
            self._requests[req_id] = request
        return request

    def pop_request(self, req_id: int) -> None:
        with self._lock:
            self._requests.pop(req_id, None)

    def _parse_bar_time(self, raw_value: str | int) -> datetime:
        if isinstance(raw_value, int):
            return datetime.fromtimestamp(raw_value, tz=timezone.utc).astimezone(self.exchange_timezone).replace(tzinfo=None)
        text = str(raw_value).strip()
        if text.isdigit():
            return datetime.fromtimestamp(int(text), tz=timezone.utc).astimezone(self.exchange_timezone).replace(tzinfo=None)
        return datetime.strptime(text, "%Y%m%d %H:%M:%S")


class _IBMarketDataApp(EWrapper, EClient):  # type: ignore[misc]
    def __init__(self) -> None:
        EClient.__init__(self, self)
        self.connected_event = Event()
        self.connection_error: str | None = None
        self._requests: dict[int, _OpeningImbalanceRequest] = {}
        self._lock = Lock()

    def nextValidId(self, orderId: int) -> None:  # noqa: N802
        self.connected_event.set()

    def error(self, reqId: int, errorCode: int, errorString: str, advancedOrderRejectJson: str = "") -> None:  # noqa: N802
        if errorCode in INFO_ERROR_CODES:
            return

        message = f"IBKR error {errorCode}: {errorString}"
        if reqId in {-1, 0}:
            self.connection_error = message
            self.connected_event.set()
            return

        with self._lock:
            request = self._requests.get(reqId)
        if request is not None:
            request.error_message = message

    def tickPrice(self, reqId: int, field: int, price: float, attribs) -> None:  # noqa: N802
        if field != AUCTION_PRICE_TICK:
            return
        with self._lock:
            request = self._requests.get(reqId)
        if request is not None and price >= 0:
            request.indicative_open_price = float(price)
            request.mark_update()

    def tickSize(self, reqId: int, field: int, size) -> None:  # noqa: N802
        with self._lock:
            request = self._requests.get(reqId)
        if request is None:
            return

        size_value = float(size)
        if field == AUCTION_VOLUME_TICK:
            request.paired_shares = size_value
            request.mark_update()
        elif field == AUCTION_IMBALANCE_TICK:
            request.imbalance_qty = size_value
            request.mark_update()
        elif field == REGULATORY_IMBALANCE_TICK:
            request.regulatory_imbalance_qty = size_value
            request.mark_update()

    def register_request(self, req_id: int) -> _OpeningImbalanceRequest:
        request = _OpeningImbalanceRequest()
        with self._lock:
            self._requests[req_id] = request
        return request

    def pop_request(self, req_id: int) -> None:
        with self._lock:
            self._requests.pop(req_id, None)


@dataclass(slots=True)
class RealIBKRBackend:
    profile: IBKRProfileSettings
    exchange_timezone: str = "America/New_York"
    connect_timeout_seconds: float = 5.0
    request_timeout_seconds: float = 30.0
    market_data_timeout_seconds: float = 5.0
    market_data_settle_seconds: float = 1.0

    def fetch_minute_bars(
        self,
        symbols: Sequence[str],
        start: datetime,
        end: datetime,
    ) -> dict[str, list[MinuteBar]]:
        return self._fetch_bars(symbols, start, end, bar_size="1 min")

    def fetch_direct_fifteen_minute_bars(
        self,
        symbols: Sequence[str],
        start: datetime,
        end: datetime,
    ) -> dict[str, list[MinuteBar]]:
        return self._fetch_bars(symbols, start, end, bar_size="15 mins")

    def fetch_session_metrics(self, symbol: str, at_time: datetime) -> SessionMetrics | None:
        return None

    def fetch_opening_imbalance(self, symbol: str, trade_date: date) -> OpeningImbalance | None:
        with self._connected_market_data_app() as app:
            req_id = 1
            request = app.register_request(req_id)
            app.reqMarketDataType(1)
            app.reqMktData(
                req_id,
                self._contract_for(symbol),
                "225",
                False,
                False,
                [],
            )
            try:
                deadline = monotonic() + self.market_data_timeout_seconds
                while monotonic() < deadline:
                    if request.error_message:
                        raise RuntimeError(f"{request.error_message} ({symbol})")
                    if request.has_data() and request.last_update_at is not None:
                        if monotonic() - request.last_update_at >= self.market_data_settle_seconds:
                            break
                    sleep(0.1)
            finally:
                app.cancelMktData(req_id)
                app.pop_request(req_id)

        imbalance_value = request.imbalance_qty
        if imbalance_value is None and request.regulatory_imbalance_qty is None and request.paired_shares is None and request.indicative_open_price is None:
            return None

        side = None
        if imbalance_value is not None and imbalance_value != 0:
            side = "BUY" if imbalance_value > 0 else "SELL"

        return OpeningImbalance(
            symbol=symbol,
            trade_date=trade_date.isoformat(),
            source="ibkr",
            opening_imbalance_side=side,
            opening_imbalance_qty=fabs(imbalance_value) if imbalance_value is not None else None,
            paired_shares=request.paired_shares,
            indicative_open_price=request.indicative_open_price,
        )

    def _fetch_bars(
        self,
        symbols: Sequence[str],
        start: datetime,
        end: datetime,
        bar_size: str,
    ) -> dict[str, list[MinuteBar]]:
        results: dict[str, list[MinuteBar]] = {}
        with self._connected_app() as app:
            for index, symbol in enumerate(symbols, start=1):
                req_id = index
                request = app.register_request(req_id)
                app.reqHistoricalData(
                    req_id,
                    self._contract_for(symbol),
                    self._format_end_datetime(end),
                    self._duration_string(start, end),
                    bar_size,
                    "TRADES",
                    1,
                    2,
                    False,
                    [],
                )
                if not request.done.wait(self.request_timeout_seconds):
                    app.cancelHistoricalData(req_id)
                    app.pop_request(req_id)
                    raise RuntimeError(f"Timed out waiting for historical data for {symbol}.")
                app.pop_request(req_id)
                if request.error_message:
                    raise RuntimeError(f"{request.error_message} ({symbol})")
                results[symbol] = [bar for bar in request.bars if start <= bar.timestamp <= end]
        return results

    @contextmanager
    def _connected_app(self) -> Iterator[_IBHistoricalDataApp]:
        app = _IBHistoricalDataApp(self.exchange_timezone)
        app.connect(self.profile.host, self.profile.port, self.profile.client_id)
        worker = Thread(target=app.run, daemon=True)
        worker.start()

        if not app.connected_event.wait(self.connect_timeout_seconds):
            app.disconnect()
            raise RuntimeError("Timed out connecting to IB Gateway.")

        if app.connection_error:
            app.disconnect()
            raise RuntimeError(app.connection_error)

        try:
            yield app
        finally:
            app.disconnect()
            worker.join(timeout=1.0)

    @contextmanager
    def _connected_market_data_app(self) -> Iterator[_IBMarketDataApp]:
        app = _IBMarketDataApp()
        app.connect(self.profile.host, self.profile.port, self.profile.client_id)
        worker = Thread(target=app.run, daemon=True)
        worker.start()

        if not app.connected_event.wait(self.connect_timeout_seconds):
            app.disconnect()
            raise RuntimeError("Timed out connecting to IB Gateway.")

        if app.connection_error:
            app.disconnect()
            raise RuntimeError(app.connection_error)

        try:
            yield app
        finally:
            app.disconnect()
            worker.join(timeout=1.0)

    def _contract_for(self, symbol: str):
        contract = Contract()
        contract.symbol = symbol
        contract.secType = "STK"
        contract.exchange = "SMART"
        contract.currency = "USD"
        return contract

    def _format_end_datetime(self, value: datetime) -> str:
        localized = value.replace(tzinfo=ZoneInfo(self.exchange_timezone))
        return localized.astimezone(timezone.utc).strftime("%Y%m%d-%H:%M:%S")

    def _duration_string(self, start: datetime, end: datetime) -> str:
        total_seconds = max(60, int((end - start).total_seconds()) + 60)
        if total_seconds <= 86400:
            return f"{total_seconds} S"
        total_days = max(1, (total_seconds + 86399) // 86400)
        return f"{total_days} D"


@dataclass(slots=True)
class IBKRMarketDataGateway:
    profile_name: str
    profile: IBKRProfileSettings
    backend: IBKRBackend | None = None
    exchange_timezone: str = "America/New_York"
    socket_timeout_seconds: float = 0.5

    provider_name: str = "ibkr"

    def probe_capabilities(self) -> ProviderCapabilities:
        status, message = self._base_status()
        minute_capability = ProviderCapability(MarketDataType.BARS_1M, status, message)
        direct_capability = ProviderCapability(MarketDataType.BARS_15M_DIRECT, status, message)
        derived_status = CapabilityStatus.AVAILABLE if status is CapabilityStatus.AVAILABLE else status
        derived_message = "Derived from 1m bars inside the sync service." if status is CapabilityStatus.AVAILABLE else message
        opening_status = CapabilityStatus.AVAILABLE if status is CapabilityStatus.AVAILABLE else status
        opening_message = (
            "Opening imbalance uses IBKR auction ticks and may only populate near the auction window."
            if status is CapabilityStatus.AVAILABLE
            else message
        )
        options_capability = ProviderCapability(
            MarketDataType.OPTIONS,
            CapabilityStatus.UNSUPPORTED,
            "Options are expected to come from Moomoo in this pipeline.",
        )
        return ProviderCapabilities(
            provider=self.provider_name,
            bars_1m=minute_capability,
            bars_15m_direct=direct_capability,
            bars_15m_derived=ProviderCapability(MarketDataType.BARS_15M_DERIVED, derived_status, derived_message),
            opening_imbalance=ProviderCapability(MarketDataType.OPENING_IMBALANCE, opening_status, opening_message),
            options=options_capability,
        )

    def get_official_open(self, symbol: str, at_time: datetime) -> float:
        session_metrics = self.get_session_metrics(symbol, at_time)
        if session_metrics and session_metrics.official_open is not None:
            return session_metrics.official_open
        bars = self.get_minute_bars(symbol, at_time.replace(hour=9, minute=30, second=0, microsecond=0), at_time)
        if not bars:
            raise RuntimeError(f"No bars available for {symbol}")
        return bars[0].open

    def get_last_price(self, symbol: str, at_time: datetime) -> float:
        session_metrics = self.get_session_metrics(symbol, at_time)
        if session_metrics and session_metrics.last_price is not None:
            return session_metrics.last_price
        bars = self.get_minute_bars(symbol, at_time.replace(hour=9, minute=30, second=0, microsecond=0), at_time)
        if not bars:
            raise RuntimeError(f"No bars available for {symbol}")
        return bars[-1].close

    def get_session_vwap(self, symbol: str, at_time: datetime) -> float:
        session_metrics = self.get_session_metrics(symbol, at_time)
        if session_metrics and session_metrics.session_vwap is not None:
            return session_metrics.session_vwap
        bars = self.get_minute_bars(symbol, at_time.replace(hour=9, minute=30, second=0, microsecond=0), at_time)
        if not bars:
            raise RuntimeError(f"No bars available for {symbol}")
        total_volume = sum(bar.volume for bar in bars)
        if total_volume <= 0:
            return bars[-1].close
        return sum(bar.close * bar.volume for bar in bars) / total_volume

    def get_session_metrics(self, symbol: str, at_time: datetime) -> SessionMetrics | None:
        self._require_backend()
        return self.backend.fetch_session_metrics(symbol, at_time)

    def get_minute_bars(self, symbol: str, start: datetime, end: datetime) -> list[MinuteBar]:
        return self.get_minute_bars_batch([symbol], start, end).get(symbol, [])

    def get_direct_fifteen_minute_bars(self, symbol: str, start: datetime, end: datetime) -> list[MinuteBar]:
        return self.get_direct_fifteen_minute_bars_batch([symbol], start, end).get(symbol, [])

    def get_opening_imbalance(self, symbol: str, trade_date: date) -> OpeningImbalance | None:
        self._require_backend()
        return self.backend.fetch_opening_imbalance(symbol, trade_date)

    def get_option_quotes(self, symbol: str, at_time: datetime) -> list[OptionQuote]:
        return []

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

    def get_option_quotes_batch(
        self,
        symbols: Sequence[str],
        at_time: datetime,
    ) -> dict[str, list[OptionQuote]]:
        return {symbol: [] for symbol in symbols}

    def _base_status(self) -> tuple[CapabilityStatus, str]:
        if importlib.util.find_spec("ibapi") is None:
            return CapabilityStatus.UNAVAILABLE, "Optional dependency ibapi is not installed."
        if not self._is_socket_reachable():
            return CapabilityStatus.UNAVAILABLE, (
                f"IB Gateway {self.profile_name} profile is not reachable at "
                f"{self.profile.host}:{self.profile.port}."
            )
        if self.backend is None:
            return CapabilityStatus.UNTESTED, "IB Gateway is reachable, but no backend adapter is configured."
        return CapabilityStatus.AVAILABLE, f"Using IB Gateway {self.profile_name} profile."

    def _is_socket_reachable(self) -> bool:
        try:
            with socket.create_connection(
                (self.profile.host, self.profile.port),
                timeout=self.socket_timeout_seconds,
            ):
                return True
        except OSError:
            return False

    def _require_backend(self) -> None:
        status, message = self._base_status()
        if status is not CapabilityStatus.AVAILABLE or self.backend is None:
            raise RuntimeError(message)
