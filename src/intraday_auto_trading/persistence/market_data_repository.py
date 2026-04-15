from __future__ import annotations

from datetime import datetime
from pathlib import Path

from intraday_auto_trading.interfaces.repositories import MarketDataRepository
from intraday_auto_trading.models import (
    MinuteBar,
    OpeningImbalance,
    OptionQuote,
    SessionMetrics,
    SymbolInfo,
    TrendSnapshot,
)
from intraday_auto_trading.persistence.schema import create_market_data_schema
from intraday_auto_trading.persistence.sqlite_base import connect_sqlite, to_storage_ts


class SqliteMarketDataRepository(MarketDataRepository):
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.initialize()

    def initialize(self) -> None:
        with connect_sqlite(self.db_path) as connection:
            create_market_data_schema(connection)

    def upsert_symbol(self, symbol_info: SymbolInfo) -> None:
        now = to_storage_ts(datetime.utcnow())
        with connect_sqlite(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO symbols (
                    symbol, name, exchange, asset_type, currency, is_active, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    name = excluded.name,
                    exchange = excluded.exchange,
                    asset_type = excluded.asset_type,
                    currency = excluded.currency,
                    is_active = excluded.is_active,
                    updated_at = excluded.updated_at
                """,
                (
                    symbol_info.symbol,
                    symbol_info.name,
                    symbol_info.exchange,
                    symbol_info.asset_type,
                    symbol_info.currency,
                    int(symbol_info.is_active),
                    now,
                    now,
                ),
            )

    def save_price_bars(
        self,
        symbol: str,
        bar_size: str,
        bars: list[MinuteBar],
        source: str,
    ) -> None:
        if not bars:
            return

        created_at = to_storage_ts(datetime.utcnow())
        with connect_sqlite(self.db_path) as connection:
            connection.executemany(
                """
                INSERT INTO price_bars (
                    symbol, bar_size, ts, open, high, low, close, volume, source, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, bar_size, ts, source) DO UPDATE SET
                    open = excluded.open,
                    high = excluded.high,
                    low = excluded.low,
                    close = excluded.close,
                    volume = excluded.volume
                """,
                [
                    (
                        symbol,
                        bar_size,
                        to_storage_ts(bar.timestamp),
                        bar.open,
                        bar.high,
                        bar.low,
                        bar.close,
                        bar.volume,
                        source,
                        created_at,
                    )
                    for bar in bars
                ],
            )

    def load_price_bars(
        self,
        symbol: str,
        bar_size: str,
        start: datetime,
        end: datetime,
    ) -> list[MinuteBar]:
        with connect_sqlite(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT ts, open, high, low, close, volume
                FROM price_bars
                WHERE symbol = ?
                  AND bar_size = ?
                  AND ts >= ?
                  AND ts <= ?
                ORDER BY ts ASC
                """,
                (symbol, bar_size, to_storage_ts(start), to_storage_ts(end)),
            ).fetchall()

        return [
            MinuteBar(
                timestamp=datetime.fromisoformat(row["ts"]),
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                volume=row["volume"],
            )
            for row in rows
        ]

    def save_session_metrics(self, metrics: SessionMetrics) -> None:
        with connect_sqlite(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO session_metrics (
                    symbol, ts, official_open, last_price, session_vwap, source, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, ts, source) DO UPDATE SET
                    official_open = excluded.official_open,
                    last_price = excluded.last_price,
                    session_vwap = excluded.session_vwap
                """,
                (
                    metrics.symbol,
                    to_storage_ts(metrics.timestamp),
                    metrics.official_open,
                    metrics.last_price,
                    metrics.session_vwap,
                    metrics.source,
                    to_storage_ts(datetime.utcnow()),
                ),
            )

    def save_opening_imbalance(self, imbalance: OpeningImbalance) -> None:
        with connect_sqlite(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO opening_imbalance (
                    symbol, trade_date, opening_imbalance_side, opening_imbalance_qty,
                    paired_shares, indicative_open_price, source, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, trade_date, source) DO UPDATE SET
                    opening_imbalance_side = excluded.opening_imbalance_side,
                    opening_imbalance_qty = excluded.opening_imbalance_qty,
                    paired_shares = excluded.paired_shares,
                    indicative_open_price = excluded.indicative_open_price
                """,
                (
                    imbalance.symbol,
                    imbalance.trade_date,
                    imbalance.opening_imbalance_side,
                    imbalance.opening_imbalance_qty,
                    imbalance.paired_shares,
                    imbalance.indicative_open_price,
                    imbalance.source,
                    to_storage_ts(datetime.utcnow()),
                ),
            )

    def save_option_quotes(self, quotes: list[OptionQuote], source: str) -> None:
        if not quotes:
            return

        now = to_storage_ts(datetime.utcnow())
        with connect_sqlite(self.db_path) as connection:
            connection.executemany(
                """
                INSERT INTO option_contracts (
                    contract_id, symbol, expiry, strike, option_type, exchange,
                    multiplier, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(contract_id) DO UPDATE SET
                    symbol = excluded.symbol,
                    expiry = excluded.expiry,
                    strike = excluded.strike,
                    option_type = excluded.option_type,
                    exchange = excluded.exchange,
                    multiplier = excluded.multiplier,
                    updated_at = excluded.updated_at
                """,
                [
                    (
                        self._contract_id_for(quote),
                        quote.symbol,
                        quote.expiry or "",
                        quote.strike,
                        quote.side.upper(),
                        quote.exchange,
                        quote.multiplier,
                        now,
                        now,
                    )
                    for quote in quotes
                ],
            )
            connection.executemany(
                """
                INSERT INTO option_quotes (
                    contract_id, symbol, snapshot_ts, bid, ask, bid_size, ask_size,
                    last, volume, iv, delta, gamma, source, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(contract_id, snapshot_ts, source) DO UPDATE SET
                    bid = excluded.bid,
                    ask = excluded.ask,
                    bid_size = excluded.bid_size,
                    ask_size = excluded.ask_size,
                    last = excluded.last,
                    volume = excluded.volume,
                    iv = excluded.iv,
                    delta = excluded.delta,
                    gamma = excluded.gamma
                """,
                [
                    (
                        self._contract_id_for(quote),
                        quote.symbol,
                        to_storage_ts(quote.snapshot_time or datetime.utcnow()),
                        quote.bid,
                        quote.ask,
                        quote.bid_size,
                        quote.ask_size,
                        quote.last,
                        quote.volume,
                        quote.iv,
                        quote.delta,
                        quote.gamma,
                        source,
                        now,
                    )
                    for quote in quotes
                ],
            )

    def save_trend_snapshot(self, snapshot: TrendSnapshot) -> None:
        with connect_sqlite(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO trend_snapshots (
                    symbol, eval_time, regime, score, reason,
                    official_open, last_price, session_vwap, source, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, eval_time, source) DO UPDATE SET
                    regime = excluded.regime,
                    score = excluded.score,
                    reason = excluded.reason,
                    official_open = excluded.official_open,
                    last_price = excluded.last_price,
                    session_vwap = excluded.session_vwap
                """,
                (
                    snapshot.symbol,
                    to_storage_ts(snapshot.eval_time),
                    snapshot.regime.value,
                    snapshot.score,
                    snapshot.reason,
                    snapshot.official_open,
                    snapshot.last_price,
                    snapshot.session_vwap,
                    snapshot.source,
                    to_storage_ts(datetime.utcnow()),
                ),
            )

    def _contract_id_for(self, quote: OptionQuote) -> str:
        if quote.contract_id:
            return quote.contract_id
        expiry = quote.expiry or "UNKNOWN"
        return f"{quote.symbol}:{expiry}:{quote.strike:.2f}:{quote.side.upper()}"

