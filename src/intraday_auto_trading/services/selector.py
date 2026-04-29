from __future__ import annotations

from datetime import datetime

from intraday_auto_trading.models import (
    Dev20SignalResult,
    IntradayOrderDecision,
)


class SymbolSelector:
    """Select which symbol to trade based on intraday dev20 signals.

    Selection logic (per bar):
    1. Exit check: if active order is already filled → action="exit"
    2. Force-buy check: if current_time >= force_buy_time → action="force_buy" (stub)
    3. Buy-signal comparison:
       - Find all symbols with signal="buy_now"
       - Compute dev20_w = dev20 * _decay_fn(completed_orders) for each candidate
       - Select candidate with highest dev20_w
       - Compare against all symbols' current dev20_w AND active_order's dev20_w_at_order
       - If best candidate is the global maximum → action="place_order"
       - Otherwise → action="wait"
    """

    @staticmethod
    def _decay_fn(completed_orders: int) -> float:
        """Decay multiplier applied to dev20 based on completed orders this week.

        Interface reserved for future customization. Default: identity (returns 1.0).

        Parameters
        ----------
        completed_orders:
            Number of orders completed for this symbol in the current week.
        """
        return 1.0

    def select(
        self,
        intraday_signals: dict[str, Dev20SignalResult],
        active_order: tuple[str, float] | None = None,
        completed_orders: dict[str, int] | None = None,
        order_filled: bool = False,
        current_time: datetime | None = None,
        force_buy_time: datetime | None = None,
    ) -> IntradayOrderDecision:
        """Evaluate per-bar tracking state and return the next action.

        Parameters
        ----------
        intraday_signals:
            Mapping of symbol → Dev20SignalResult for the current bar.
        active_order:
            (symbol, dev20_w_at_order) of the currently open order, or None.
            dev20_w_at_order is the weighted dev20 recorded when that order was placed.
        completed_orders:
            Mapping of symbol → completed order count this week, used by _decay_fn.
        order_filled:
            True if the active order has been confirmed as filled (exit tracking).
        current_time:
            Timestamp of the current bar (used for force-buy window check).
        force_buy_time:
            Deadline: if current_time >= force_buy_time, trigger force-buy interface.

        Returns
        -------
        IntradayOrderDecision with action in {"wait", "place_order", "exit", "force_buy"}.
        """
        orders = completed_orders or {}

        # 1. Exit: active order filled
        if order_filled:
            return IntradayOrderDecision(
                action="exit",
                rationale="active order filled; exiting 1m tracking",
            )

        # 2. Force-buy window (interface only — execution stub in ExecutionPlanner)
        if current_time is not None and force_buy_time is not None and current_time >= force_buy_time:
            return IntradayOrderDecision(
                action="force_buy",
                rationale="force buy window reached",
            )

        # 3. Buy-signal comparison
        buy_candidates = {
            sym: sig
            for sym, sig in intraday_signals.items()
            if sig.signal == "buy_now" and sig.dev20 is not None
        }

        if not buy_candidates:
            return IntradayOrderDecision(action="wait")

        # Weighted dev20 for each buy candidate
        candidate_dev20_w: dict[str, float] = {
            sym: sig.dev20 * self._decay_fn(orders.get(sym, 0))  # type: ignore[operator]
            for sym, sig in buy_candidates.items()
        }

        best_sym = max(candidate_dev20_w, key=lambda s: candidate_dev20_w[s])
        best_dev20_w = candidate_dev20_w[best_sym]

        # Build comparison set: all symbols' current dev20_w + active order's dev20_w
        comparison: list[float] = [
            sig.dev20 * self._decay_fn(orders.get(sym, 0))
            for sym, sig in intraday_signals.items()
            if sig.dev20 is not None
        ]
        if active_order is not None:
            comparison.append(active_order[1])

        if not comparison or best_dev20_w < max(comparison):
            return IntradayOrderDecision(action="wait")

        return IntradayOrderDecision(
            action="place_order",
            symbol=best_sym,
            limit_price=buy_candidates[best_sym].limit_price,
            dev20_at_order=best_dev20_w,
            cancel_symbol=active_order[0] if active_order is not None else None,
            rationale=(
                f"buy_now on {best_sym}; "
                f"dev20_w={best_dev20_w:.6f} is global max "
                f"(candidates={list(candidate_dev20_w.keys())})"
            ),
        )
