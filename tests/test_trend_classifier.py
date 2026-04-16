"""Unit tests for TrendClassifier v2（量化评分模型）。"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from intraday_auto_trading.models import MinuteBar, OptionQuote, Regime, TrendInput
from intraday_auto_trading.services.trend_classifier import TrendClassifier

NOW = datetime(2026, 4, 16, 9, 35, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def make_bars(closes: list[float], base: float = 100.0) -> list[MinuteBar]:
    """生成 1 分钟 bar 列表，方便测试场景构造。"""
    bars = []
    for i, close in enumerate(closes):
        ts = NOW.replace(minute=30 + i)
        bars.append(
            MinuteBar(
                timestamp=ts,
                open=base,
                high=close + 0.1,
                low=close - 0.1,
                close=close,
                volume=1000.0,
            )
        )
    return bars


def make_option_quote(
    strike: float,
    side: str,
    iv: float | None,
    delta: float | None,
    snapshot_time: datetime | None = None,
) -> OptionQuote:
    snapshot_time = snapshot_time or NOW
    return OptionQuote(
        symbol="SPY",
        strike=strike,
        side=side,
        bid=1.0,
        ask=1.2,
        iv=iv,
        delta=delta,
        snapshot_time=snapshot_time,
    )


def make_input(
    last_price: float,
    official_open: float,
    session_vwap: float,
    closes: list[float],
    option_quotes: list[OptionQuote] | None = None,
) -> TrendInput:
    return TrendInput(
        symbol="SPY",
        eval_time=NOW,
        official_open=official_open,
        last_price=last_price,
        session_vwap=session_vwap,
        minute_bars=make_bars(closes, base=official_open),
        option_quotes=option_quotes or [],
    )


clf = TrendClassifier()


# ---------------------------------------------------------------------------
# 基础断言：无 option_quotes 时纯价格信号驱动
# ---------------------------------------------------------------------------


def test_strong_uptrend_no_options_gives_early_buy() -> None:
    """价格明显高于开盘价 + VWAP，且 bar 持续抬升 → EARLY_BUY。"""
    signal = clf.classify(
        make_input(
            last_price=101.0,
            official_open=100.0,
            session_vwap=100.2,
            closes=[100.2, 100.5, 100.8, 101.0],
        )
    )
    assert signal.regime == Regime.EARLY_BUY
    assert signal.score > 0.75


def test_weak_downtrend_no_options_gives_weak_tail() -> None:
    """价格明显低于开盘价 + VWAP，且 bar 持续下行 → WEAK_TAIL。"""
    signal = clf.classify(
        make_input(
            last_price=99.5,
            official_open=100.0,
            session_vwap=100.1,
            closes=[99.9, 99.7, 99.5, 99.5],
        )
    )
    assert signal.regime == Regime.WEAK_TAIL
    assert signal.score > 0.70


def test_neutral_gives_range_track() -> None:
    """价格贴近开盘价，VWAP 相差极小 → RANGE_TRACK_15M。"""
    signal = clf.classify(
        make_input(
            last_price=100.01,
            official_open=100.0,
            session_vwap=100.01,
            closes=[100.0, 100.01, 99.99, 100.01],
        )
    )
    assert signal.regime == Regime.RANGE_TRACK_15M


# ---------------------------------------------------------------------------
# 期权信号增强场景
# ---------------------------------------------------------------------------

T_OPEN = datetime(2026, 4, 16, 9, 30, tzinfo=timezone.utc)
T_NOW = datetime(2026, 4, 16, 9, 35, tzinfo=timezone.utc)


def make_atm_options(
    call_iv: float,
    put_iv: float,
    call_delta: float,
    put_delta: float,
    snapshot_time: datetime,
    last_price: float = 100.0,
) -> list[OptionQuote]:
    strike = round(last_price)
    return [
        make_option_quote(strike, "CALL", call_iv, call_delta, snapshot_time),
        make_option_quote(strike, "PUT", put_iv, -put_delta, snapshot_time),
    ]


def test_bullish_options_confirm_early_buy() -> None:
    """价格略偏强 + 期权 call IV > put IV，delta 偏多 → 期权信号应加强 EARLY_BUY。"""
    open_opts = make_atm_options(0.20, 0.22, 0.48, 0.48, T_OPEN)
    now_opts = make_atm_options(0.22, 0.20, 0.55, 0.42, T_NOW)  # call IV 超过 put IV，call delta 偏高
    signal = clf.classify(
        make_input(
            last_price=100.6,
            official_open=100.0,
            session_vwap=100.1,
            closes=[100.1, 100.3, 100.5, 100.6],
            option_quotes=open_opts + now_opts,
        )
    )
    assert signal.regime == Regime.EARLY_BUY


def test_bearish_options_confirm_weak_tail() -> None:
    """价格略偏弱 + 期权 put IV > call IV，delta 偏空 → 期权信号应加强 WEAK_TAIL。"""
    open_opts = make_atm_options(0.22, 0.22, 0.48, 0.48, T_OPEN)
    now_opts = make_atm_options(0.20, 0.26, 0.42, 0.55, T_NOW)  # put IV 高，put delta 高
    signal = clf.classify(
        make_input(
            last_price=99.5,
            official_open=100.0,
            session_vwap=99.9,
            closes=[99.9, 99.7, 99.5, 99.5],
            option_quotes=open_opts + now_opts,
        )
    )
    assert signal.regime == Regime.WEAK_TAIL


def test_options_without_iv_fallback_to_mid_price() -> None:
    """iv 和 delta 均为 None，退化到 call/put mid 差值；不应 raise。"""
    opts = [
        make_option_quote(100, "CALL", iv=None, delta=None, snapshot_time=T_NOW),
        make_option_quote(100, "PUT", iv=None, delta=None, snapshot_time=T_NOW),
    ]
    signal = clf.classify(
        make_input(
            last_price=100.0,
            official_open=100.0,
            session_vwap=100.0,
            closes=[100.0, 100.0],
            option_quotes=opts,
        )
    )
    assert signal.regime in (Regime.EARLY_BUY, Regime.RANGE_TRACK_15M, Regime.WEAK_TAIL)


def test_empty_option_quotes_uses_price_only() -> None:
    """option_quotes=[] → 完全依赖价格信号，不应 raise。"""
    signal = clf.classify(
        make_input(
            last_price=101.0,
            official_open=100.0,
            session_vwap=100.2,
            closes=[100.2, 100.5, 101.0],
            option_quotes=[],
        )
    )
    assert signal.regime == Regime.EARLY_BUY


# ---------------------------------------------------------------------------
# 边界场景
# ---------------------------------------------------------------------------


def test_single_bar_does_not_raise() -> None:
    """只有 1 根 bar，bar_slope 退化为 0，不应 raise。"""
    signal = clf.classify(
        make_input(
            last_price=100.0,
            official_open=100.0,
            session_vwap=100.0,
            closes=[100.0],
        )
    )
    assert signal.regime in (Regime.EARLY_BUY, Regime.RANGE_TRACK_15M, Regime.WEAK_TAIL)


def test_empty_bars_raises() -> None:
    with pytest.raises(ValueError, match="minute_bars must not be empty"):
        clf.classify(
            TrendInput(
                symbol="SPY",
                eval_time=NOW,
                official_open=100.0,
                last_price=100.0,
                session_vwap=100.0,
                minute_bars=[],
            )
        )


def test_price_equals_open_neutral() -> None:
    """价格等于开盘价，VWAP 也等于开盘价 → 应落在 RANGE_TRACK_15M。"""
    signal = clf.classify(
        make_input(
            last_price=100.0,
            official_open=100.0,
            session_vwap=100.0,
            closes=[100.0, 100.0, 100.0],
        )
    )
    assert signal.regime == Regime.RANGE_TRACK_15M


def test_score_bounds() -> None:
    """score 应在合理范围内（不超过 [0, 1.5]）。"""
    signal = clf.classify(
        make_input(
            last_price=102.0,
            official_open=100.0,
            session_vwap=100.5,
            closes=[100.5, 101.0, 101.5, 102.0],
        )
    )
    assert 0.0 <= signal.score <= 1.5


def test_reason_contains_regime_label() -> None:
    """reason 字段应包含得分信息（非空）。"""
    signal = clf.classify(
        make_input(
            last_price=100.0,
            official_open=100.0,
            session_vwap=100.0,
            closes=[100.0, 100.0],
        )
    )
    assert signal.reason
    assert signal.symbol == "SPY"
