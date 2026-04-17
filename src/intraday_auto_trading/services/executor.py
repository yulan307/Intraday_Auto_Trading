from __future__ import annotations

from intraday_auto_trading.models import BuyStrategy, OrderInstruction, SelectionResult


class ExecutionPlanner:
    def build_initial_order(self, selection: SelectionResult, quantity: int) -> OrderInstruction:
        return OrderInstruction(
            symbol=selection.symbol,
            strategy=selection.strategy,
            quantity=quantity,
            rationale=selection.rationale,
        )

    def build_tracking_order(self, symbol: str, quantity: int, limit_price: float) -> OrderInstruction:
        return OrderInstruction(
            symbol=symbol,
            strategy=BuyStrategy.TRACKING_BUY,
            quantity=quantity,
            limit_price=limit_price,
            rationale="Tracking confirmation completed on the 15-minute chart; submit a limit order near the session low.",
        )

    def build_force_order(self, symbol: str, quantity: int, limit_price: float) -> OrderInstruction:
        return OrderInstruction(
            symbol=symbol,
            strategy=BuyStrategy.FORCE_BUY,
            quantity=quantity,
            limit_price=limit_price,
            rationale="The final buy window has started; submit the force-buy limit order.",
        )
