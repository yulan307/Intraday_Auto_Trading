from __future__ import annotations

from datetime import datetime
from pathlib import Path

from intraday_auto_trading.interfaces.repositories import MarketDataRepository
from intraday_auto_trading.models import (
    DailyCoverage,
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

    def load_price_bars_with_source_priority(
        self,
        symbol: str,
        bar_size: str,
        start: datetime,
        end: datetime,
        source_priority: list[str],
    ) -> tuple[list[MinuteBar], str]:
        """Load bars, returning one bar per timestamp from the highest-priority source.

        Returns a tuple of (bars, winning_source). winning_source is the source of the
        first bar returned, or "" if no bars are found.
        """
        if not source_priority:
            return [], ""

        placeholders = ",".join("?" * len(source_priority))
        priority_case = " ".join(
            f"WHEN ? THEN {i + 1}" for i, _ in enumerate(source_priority)
        )
        params: list = (
            [symbol, bar_size, to_storage_ts(start), to_storage_ts(end)]
            + list(source_priority)
            + list(source_priority)
        )

        with connect_sqlite(self.db_path) as connection:
            rows = connection.execute(
                f"""
                SELECT ts, open, high, low, close, volume, source
                FROM price_bars
                WHERE symbol = ?
                  AND bar_size = ?
                  AND ts >= ?
                  AND ts <= ?
                  AND source IN ({placeholders})
                ORDER BY ts ASC,
                  CASE source {priority_case} ELSE 99 END ASC
                """,
                params,
            ).fetchall()

        if not rows:
            return [], ""

        seen_ts: set[str] = set()
        bars: list[MinuteBar] = []
        winning_source = rows[0]["source"]
        for row in rows:
            ts = row["ts"]
            if ts in seen_ts:
                continue
            seen_ts.add(ts)
            bars.append(
                MinuteBar(
                    timestamp=datetime.fromisoformat(ts),
                    open=row["open"],
                    high=row["high"],
                    low=row["low"],
                    close=row["close"],
                    volume=row["volume"],
                )
            )

        return bars, winning_source

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

    def load_session_metrics(self, symbol: str, at_time: datetime) -> SessionMetrics | None:
        """Return the most-recent session_metrics row for symbol at or before at_time."""
        with connect_sqlite(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT symbol, ts, official_open, last_price, session_vwap, source
                FROM session_metrics
                WHERE symbol = ?
                  AND ts <= ?
                ORDER BY ts DESC
                LIMIT 1
                """,
                (symbol, to_storage_ts(at_time)),
            ).fetchone()

        if row is None:
            return None
        return SessionMetrics(
            symbol=row["symbol"],
            timestamp=datetime.fromisoformat(row["ts"]),
            source=row["source"],
            official_open=row["official_open"],
            last_price=row["last_price"],
            session_vwap=row["session_vwap"],
        )

    def load_option_quotes(self, symbol: str, start: datetime, end: datetime) -> list[OptionQuote]:
        """Return all option quotes for symbol with snapshot_ts in [start, end]."""
        with connect_sqlite(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT
                    oq.contract_id, oq.symbol, oq.snapshot_ts,
                    oq.bid, oq.ask, oq.bid_size, oq.ask_size,
                    oq.last, oq.volume, oq.iv, oq.delta, oq.gamma,
                    oc.strike, oc.option_type, oc.expiry, oc.exchange, oc.multiplier
                FROM option_quotes oq
                JOIN option_contracts oc ON oc.contract_id = oq.contract_id
                WHERE oq.symbol = ?
                  AND oq.snapshot_ts >= ?
                  AND oq.snapshot_ts <= ?
                ORDER BY oq.snapshot_ts ASC
                """,
                (symbol, to_storage_ts(start), to_storage_ts(end)),
            ).fetchall()

        return [
            OptionQuote(
                symbol=row["symbol"],
                strike=row["strike"],
                side=row["option_type"],
                bid=row["bid"] or 0.0,
                ask=row["ask"] or 0.0,
                bid_size=row["bid_size"] or 0,
                ask_size=row["ask_size"] or 0,
                last=row["last"] or 0.0,
                volume=row["volume"] or 0,
                iv=row["iv"],
                delta=row["delta"],
                gamma=row["gamma"],
                contract_id=row["contract_id"],
                expiry=row["expiry"],
                exchange=row["exchange"],
                multiplier=row["multiplier"],
                snapshot_time=datetime.fromisoformat(row["snapshot_ts"]),
            )
            for row in rows
        ]

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

    def save_daily_coverage(self, coverage: DailyCoverage) -> None:
        with connect_sqlite(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO daily_coverage (
                    symbol, bar_size, trade_date, source,
                    expected_bars, actual_bars, is_complete, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, bar_size, trade_date) DO UPDATE SET
                    source = excluded.source,
                    expected_bars = excluded.expected_bars,
                    actual_bars = excluded.actual_bars,
                    is_complete = excluded.is_complete,
                    updated_at = excluded.updated_at
                """,
                (
                    coverage.symbol,
                    coverage.bar_size,
                    coverage.trade_date,
                    coverage.source,
                    coverage.expected_bars,
                    coverage.actual_bars,
                    int(coverage.is_complete),
                    to_storage_ts(datetime.utcnow()),
                ),
            )

    def load_daily_coverage(
        self, symbol: str, bar_size: str, trade_date: str
    ) -> DailyCoverage | None:
        with connect_sqlite(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT symbol, bar_size, trade_date, source,
                       expected_bars, actual_bars, is_complete
                FROM daily_coverage
                WHERE symbol = ? AND bar_size = ? AND trade_date = ?
                """,
                (symbol, bar_size, trade_date),
            ).fetchone()
        if row is None:
            return None
        return DailyCoverage(
            symbol=row["symbol"],
            bar_size=row["bar_size"],
            trade_date=row["trade_date"],
            source=row["source"],
            expected_bars=row["expected_bars"],
            actual_bars=row["actual_bars"],
            is_complete=bool(row["is_complete"]),
        )

    def load_daily_coverage_range(
        self, symbols: list[str], bar_size: str, start_date: str, end_date: str
    ) -> dict[tuple[str, str], DailyCoverage]:
        if not symbols:
            return {}
        placeholders = ",".join("?" * len(symbols))
        with connect_sqlite(self.db_path) as connection:
            rows = connection.execute(
                f"""
                SELECT symbol, bar_size, trade_date, source,
                       expected_bars, actual_bars, is_complete
                FROM daily_coverage
                WHERE symbol IN ({placeholders})
                  AND bar_size = ?
                  AND trade_date BETWEEN ? AND ?
                """,
                (*symbols, bar_size, start_date, end_date),
            ).fetchall()
        result: dict[tuple[str, str], DailyCoverage] = {}
        for row in rows:
            key = (row["symbol"], row["trade_date"])
            result[key] = DailyCoverage(
                symbol=row["symbol"],
                bar_size=row["bar_size"],
                trade_date=row["trade_date"],
                source=row["source"],
                expected_bars=row["expected_bars"],
                actual_bars=row["actual_bars"],
                is_complete=bool(row["is_complete"]),
            )
        return result

    def _contract_id_for(self, quote: OptionQuote) -> str:
        if quote.contract_id:
            return quote.contract_id
        expiry = quote.expiry or "UNKNOWN"
        return f"{quote.symbol}:{expiry}:{quote.strike:.2f}:{quote.side.upper()}"

