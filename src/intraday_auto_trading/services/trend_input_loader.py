from __future__ import annotations

from datetime import datetime, timezone

from intraday_auto_trading.interfaces.brokers import MarketDataGateway
from intraday_auto_trading.interfaces.repositories import MarketDataRepository
from intraday_auto_trading.models import TrendInput


class LiveTrendInputLoader:
    """从实时网关（IBKR / Moomoo）拉取单标的数据并组装 TrendInput。

    minute_bars 范围：session_open（09:30 ET）到 eval_time。
    option_quotes：eval_time 快照（网关内部决定合约选择逻辑）。
    session_metrics（official_open / last_price / session_vwap）：直接向网关请求。
    """

    def __init__(self, gateway: MarketDataGateway, session_open: datetime) -> None:
        self.gateway = gateway
        self.session_open = session_open

    def load(self, symbol: str, eval_time: datetime) -> TrendInput:
        metrics = self.gateway.get_session_metrics(symbol, eval_time)
        if metrics is None:
            raise ValueError(
                f"Gateway returned no session metrics for {symbol} at {eval_time}"
            )
        if metrics.official_open is None or metrics.last_price is None or metrics.session_vwap is None:
            raise ValueError(
                f"Incomplete session metrics for {symbol}: {metrics}"
            )

        bars = self.gateway.get_minute_bars(symbol, self.session_open, eval_time)
        if not bars:
            raise ValueError(
                f"Gateway returned no minute bars for {symbol} [{self.session_open}, {eval_time}]"
            )

        option_quotes = self.gateway.get_option_quotes(symbol, eval_time)

        return TrendInput(
            symbol=symbol,
            eval_time=eval_time,
            official_open=metrics.official_open,
            last_price=metrics.last_price,
            session_vwap=metrics.session_vwap,
            minute_bars=bars,
            option_quotes=option_quotes,
        )


class BacktestTrendInputLoader:
    """从本地 SQLite 数据库读取历史数据并组装 TrendInput。

    minute_bars：load_price_bars_with_source_priority，source 顺序由调用方指定。
    session_metrics：load_session_metrics，取 eval_time 前最近一条记录。
    option_quotes：load_option_quotes，取 session_open 到 eval_time 区间内的全部快照。
    """

    def __init__(
        self,
        repository: MarketDataRepository,
        session_open: datetime,
        bar_source_priority: list[str] | None = None,
    ) -> None:
        self.repository = repository
        self.session_open = session_open
        self.bar_source_priority = bar_source_priority or ["ibkr", "moomoo", "yfinance"]

    def load(self, symbol: str, eval_time: datetime) -> TrendInput:
        bars, _ = self.repository.load_price_bars_with_source_priority(
            symbol=symbol,
            bar_size="1m",
            start=self.session_open,
            end=eval_time,
            source_priority=self.bar_source_priority,
        )
        if not bars:
            raise ValueError(
                f"No 1m bars in DB for {symbol} [{self.session_open}, {eval_time}]"
            )

        metrics = self.repository.load_session_metrics(symbol, eval_time)
        if metrics is None:
            # 从 bars 中推算，与 MarketDataSyncService._resolve_session_metrics 保持一致
            total_volume = sum(b.volume for b in bars)
            vwap = (
                bars[-1].close
                if total_volume <= 0
                else sum(b.close * b.volume for b in bars) / total_volume
            )
            official_open = bars[0].open
            last_price = bars[-1].close
            session_vwap = vwap
        else:
            official_open = metrics.official_open if metrics.official_open is not None else bars[0].open
            last_price = metrics.last_price if metrics.last_price is not None else bars[-1].close
            session_vwap = metrics.session_vwap if metrics.session_vwap is not None else bars[-1].close

        option_quotes = self.repository.load_option_quotes(
            symbol=symbol,
            start=self.session_open,
            end=eval_time,
        )

        return TrendInput(
            symbol=symbol,
            eval_time=eval_time,
            official_open=official_open,
            last_price=last_price,
            session_vwap=session_vwap,
            minute_bars=bars,
            option_quotes=option_quotes,
        )
