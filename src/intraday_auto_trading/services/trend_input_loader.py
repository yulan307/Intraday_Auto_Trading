from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from intraday_auto_trading.interfaces.brokers import MarketDataGateway
from intraday_auto_trading.interfaces.repositories import MarketDataRepository
from intraday_auto_trading.models import MinuteBar, OptionQuote, SessionMetrics, TrendInput
from intraday_auto_trading.services.data_fetch_policy import DataFetchPolicy, default_policy


class TrendInputLoader:
    """Unified loader for TrendInput — works for both live and historical eval times.

    Fetch strategy:
      1. DB first (source priority: policy.db_source_priority).
      2. On DB miss, determine "live" vs "historical" from eval_time vs today (ET).
         - Live  (eval_time.date() >= today ET): policy.live_source_order  → RuntimeError on all fail.
         - Historical (eval_time.date() < today ET): policy.history_source_order → RuntimeError on all fail.
      3. Successful gateway fetch is written back to DB immediately.

    Option quotes never raise — an empty list is acceptable when all sources are skipped or fail.
    IBKR options are skipped when policy.ibkr_options_enabled is False.
    Session metrics fall back to bar-derived values when all gateways fail but bars are available.
    """

    def __init__(
        self,
        repository: MarketDataRepository,
        gateways: dict[str, MarketDataGateway],
        session_open: datetime,
        policy: DataFetchPolicy | None = None,
        timezone: ZoneInfo | None = None,
    ) -> None:
        self.repository = repository
        self.gateways = gateways
        self.session_open = session_open
        self.policy = policy or default_policy()
        self.timezone = timezone or ZoneInfo("America/New_York")

    def load(self, symbol: str, eval_time: datetime) -> TrendInput:
        bars = self._fetch_bars(symbol, eval_time)
        metrics = self._fetch_session_metrics(symbol, eval_time, bars)
        option_quotes = self._fetch_option_quotes(symbol, eval_time)
        return TrendInput(
            symbol=symbol,
            eval_time=eval_time,
            official_open=metrics.official_open,  # type: ignore[arg-type]
            last_price=metrics.last_price,  # type: ignore[arg-type]
            session_vwap=metrics.session_vwap,  # type: ignore[arg-type]
            minute_bars=bars,
            option_quotes=option_quotes,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _is_live(self, eval_time: datetime) -> bool:
        today_et = datetime.now(self.timezone).date()
        return eval_time.astimezone(self.timezone).date() >= today_et

    def _source_order(self, eval_time: datetime) -> list[str]:
        return (
            self.policy.live_source_order
            if self._is_live(eval_time)
            else self.policy.history_source_order
        )

    def _fetch_bars(self, symbol: str, eval_time: datetime) -> list[MinuteBar]:
        bars, _ = self.repository.load_price_bars_with_source_priority(
            symbol=symbol,
            bar_size="1m",
            start=self.session_open,
            end=eval_time,
            source_priority=self.policy.db_source_priority,
        )
        if bars:
            return bars

        source_order = self._source_order(eval_time)
        for source_name in source_order:
            gateway = self.gateways.get(source_name)
            if gateway is None:
                continue
            fetched = gateway.get_minute_bars(symbol, self.session_open, eval_time)
            if fetched:
                self.repository.save_price_bars(symbol, "1m", fetched, source_name)
                return fetched

        mode = "live" if self._is_live(eval_time) else "historical"
        raise RuntimeError(
            f"No 1m bars for {symbol} [{self.session_open}, {eval_time}] "
            f"from any {mode} source: {source_order}"
        )

    def _fetch_session_metrics(
        self,
        symbol: str,
        eval_time: datetime,
        bars: list[MinuteBar],
    ) -> SessionMetrics:
        db_metrics = self.repository.load_session_metrics(symbol, eval_time)
        if db_metrics is not None:
            official_open = (
                db_metrics.official_open
                if db_metrics.official_open is not None
                else (bars[0].open if bars else None)
            )
            last_price = (
                db_metrics.last_price
                if db_metrics.last_price is not None
                else (bars[-1].close if bars else None)
            )
            session_vwap = (
                db_metrics.session_vwap
                if db_metrics.session_vwap is not None
                else (bars[-1].close if bars else None)
            )
            return SessionMetrics(
                symbol=symbol,
                timestamp=eval_time,
                source=db_metrics.source,
                official_open=official_open,
                last_price=last_price,
                session_vwap=session_vwap,
            )

        source_order = self._source_order(eval_time)
        for source_name in source_order:
            gateway = self.gateways.get(source_name)
            if gateway is None:
                continue
            fetched = gateway.get_session_metrics(symbol, eval_time)
            if fetched is not None:
                self.repository.save_session_metrics(fetched)
                return fetched

        if bars:
            total_volume = sum(b.volume for b in bars)
            vwap = (
                bars[-1].close
                if total_volume <= 0
                else sum(b.close * b.volume for b in bars) / total_volume
            )
            return SessionMetrics(
                symbol=symbol,
                timestamp=eval_time,
                source="derived",
                official_open=bars[0].open,
                last_price=bars[-1].close,
                session_vwap=vwap,
            )

        mode = "live" if self._is_live(eval_time) else "historical"
        raise RuntimeError(
            f"No session metrics for {symbol} at {eval_time} "
            f"from any {mode} source and no bars to derive from"
        )

    def _fetch_option_quotes(self, symbol: str, eval_time: datetime) -> list[OptionQuote]:
        quotes = self.repository.load_option_quotes(symbol, self.session_open, eval_time)
        if quotes:
            return quotes

        source_order = self._source_order(eval_time)
        for source_name in source_order:
            if source_name == "ibkr" and not self.policy.ibkr_options_enabled:
                continue
            gateway = self.gateways.get(source_name)
            if gateway is None:
                continue
            fetched = gateway.get_option_quotes(symbol, eval_time)
            if fetched:
                self.repository.save_option_quotes(fetched, source_name)
                return fetched

        return []
