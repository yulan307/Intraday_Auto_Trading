from __future__ import annotations

from intraday_auto_trading.models import TrackingDecision


class FifteenMinuteTracker:
    def __init__(self, confirmation_bars: int, limit_price_factor: float) -> None:
        if confirmation_bars < 1:
            raise ValueError("confirmation_bars must be >= 1")
        if limit_price_factor <= 1:
            raise ValueError("limit_price_factor must be > 1")

        self.confirmation_bars = confirmation_bars
        self.limit_price_factor = limit_price_factor
        self.lowest_close: float | None = None
        self.bars_since_low = 0

    def observe(self, close_price: float) -> TrackingDecision:
        if self.lowest_close is None or close_price < self.lowest_close:
            self.lowest_close = close_price
            self.bars_since_low = 0
            return TrackingDecision(
                should_place_order=False,
                should_cancel_order=True,
                limit_price=None,
                lowest_close=close_price,
                message="发现新的最低 close，撤销旧限价单并继续跟踪。",
            )

        self.bars_since_low += 1
        if self.bars_since_low >= self.confirmation_bars:
            limit_price = round(self.lowest_close * self.limit_price_factor, 2)
            return TrackingDecision(
                should_place_order=True,
                should_cancel_order=False,
                limit_price=limit_price,
                lowest_close=self.lowest_close,
                message="连续多个 15m bar 未创新低，满足反弹确认，准备挂限价单。",
            )

        return TrackingDecision(
            should_place_order=False,
            should_cancel_order=False,
            limit_price=None,
            lowest_close=self.lowest_close,
            message="仍在观察是否确认反弹。",
        )

    def force_buy_price(self) -> float:
        if self.lowest_close is None:
            raise ValueError("lowest_close is not initialized")
        return round(self.lowest_close * self.limit_price_factor, 2)

