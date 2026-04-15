from __future__ import annotations

from intraday_auto_trading.models import Regime, TrendInput, TrendSignal


class TrendClassifier:
    """启发式占位实现，后续可替换为更完整的量化模型。"""

    def classify(self, payload: TrendInput) -> TrendSignal:
        if not payload.minute_bars:
            raise ValueError("minute_bars must not be empty")

        first_close = payload.minute_bars[0].close
        last_close = payload.minute_bars[-1].close
        day_low = min(bar.low for bar in payload.minute_bars)
        distance_from_low = 0.0 if day_low == 0 else (last_close - day_low) / day_low
        open_change = (payload.last_price - payload.official_open) / payload.official_open
        vwap_change = (payload.last_price - payload.session_vwap) / payload.session_vwap

        if open_change >= 0.003 and vwap_change >= 0.0 and last_close >= first_close:
            return TrendSignal(
                symbol=payload.symbol,
                eval_time=payload.eval_time,
                regime=Regime.EARLY_BUY,
                score=0.8 + open_change,
                reason="价格站上开盘价和 VWAP，开盘后呈强势抬升。",
            )

        if open_change <= -0.004 or (vwap_change < 0 and distance_from_low <= 0.0025):
            return TrendSignal(
                symbol=payload.symbol,
                eval_time=payload.eval_time,
                regime=Regime.WEAK_TAIL,
                score=0.7 + abs(open_change),
                reason="价格弱于开盘价或贴近日内低点，更适合低位追踪。",
            )

        return TrendSignal(
            symbol=payload.symbol,
            eval_time=payload.eval_time,
            regime=Regime.RANGE_TRACK_15M,
            score=0.6 + max(vwap_change, -0.02),
            reason="价格处于区间整理，建议进入 15 分钟反弹确认流程。",
        )

