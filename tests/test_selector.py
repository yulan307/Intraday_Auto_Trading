from datetime import datetime

from intraday_auto_trading.config import SelectionSettings
from intraday_auto_trading.models import AccountSymbolState, Regime, TrendSignal
from intraday_auto_trading.services.selector import SymbolSelector


def test_selector_prefers_unbought_weak_tail_symbol() -> None:
    selector = SymbolSelector(
        SelectionSettings(
            weak_tail_weight=3.0,
            range_track_weight=2.0,
            early_buy_weight=1.0,
            unbought_bonus=2.0,
            recent_fill_penalty_step=0.5,
        )
    )

    signals = [
        TrendSignal(
            symbol="SPY",
            eval_time=datetime(2026, 4, 15, 10, 0),
            regime=Regime.EARLY_BUY,
            score=0.9,
            reason="强势开盘",
        ),
        TrendSignal(
            symbol="QQQ",
            eval_time=datetime(2026, 4, 15, 10, 0),
            regime=Regime.WEAK_TAIL,
            score=0.8,
            reason="弱势拖尾",
        ),
    ]
    account_states = {
        "SPY": AccountSymbolState(symbol="SPY", completed_orders_this_week=2, has_position=True),
        "QQQ": AccountSymbolState(symbol="QQQ", completed_orders_this_week=0, has_position=False),
    }

    result = selector.select(signals, account_states)

    assert result.symbol == "QQQ"
    assert result.strategy.value == "TRACKING_BUY"

