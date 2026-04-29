"""期权数据服务：DB 优先 + 按查询记录决策是否调 gateway。

单标的接口：load_option_quotes()
批量接口：load_option_quotes_batch()  ← 推荐，一次 API 调用处理所有标的

决策流程（每个 gateway 独立判断，batch 版本将所有需要拉取的 symbol 合并成一次调用）：
  1. DB 已有 quotes → 直接使用，不再调 gateway
  2. 有 fetch_log 记录：
     - status in ("no_data", "unsupported") → 跳过（不重试）
     - status in ("permission", "api_error") → 重试 gateway
  3. 无记录 → 调 gateway
  4. Gateway 结果：
     - 有数据 → save_option_quotes + save_option_fetch_log(success)
     - 空列表 → save_option_fetch_log(no_data)
     - 异常   → save_option_fetch_log(permission|api_error)，仅影响该 gateway，继续尝试下一个
"""
from __future__ import annotations

from datetime import datetime
from typing import cast

from intraday_auto_trading.interfaces.brokers import BatchMarketDataGateway, MarketDataGateway
from intraday_auto_trading.interfaces.repositories import MarketDataRepository
from intraday_auto_trading.models import OptionFetchLog, OptionQuote

_RETRYABLE = {"permission", "api_error"}


def _classify_error(exc: Exception) -> str:
    """Map a gateway exception to a fetch-status string."""
    msg = str(exc).lower()
    if "not logged in" in msg or "session" in msg or "login" in msg:
        return "permission"
    return "api_error"


def _fetch_batch(
    gateway: MarketDataGateway,
    symbols: list[str],
    eval_time: datetime,
) -> dict[str, list[OptionQuote]]:
    """Call batch API if available, otherwise fall back to per-symbol calls."""
    if hasattr(gateway, "get_option_quotes_batch"):
        return cast(BatchMarketDataGateway, gateway).get_option_quotes_batch(symbols, eval_time)
    return {s: gateway.get_option_quotes(s, eval_time) for s in symbols}


def load_option_quotes_batch(
    symbols: list[str],
    trade_date: str,         # "YYYY-MM-DD"
    start_utc: datetime,
    end_utc: datetime,
    repository: MarketDataRepository,
    gateways: dict[str, MarketDataGateway],
    eval_time: datetime,
) -> dict[str, list[OptionQuote]]:
    """Batch version: load option quotes for multiple symbols in one gateway call per source.

    For each gateway, all symbols that need fetching are batched into a single
    get_option_quotes_batch() call, avoiding per-symbol rate limiting.

    Returns dict[symbol, list[OptionQuote]]. Missing symbols map to [].
    """
    result: dict[str, list[OptionQuote]] = {}

    # 1. Fill from DB first; collect symbols that still need fetching
    pending: list[str] = []
    for symbol in symbols:
        quotes = repository.load_option_quotes(symbol, start_utc, end_utc)
        if quotes:
            result[symbol] = quotes
        else:
            pending.append(symbol)

    if not pending:
        return result

    # 2. For each gateway, determine which pending symbols to include in the batch
    for source, gateway in gateways.items():
        # Partition pending symbols by fetch-log status
        to_fetch: list[str] = []
        for symbol in pending:
            if symbol in result:
                continue   # already filled by a previous gateway
            log = repository.load_option_fetch_log(symbol, source, trade_date)
            if log is not None and log.status not in _RETRYABLE:
                continue   # no_data / unsupported → skip
            to_fetch.append(symbol)

        if not to_fetch:
            continue

        # 3. Single batch call for all symbols that need fetching
        try:
            fetched_batch = _fetch_batch(gateway, to_fetch, eval_time)
        except Exception as exc:
            status = _classify_error(exc)
            msg = str(exc)[:500]
            for symbol in to_fetch:
                repository.save_option_fetch_log(OptionFetchLog(
                    symbol=symbol, source=source, trade_date=trade_date,
                    status=status, message=msg,
                ))
            continue

        # 4. Save per-symbol results
        for symbol in to_fetch:
            fetched = fetched_batch.get(symbol, [])
            if fetched:
                repository.save_option_quotes(fetched, source=source)
                repository.save_option_fetch_log(OptionFetchLog(
                    symbol=symbol, source=source, trade_date=trade_date,
                    status="success", quote_count=len(fetched),
                ))
                result[symbol] = fetched
            else:
                repository.save_option_fetch_log(OptionFetchLog(
                    symbol=symbol, source=source, trade_date=trade_date,
                    status="no_data", message="gateway returned empty list",
                ))

    # Ensure every requested symbol has an entry
    for symbol in symbols:
        result.setdefault(symbol, [])

    return result


def load_option_quotes(
    symbol: str,
    trade_date: str,
    start_utc: datetime,
    end_utc: datetime,
    repository: MarketDataRepository,
    gateways: dict[str, MarketDataGateway],
    eval_time: datetime,
) -> list[OptionQuote]:
    """Single-symbol wrapper around load_option_quotes_batch."""
    return load_option_quotes_batch(
        symbols=[symbol],
        trade_date=trade_date,
        start_utc=start_utc,
        end_utc=end_utc,
        repository=repository,
        gateways=gateways,
        eval_time=eval_time,
    ).get(symbol, [])
