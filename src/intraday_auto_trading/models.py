from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Regime(str, Enum):
    EARLY_BUY = "EARLY_BUY"
    RANGE_TRACK_15M = "RANGE_TRACK_15M"
    WEAK_TAIL = "WEAK_TAIL"


class BuyStrategy(str, Enum):
    IMMEDIATE_BUY = "IMMEDIATE_BUY"
    TRACKING_BUY = "TRACKING_BUY"
    FORCE_BUY = "FORCE_BUY"


@dataclass(slots=True)
class MinuteBar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(slots=True)
class SymbolInfo:
    symbol: str
    name: str | None = None
    exchange: str | None = None
    asset_type: str | None = None
    currency: str = "USD"
    is_active: bool = True


@dataclass(slots=True)
class SessionMetrics:
    symbol: str
    timestamp: datetime
    source: str
    official_open: float | None = None
    last_price: float | None = None
    session_vwap: float | None = None


@dataclass(slots=True)
class OpeningImbalance:
    symbol: str
    trade_date: str
    source: str
    opening_imbalance_side: str | None = None
    opening_imbalance_qty: float | None = None
    paired_shares: float | None = None
    indicative_open_price: float | None = None


@dataclass(slots=True)
class OptionQuote:
    symbol: str
    strike: float
    side: str
    bid: float
    ask: float
    bid_size: int = 0
    ask_size: int = 0
    last: float = 0.0
    volume: int = 0
    iv: float | None = None
    delta: float | None = None
    gamma: float | None = None
    contract_id: str | None = None
    expiry: str | None = None
    exchange: str | None = None
    multiplier: int | None = None
    snapshot_time: datetime | None = None


@dataclass(slots=True)
class TrendInput:
    symbol: str
    eval_time: datetime
    official_open: float
    last_price: float
    session_vwap: float
    minute_bars: list[MinuteBar]
    option_quotes: list[OptionQuote] = field(default_factory=list)


@dataclass(slots=True)
class TrendSignal:
    symbol: str
    eval_time: datetime
    regime: Regime
    score: float
    reason: str


@dataclass(slots=True)
class TrendSnapshot:
    symbol: str
    eval_time: datetime
    source: str
    regime: Regime
    score: float
    reason: str
    official_open: float | None = None
    last_price: float | None = None
    session_vwap: float | None = None


@dataclass(slots=True)
class AccountSymbolState:
    symbol: str
    completed_orders_this_week: int = 0
    has_position: bool = False


@dataclass(slots=True)
class SelectionResult:
    symbol: str
    regime: Regime
    strategy: BuyStrategy
    ranking_score: float
    rationale: str


@dataclass(slots=True)
class OrderInstruction:
    symbol: str
    strategy: BuyStrategy
    quantity: int
    limit_price: float | None = None
    rationale: str = ""


@dataclass(slots=True)
class TrackingDecision:
    should_place_order: bool
    should_cancel_order: bool
    limit_price: float | None
    lowest_close: float
    message: str
