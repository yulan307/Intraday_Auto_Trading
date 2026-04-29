from __future__ import annotations

from typing import Sequence

from intraday_auto_trading.models import BuyStrategy, MinuteBar, OrderInstruction, SelectionResult


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
            rationale="dev20 buy_now signal; submit limit order at (prev.low + prev.close) / 2.",
        )

    def build_force_order(self, symbol: str, quantity: int, limit_price: float) -> OrderInstruction:
        return OrderInstruction(
            symbol=symbol,
            strategy=BuyStrategy.FORCE_BUY,
            quantity=quantity,
            limit_price=limit_price,
            rationale="The final buy window has started; submit the force-buy limit order.",
        )

    def build_vwap_early_buy_order(
        self,
        symbol: str,
        quantity: int,
        vwap: float,
        dev20_w: float,
    ) -> OrderInstruction:
        """Build initial order when all symbols are EARLY_BUY with negative dev20_w.

        Limit price is set to VWAP at classify time.
        """
        return OrderInstruction(
            symbol=symbol,
            strategy=BuyStrategy.IMMEDIATE_BUY,
            quantity=quantity,
            limit_price=round(vwap, 2),
            rationale=(
                f"All EARLY_BUY + dev20_w={dev20_w:.6f} < 0; "
                f"initial order at VWAP={vwap:.2f}"
            ),
        )

    def force_buy(
        self,
        symbol: str,
        quantity: int,
        bars: Sequence[MinuteBar],
        current_idx: int,
    ) -> OrderInstruction | None:
        """Force buy at market close. Interface reserved; not yet implemented.

        Returns None until implemented.
        """
        return None
