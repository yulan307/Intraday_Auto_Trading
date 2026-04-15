from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
import importlib.util
import socket
from typing import Any, Protocol, Sequence

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

    def fetch_option_quotes(
        self,
        symbols: Sequence[str],
        at_time: datetime,
    ) -> dict[str, list[OptionQuote]]: ...


@dataclass(slots=True)
class RealMoomooBackend:
    settings: MoomooSettings
    snapshot_chunk_size: int = 200

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

        return CapabilityStatus.AVAILABLE, "Using Moomoo OpenD option quotes."

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
        options_status, options_message = self._options_status()
        unsupported_message = "This pipeline routes bars and opening imbalance through IBKR first."
        return ProviderCapabilities(
            provider=self.provider_name,
            bars_1m=ProviderCapability(MarketDataType.BARS_1M, CapabilityStatus.UNSUPPORTED, unsupported_message),
            bars_15m_direct=ProviderCapability(MarketDataType.BARS_15M_DIRECT, CapabilityStatus.UNSUPPORTED, unsupported_message),
            bars_15m_derived=ProviderCapability(MarketDataType.BARS_15M_DERIVED, CapabilityStatus.UNSUPPORTED, unsupported_message),
            opening_imbalance=ProviderCapability(MarketDataType.OPENING_IMBALANCE, CapabilityStatus.UNSUPPORTED, unsupported_message),
            options=ProviderCapability(MarketDataType.OPTIONS, options_status, options_message),
        )

    def get_official_open(self, symbol: str, at_time: datetime) -> float:
        raise RuntimeError("Moomoo is not configured as the primary bar source.")

    def get_last_price(self, symbol: str, at_time: datetime) -> float:
        raise RuntimeError("Moomoo is not configured as the primary bar source.")

    def get_session_vwap(self, symbol: str, at_time: datetime) -> float:
        raise RuntimeError("Moomoo is not configured as the primary bar source.")

    def get_session_metrics(self, symbol: str, at_time: datetime) -> SessionMetrics | None:
        return None

    def get_minute_bars(self, symbol: str, start: datetime, end: datetime) -> list[MinuteBar]:
        return []

    def get_direct_fifteen_minute_bars(self, symbol: str, start: datetime, end: datetime) -> list[MinuteBar]:
        return []

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

    def _options_status(self) -> tuple[CapabilityStatus, str]:
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
        status, message = self._options_status()
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
