# 日内低点执行规则 V2（Codex 执行版）

## 1. 文档定位

本文件用于指导 Codex 或其他代码代理实现“**日内低点执行规则 V2**”。

该规则的定位不是预测绝对最低点，而是在“**每日必须完成买入**”的前提下，尽量提前识别“**已发生回落且开始局部反转**”的时点，以提高买在当日低点附近的概率。

本文件只定义“**提前买入信号**”的判断逻辑。
“**最后 15 分钟强制买入**”属于外部兜底逻辑，可由上层执行模块实现，但本文件会预留接口。

---

## 2. 目标与边界

### 2.1 目标

在每个交易日中：

- 若日内出现较像“局部低点附近”的反转结构，则提前买入。
- 若直到收盘前仍未出现该结构，则交给外部 `force_buy` 逻辑处理。

### 2.2 非目标

本规则**不做**以下事情：

- 不判断长期趋势是否看多。
- 不判断当天整体趋势是否强或弱。
- 不预测当日绝对最低价。
- 不处理仓位分配、滑点、限价策略、订单路由。
- 不实现最后 15 分钟强制买入细节。

### 2.3 设计原则

- 每日必须买。
- 趋势过滤一律不参与本规则。
- 规则追求“较高命中率买在低点附近”，而不是“尽早买”。
- 可以接受一部分交易日无法提前识别，交给尾盘兜底。

---

## 3. 时间框架

### 3.1 推荐粒度

优先使用：

- `1m bar`

兼容使用：

- `5m bar`

### 3.2 默认评估方式

- 仅在 **bar 收盘后** 计算一次信号。
- 不在 bar 内部做 tick 级预测。

---

## 4. 输入数据

每根 bar 至少需要以下字段：

- `ts`：时间戳
- `open`
- `high`
- `low`
- `close`
- `volume`

额外需要：

- 当日交易时段定义
- 收盘时间
- 外部传入的 `force_buy_time`

---

## 5. 输出定义

每次 bar 计算后，输出以下三类之一：

- `wait`
- `buy_now`
- `force_buy`

含义：

- `wait`：继续等待
- `buy_now`：当前满足提前买入条件
- `force_buy`：已到外部定义的兜底时刻，必须买入

说明：

- `force_buy` 的触发时机由外部模块控制。
- 本规则模块只需支持该状态输出，不需要在此文件内实现尾盘逻辑细节。

---

## 6. 核心思路

提前买入条件由两层组成：

1. `pullback_ok`：此前已经发生回落
2. `reversal_ok`：当前出现局部反转确认

只有两者同时满足，才输出 `buy_now`。

即：

```python
buy_now = pullback_ok and reversal_ok
```

---

## 7. 特征定义

## 7.1 EMA 指标

定义：

```python
ema5 = EMA(close, 5)
ema20 = EMA(close, 20)
```

同时需要：

```python
ema5_prev = ema5.shift(1)
```

说明：

- `ema5` 用于判断超短线修复
- `ema20` 用于判断当前价格是否仍处在回落背景下

---

## 7.2 最近 3 根 bar 高点

定义：

```python
recent_3bar_high = max(high[-1], high[-2], high[-3])
```

说明：

- 这里的 `[-1]、[-2]、[-3]` 指当前 bar 之前的 3 根已完成 bar
- 当前 bar 不计入该最高值

---

## 8. 规则定义

## 8.1 回落条件 `pullback_ok`

定义：

```python
pullback_ok = close < ema20
```

解释：

- 当前价格位于 `ema20` 下方，表示在短期视角下，价格仍处于回落背景中
- 该条件只用于过滤“完全没跌过、直接向上冲”的场景

---

## 8.2 反转条件 `reversal_ok`

满足以下任一即可：

### 方案 A：突破最近 3 根 bar 高点

```python
reversal_ok_a = close > recent_3bar_high
```

含义：

- 当前 bar 的收盘价已经突破此前 3 根 bar 的局部高点
- 表示价格开始脱离局部底部

### 方案 B：连续两次低点抬高

```python
reversal_ok_b = (low[-1] > low[-2]) and (low[0] > low[-1])
```

说明：

- 这里 `low[0]` 表示当前 bar 的 `low`
- 该条件表达的是局部低点开始抬高

### 方案 C：站上 ema5 且 ema5 拐头向上

```python
reversal_ok_c = close > ema5 and ema5 > ema5_prev
```

含义：

- 当前价格已站上短均线
- 短均线本身开始上拐

### 综合定义

```python
reversal_ok = reversal_ok_a or reversal_ok_b or reversal_ok_c
```

---

## 8.3 提前买入条件 `buy_now`

定义：

```python
buy_now = pullback_ok and reversal_ok
```

如果 `buy_now == True`，则本 bar 输出：

```python
signal = "buy_now"
```

否则输出：

```python
signal = "wait"
```

---

## 8.4 尾盘兜底接口 `force_buy`

该规则不实现尾盘强制买入逻辑，但预留统一接口：

```python
if current_time >= force_buy_time and not already_bought_today:
    signal = "force_buy"
```

其中：

- `force_buy_time` 由外部模块给定
- 典型值可设为：收盘前 15 分钟
- `already_bought_today` 由上层执行器维护

优先级建议：

```python
if already_bought_today:
    signal = "wait"
elif current_time >= force_buy_time:
    signal = "force_buy"
elif buy_now:
    signal = "buy_now"
else:
    signal = "wait"
```

---

## 9. 最小实现伪代码

```python
from dataclasses import dataclass


@dataclass
class IntradayLowSignalResult:
    signal: str                # wait / buy_now / force_buy
    pullback_ok: bool
    reversal_ok_a: bool
    reversal_ok_b: bool
    reversal_ok_c: bool
    reversal_ok: bool
    ema5: float | None
    ema20: float | None
    recent_3bar_high: float | None


def compute_intraday_low_signal_v2(df, current_idx, force_buy_time, already_bought_today):
    """
    df 必须至少包含: ts, open, high, low, close, volume
    current_idx 指向当前已收盘 bar
    """

    # 若当天已买，直接等待
    if already_bought_today:
        return IntradayLowSignalResult(
            signal="wait",
            pullback_ok=False,
            reversal_ok_a=False,
            reversal_ok_b=False,
            reversal_ok_c=False,
            reversal_ok=False,
            ema5=None,
            ema20=None,
            recent_3bar_high=None,
        )

    current_time = df.iloc[current_idx]["ts"]

    # force buy 优先级高于普通信号
    if current_time >= force_buy_time:
        return IntradayLowSignalResult(
            signal="force_buy",
            pullback_ok=False,
            reversal_ok_a=False,
            reversal_ok_b=False,
            reversal_ok_c=False,
            reversal_ok=False,
            ema5=None,
            ema20=None,
            recent_3bar_high=None,
        )

    # warmup 不足
    if current_idx < 20:
        return IntradayLowSignalResult(
            signal="wait",
            pullback_ok=False,
            reversal_ok_a=False,
            reversal_ok_b=False,
            reversal_ok_c=False,
            reversal_ok=False,
            ema5=None,
            ema20=None,
            recent_3bar_high=None,
        )

    close_series = df["close"].iloc[: current_idx + 1]
    high_series = df["high"].iloc[: current_idx + 1]
    low_series = df["low"].iloc[: current_idx + 1]

    ema5 = close_series.ewm(span=5, adjust=False).mean().iloc[-1]
    ema20 = close_series.ewm(span=20, adjust=False).mean().iloc[-1]
    ema5_prev = close_series.iloc[:-1].ewm(span=5, adjust=False).mean().iloc[-1]

    close_now = close_series.iloc[-1]
    low_now = low_series.iloc[-1]

    recent_3bar_high = max(high_series.iloc[-2], high_series.iloc[-3], high_series.iloc[-4])

    pullback_ok = close_now < ema20

    reversal_ok_a = close_now > recent_3bar_high
    reversal_ok_b = (low_series.iloc[-2] > low_series.iloc[-3]) and (low_now > low_series.iloc[-2])
    reversal_ok_c = (close_now > ema5) and (ema5 > ema5_prev)

    reversal_ok = reversal_ok_a or reversal_ok_b or reversal_ok_c

    signal = "buy_now" if (pullback_ok and reversal_ok) else "wait"

    return IntradayLowSignalResult(
        signal=signal,
        pullback_ok=bool(pullback_ok),
        reversal_ok_a=bool(reversal_ok_a),
        reversal_ok_b=bool(reversal_ok_b),
        reversal_ok_c=bool(reversal_ok_c),
        reversal_ok=bool(reversal_ok),
        ema5=float(ema5),
        ema20=float(ema20),
        recent_3bar_high=float(recent_3bar_high),
    )
```

---

## 10. 实现约束

Codex 在实现时必须遵守以下约束：

### 10.1 单日只触发一次真实买入

- 一旦上层执行器确认当日已买入
- 后续所有 bar 都应返回 `wait`

### 10.2 仅使用当前 bar 及过去数据

禁止使用任何未来数据。

### 10.3 只在 bar 收盘后计算

- 不在 bar 尚未收盘时做信号判断
- 避免未来高低点污染

### 10.4 force_buy 为外部控制

本规则模块不得在内部写死交易所时间，应由外部传入 `force_buy_time`。

---

## 11. 参数表

| 参数名 | 默认值 | 说明 |
|---|---:|---|
| `ema_fast_span` | 5 | 短均线周期 |
| `ema_slow_span` | 20 | 回落过滤均线周期 |
| `recent_high_lookback` | 3 | 反转突破参考 bar 数 |
| `force_buy_minutes_before_close` | 15 | 外部兜底买入时点，供上层使用 |
| `bar_interval` | 1m | 推荐 1m，兼容 5m |

---

## 12. 推荐文件结构

建议 Codex 将该功能实现为以下模块之一：

```text
app/intraday/low_exec_v2.py
```

或：

```text
app/execution/intraday_low_signal_v2.py
```

建议暴露以下函数：

```python
def compute_intraday_low_signal_v2(...):
    ...
```

若项目中已有日内状态对象，也可以改为：

```python
def evaluate_intraday_buy_signal(state, bar, config):
    ...
```

---

## 13. 日志要求

建议每根 bar 输出以下调试字段，便于后续回测分析：

- `ts`
- `close`
- `ema5`
- `ema20`
- `recent_3bar_high`
- `pullback_ok`
- `reversal_ok_a`
- `reversal_ok_b`
- `reversal_ok_c`
- `reversal_ok`
- `signal`
- `already_bought_today`

---

## 14. 回测评价建议

该规则的目标不是尽早买，而是尽量买在**当日低点附近**。

因此建议后续至少统计以下指标：

### 14.1 低点附近命中率

定义示例：

```python
success = buy_price <= day_low + near_low_threshold
```

然后统计：

```python
success_rate = success_days / total_days
```

### 14.2 平均偏离最低点

```python
buy_premium = buy_price - day_low
```

### 14.3 未提前触发比例

即最终依赖 `force_buy` 的交易日比例。

说明：

- 这里的 `near_low_threshold` 不在本规则中固定写死
- 可由后续研究单独决定，例如按 ATR、按日内振幅比例、或按绝对价格差定义

---

## 15. 当前版本结论

当前 V2 版本保持极简，只保留以下核心判断：

```python
pullback_ok = close < ema20

reversal_ok = (
    close > max(high[-1], high[-2], high[-3])
    or ((low[-1] > low[-2]) and (low[0] > low[-1]))
    or (close > ema5 and ema5 > ema5_prev)
)

buy_now = pullback_ok and reversal_ok
```

这是一个“**回落后，局部反转确认，再提前买入**”的执行规则。

其目的是：

- 不依赖趋势过滤
- 不试图预测绝对最低点
- 只在更像低点附近的时刻提前出手
- 未命中的情况交给尾盘兜底

