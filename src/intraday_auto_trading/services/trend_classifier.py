from __future__ import annotations

from datetime import datetime

from intraday_auto_trading.models import MinuteBar, OptionQuote, Regime, TrendInput, TrendSignal


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _safe_mean(values: list[float]) -> float | None:
    filtered = [v for v in values if v is not None]
    return sum(filtered) / len(filtered) if filtered else None


class TrendClassifier:
    """基于开盘 bar + 期权数据的量化趋势分类器。

    两路信号：
      - price_score  [-1, +1]：open_change、vwap_bias、bar_slope、range_position
      - option_score [-1, +1]：IV skew、IV skew 变化、delta bias、IV 绝对水平变化

    合并规则：
      - 无期权数据 → composite = price_score
      - 首 bar 放量(vol_surge > 2) → 0.7 * price + 0.3 * option
      - 其他 → 0.6 * price + 0.4 * option

    阈值：
      -  composite >= 0.25 → EARLY_BUY
      - composite <= -0.20 → WEAK_TAIL
      - 其他 → RANGE_TRACK_15M
    """

    def classify(self, payload: TrendInput) -> TrendSignal:
        if not payload.minute_bars:
            raise ValueError("minute_bars must not be empty")

        price_score, price_detail = self._compute_price_score(payload)
        option_score, option_detail = self._compute_option_score(payload.option_quotes, payload.last_price)

        vol_surge = self._vol_surge(payload.minute_bars)

        if option_score is None:
            composite = price_score
            weight_label = "price_only"
        elif vol_surge > 2:
            composite = 0.7 * price_score + 0.3 * option_score
            weight_label = "vol_surge"
        else:
            composite = 0.6 * price_score + 0.4 * option_score
            weight_label = "balanced"

        if composite >= 0.25:
            regime = Regime.EARLY_BUY
            score = 0.75 + composite * 0.25
            reason = (
                f"综合得分 {composite:.3f}（{weight_label}）偏强；"
                f"价格信号 {price_score:.3f}（{price_detail}）"
                + (f"，期权信号 {option_score:.3f}（{option_detail}）" if option_score is not None else "")
            )
        elif composite <= -0.20:
            regime = Regime.WEAK_TAIL
            score = 0.70 + abs(composite) * 0.25
            reason = (
                f"综合得分 {composite:.3f}（{weight_label}）偏弱；"
                f"价格信号 {price_score:.3f}（{price_detail}）"
                + (f"，期权信号 {option_score:.3f}（{option_detail}）" if option_score is not None else "")
            )
        else:
            regime = Regime.RANGE_TRACK_15M
            score = 0.60 + composite * 0.15
            reason = (
                f"综合得分 {composite:.3f}（{weight_label}）中性区间；"
                f"价格信号 {price_score:.3f}（{price_detail}）"
                + (f"，期权信号 {option_score:.3f}（{option_detail}）" if option_score is not None else "")
            )

        return TrendSignal(
            symbol=payload.symbol,
            eval_time=payload.eval_time,
            regime=regime,
            score=score,
            reason=reason,
        )

    # ------------------------------------------------------------------
    # 价格信号
    # ------------------------------------------------------------------

    def _compute_price_score(self, payload: TrendInput) -> tuple[float, str]:
        bars = payload.minute_bars
        official_open = payload.official_open
        last_price = payload.last_price
        session_vwap = payload.session_vwap

        open_change = (last_price - official_open) / official_open if official_open else 0.0
        vwap_bias = (last_price - session_vwap) / session_vwap if session_vwap else 0.0

        bar_slope = self._bar_slope(bars, official_open)

        day_low = min(b.low for b in bars)
        day_high = max(b.high for b in bars)
        rng = day_high - day_low
        range_position = (last_price - day_low) / rng if rng > 0 else 0.5

        score = (
            0.35 * _clip(open_change / 0.008, -1.0, 1.0)
            + 0.30 * _clip(vwap_bias / 0.005, -1.0, 1.0)
            + 0.20 * _clip(bar_slope / 0.001, -1.0, 1.0)
            + 0.15 * (range_position * 2 - 1.0)
        )

        detail = (
            f"open_chg={open_change:.4f} vwap_bias={vwap_bias:.4f} "
            f"slope={bar_slope:.5f} range_pos={range_position:.2f}"
        )
        return _clip(score, -1.0, 1.0), detail

    @staticmethod
    def _bar_slope(bars: list[MinuteBar], reference: float) -> float:
        """最近 3 根（或更少）bar close 的每 bar 平均变化，除以 reference 做归一化。"""
        recent = bars[-3:] if len(bars) >= 3 else bars
        if len(recent) < 2:
            return 0.0
        total_change = recent[-1].close - recent[0].close
        steps = len(recent) - 1
        return (total_change / steps / reference) if reference else 0.0

    @staticmethod
    def _vol_surge(bars: list[MinuteBar]) -> float:
        """首根 bar 的量能相对后续 bar 均量的倍数。"""
        if len(bars) < 2:
            return 1.0
        remaining_volumes = [b.volume for b in bars[1:] if b.volume > 0]
        if not remaining_volumes:
            return 1.0
        avg_rest = sum(remaining_volumes) / len(remaining_volumes)
        return bars[0].volume / avg_rest if avg_rest > 0 else 1.0

    # ------------------------------------------------------------------
    # 期权信号
    # ------------------------------------------------------------------

    def _compute_option_score(
        self, quotes: list[OptionQuote], last_price: float
    ) -> tuple[float | None, str]:
        if not quotes:
            return None, "no_options"

        # 按 snapshot_time 分组，取最早和最新两个时间点
        ts_groups: dict[datetime | None, list[OptionQuote]] = {}
        for q in quotes:
            ts_groups.setdefault(q.snapshot_time, []).append(q)

        sorted_ts = sorted(ts_groups.keys(), key=lambda t: (t is None, t))
        if len(sorted_ts) < 2:
            t_open_quotes = sorted_ts[0] and ts_groups[sorted_ts[0]] or quotes
            t_now_quotes = t_open_quotes
        else:
            t_open_quotes = ts_groups[sorted_ts[0]]
            t_now_quotes = ts_groups[sorted_ts[-1]]

        iv_skew_open = self._iv_skew(t_open_quotes, last_price)
        iv_skew_now = self._iv_skew(t_now_quotes, last_price)
        delta_bias_now = self._delta_bias(t_now_quotes, last_price)
        iv_level_open = self._mean_iv(t_open_quotes, last_price)
        iv_level_now = self._mean_iv(t_now_quotes, last_price)

        if iv_skew_now is None and delta_bias_now is None:
            # 退化：仅用 call/put mid 差
            mid_score = self._mid_price_score(t_now_quotes, last_price)
            if mid_score is None:
                return None, "no_iv_no_mid"
            return _clip(mid_score, -1.0, 1.0), "mid_fallback"

        iv_skew_change = (
            (iv_skew_now - iv_skew_open)
            if iv_skew_now is not None and iv_skew_open is not None
            else 0.0
        )
        iv_level_change = (
            (iv_level_now - iv_level_open) / iv_level_open
            if iv_level_now is not None and iv_level_open is not None and iv_level_open > 0
            else 0.0
        )

        score = (
            0.45 * _clip((iv_skew_now or 0.0) / 0.05, -1.0, 1.0)
            + 0.25 * _clip(iv_skew_change / 0.03, -1.0, 1.0)
            + 0.20 * _clip((delta_bias_now or 0.0) / 0.10, -1.0, 1.0)
            + 0.10 * (-1.0) * _clip(iv_level_change / 0.20, -1.0, 1.0)
        )

        detail = (
            f"iv_skew={iv_skew_now:.4f} iv_skew_chg={iv_skew_change:.4f} "
            f"delta_bias={delta_bias_now:.4f} iv_lvl_chg={iv_level_change:.4f}"
            if iv_skew_now is not None and delta_bias_now is not None
            else f"iv_skew={iv_skew_now} delta_bias={delta_bias_now}"
        )
        return _clip(score, -1.0, 1.0), detail

    @staticmethod
    def _atm_quotes(quotes: list[OptionQuote], last_price: float) -> list[OptionQuote]:
        """返回距当前价最近 3 个 strike 的 quotes（ATM ±1 档）。"""
        strikes = sorted({q.strike for q in quotes})
        if not strikes:
            return quotes
        # 找到最近 strike
        atm = min(strikes, key=lambda s: abs(s - last_price))
        atm_idx = strikes.index(atm)
        selected = set(strikes[max(0, atm_idx - 1): atm_idx + 2])
        return [q for q in quotes if q.strike in selected]

    def _iv_skew(self, quotes: list[OptionQuote], last_price: float) -> float | None:
        atm = self._atm_quotes(quotes, last_price)
        call_ivs = [q.iv for q in atm if q.side.upper() == "CALL" and q.iv is not None]
        put_ivs = [q.iv for q in atm if q.side.upper() == "PUT" and q.iv is not None]
        call_mean = _safe_mean(call_ivs)
        put_mean = _safe_mean(put_ivs)
        if call_mean is None or put_mean is None:
            return None
        return call_mean - put_mean

    def _delta_bias(self, quotes: list[OptionQuote], last_price: float) -> float | None:
        """call_delta_mean - (1 - abs(put_delta_mean))；接近 0 表示中性。"""
        atm = self._atm_quotes(quotes, last_price)
        call_deltas = [q.delta for q in atm if q.side.upper() == "CALL" and q.delta is not None]
        put_deltas = [abs(q.delta) for q in atm if q.side.upper() == "PUT" and q.delta is not None]
        call_mean = _safe_mean(call_deltas)
        put_mean = _safe_mean(put_deltas)
        if call_mean is None or put_mean is None:
            return None
        return call_mean - (1.0 - put_mean)

    def _mean_iv(self, quotes: list[OptionQuote], last_price: float) -> float | None:
        atm = self._atm_quotes(quotes, last_price)
        ivs = [q.iv for q in atm if q.iv is not None]
        return _safe_mean(ivs)

    @staticmethod
    def _mid_price_score(quotes: list[OptionQuote], last_price: float) -> float | None:
        """当 iv/delta 不可用时，用 call_mid - put_mid 差值作为方向代理。"""
        call_mids = [(q.bid + q.ask) / 2 for q in quotes if q.side.upper() == "CALL" and q.ask > 0]
        put_mids = [(q.bid + q.ask) / 2 for q in quotes if q.side.upper() == "PUT" and q.ask > 0]
        if not call_mids or not put_mids:
            return None
        diff = sum(call_mids) / len(call_mids) - sum(put_mids) / len(put_mids)
        return _clip(diff / (last_price * 0.01), -1.0, 1.0) if last_price > 0 else None
