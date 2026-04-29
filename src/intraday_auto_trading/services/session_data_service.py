"""Session metrics service: DB-first + gateway fallback + bar-derived fallback.

决策流程（每个 gateway 独立判断）：
  1. DB 已有 session_metrics → 直接使用，不再调 gateway
  2. 有 fetch_log 记录：
     - status in ("no_data", "unsupported") → 跳过（不重试）
     - status in ("permission", "api_error") → 重试 gateway
  3. 无记录 → 调 gateway
  4. Gateway 结果：
     - 有数据 → save_session_metrics + save_session_fetch_log(success)
     - None   → save_session_fetch_log(no_data)
     - 异常   → save_session_fetch_log(permission|api_error)，继续下一个
  5. 全部失败 → 从 1m bars 计算，source="computed_from_bars"，标记 fetch_status
"""
from __future__ import annotations

from datetime import datetime

from intraday_auto_trading.interfaces.brokers import MarketDataGateway
from intraday_auto_trading.interfaces.repositories import MarketDataRepository
from intraday_auto_trading.models import MinuteBar, SessionFetchLog, SessionMetrics
from intraday_auto_trading.services.bar_metrics import derive_session_metrics_from_minute_bars

_RETRYABLE = {"permission", "api_error"}


def _classify_error(exc: Exception) -> str:
    msg = str(exc).lower()
    if "not logged in" in msg or "session" in msg or "login" in msg:
        return "permission"
    return "api_error"


def _derive_from_bars(
    symbol: str,
    eval_time: datetime,
    bars: list[MinuteBar],
) -> SessionMetrics:
    """Compute session metrics from 1m bars as fallback."""
    return derive_session_metrics_from_minute_bars(symbol, eval_time, bars)


def load_session_metrics(
    symbol: str,
    trade_date: str,        # "YYYY-MM-DD"
    eval_time: datetime,    # used for DB query and gateway call
    repository: MarketDataRepository,
    gateways: dict[str, MarketDataGateway],
    bars: list[MinuteBar],  # used as last-resort fallback
) -> SessionMetrics:
    """Load session metrics with DB-first caching, gateway fallback, and bar derivation.

    Returns a SessionMetrics object. source="computed_from_bars" when all gateways fail
    and metrics are derived from the provided 1m bars.

    Raises RuntimeError only when all gateways fail AND no bars are available.
    """
    # 1. DB hit — return immediately
    db_metrics = repository.load_session_metrics(symbol, eval_time)
    if db_metrics is not None:
        # Fill any None fields from bars
        official_open = db_metrics.official_open or (bars[0].open if bars else None)
        last_price = db_metrics.last_price or (bars[-1].close if bars else None)
        session_vwap = db_metrics.session_vwap or (bars[-1].close if bars else None)
        return SessionMetrics(
            symbol=symbol,
            timestamp=eval_time,
            source=db_metrics.source,
            official_open=official_open,
            last_price=last_price,
            session_vwap=session_vwap,
        )

    # 2. Try each gateway with fetch-log decision
    for source, gateway in gateways.items():
        log = repository.load_session_fetch_log(symbol, source, trade_date)
        if log is not None and log.status not in _RETRYABLE:
            continue   # no_data / unsupported → skip

        try:
            fetched = gateway.get_session_metrics(symbol, eval_time)
        except Exception as exc:
            status = _classify_error(exc)
            repository.save_session_fetch_log(SessionFetchLog(
                symbol=symbol, source=source, trade_date=trade_date,
                status=status, message=str(exc)[:500],
            ))
            continue

        if fetched is not None:
            repository.save_session_metrics(fetched)
            repository.save_session_fetch_log(SessionFetchLog(
                symbol=symbol, source=source, trade_date=trade_date,
                status="success",
            ))
            return fetched
        else:
            repository.save_session_fetch_log(SessionFetchLog(
                symbol=symbol, source=source, trade_date=trade_date,
                status="no_data", message="gateway returned None",
            ))

    # 3. All gateways failed — derive from bars
    if bars:
        derived = _derive_from_bars(symbol, eval_time, bars)
        repository.save_session_metrics(derived)
        # Record that we fell back to bars for each gateway so we don't retry next time
        for source in gateways:
            log = repository.load_session_fetch_log(symbol, source, trade_date)
            if log is None:
                repository.save_session_fetch_log(SessionFetchLog(
                    symbol=symbol, source=source, trade_date=trade_date,
                    status="no_data", message="no gateway available; derived from bars",
                ))
        return derived

    raise RuntimeError(
        f"No session metrics for {symbol} at {eval_time} from any gateway and no bars to derive from"
    )
