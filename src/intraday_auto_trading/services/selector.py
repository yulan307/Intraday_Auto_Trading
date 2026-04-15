from __future__ import annotations

from intraday_auto_trading.config import SelectionSettings
from intraday_auto_trading.models import (
    AccountSymbolState,
    BuyStrategy,
    Regime,
    SelectionResult,
    TrendSignal,
)


class SymbolSelector:
    def __init__(self, settings: SelectionSettings) -> None:
        self.settings = settings

    def select(
        self,
        signals: list[TrendSignal],
        account_states: dict[str, AccountSymbolState],
    ) -> SelectionResult:
        if not signals:
            raise ValueError("signals must not be empty")

        ranked = sorted(
            (
                self._score_signal(signal, account_states.get(signal.symbol))
                for signal in signals
            ),
            key=lambda item: item.ranking_score,
            reverse=True,
        )
        return ranked[0]

    def _score_signal(
        self,
        signal: TrendSignal,
        account_state: AccountSymbolState | None,
    ) -> SelectionResult:
        trend_score = {
            Regime.WEAK_TAIL: self.settings.weak_tail_weight,
            Regime.RANGE_TRACK_15M: self.settings.range_track_weight,
            Regime.EARLY_BUY: self.settings.early_buy_weight,
        }[signal.regime]

        orders = account_state.completed_orders_this_week if account_state else 0
        has_position = account_state.has_position if account_state else False
        ownership_score = 0.0 if has_position else self.settings.unbought_bonus
        frequency_penalty = orders * self.settings.recent_fill_penalty_step
        ranking_score = trend_score + ownership_score - frequency_penalty + signal.score

        strategy = (
            BuyStrategy.IMMEDIATE_BUY
            if signal.regime is Regime.EARLY_BUY
            else BuyStrategy.TRACKING_BUY
        )
        rationale = (
            f"{signal.reason} "
            f"趋势权重={trend_score:.2f}, 未持仓加分={ownership_score:.2f}, 周内订单惩罚={frequency_penalty:.2f}。"
        )
        return SelectionResult(
            symbol=signal.symbol,
            regime=signal.regime,
            strategy=strategy,
            ranking_score=ranking_score,
            rationale=rationale,
        )

