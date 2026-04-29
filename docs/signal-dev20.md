# dev20 信号算法文档

## 概述

基于 VWAP 与 EMA20 偏离度的动量信号，通过多阶稳健回归斜率识别买入时机。

实现位置：`scripts/main_chain_chart.py`，函数 `_simulate_and_collect()`。

---

## 指标定义

### 基础指标

| 指标 | 公式 | 说明 |
|------|------|------|
| VWAP | `∑(典型价 × 成交量) / ∑成交量` | 日内累积，典型价 = (High + Low + Close) / 3 |
| EMA5 | 5周期指数移动平均 | 基于收盘价 |
| EMA10 | 10周期指数移动平均 | 基于收盘价 |
| EMA20 | 20周期指数移动平均 | 基于收盘价 |

### 派生指标

```
dev20    = (vwap - ema20) / vwap
s_dev20  = Theil-Sen slope(dev20,  window=10)
ss_dev20 = Theil-Sen slope(s_dev20, window=10)
valley   = s_dev20 + 10 × ss_dev20
s_valley = Theil-Sen slope(valley,  window=3)
```

- **dev20**：EMA20 相对 VWAP 的归一化偏离，正值表示 EMA20 在 VWAP 之下（价格偏低）
- **s_dev20**：dev20 的一阶动量（斜率），正值表示偏离正在扩大
- **ss_dev20**：dev20 的二阶动量（加速度），负值表示扩大速度正在放缓（即将收敛）
- **valley**：综合一阶与二阶动量的合成指标，用于识别动量谷底
- **s_valley**：valley 的短周期（3根）稳健斜率，负值表示 valley 仍在下行

### 稳健回归方法

所有斜率均使用 **Theil-Sen 估计量**（所有点对斜率的中位数），对异常值具有鲁棒性。

```python
def _theil_sen_slope(values, n):
    y = values[-n:]
    slopes = [(y[j] - y[i]) / (j - i) for i in range(n) for j in range(i+1, n)]
    return sorted(slopes)[len(slopes) // 2]
```

---

## 信号逻辑

### 买点条件（同时满足）

```
ema20 < vwap                    # EMA20 在 VWAP 之下，价格处于低估区间
AND s_dev20 > valley > 0        # dev20 动量为正，valley 为正但小于 s_dev20
AND 0 > s_valley                # valley 斜率为负（合成动量开始回落）
AND abs(s_valley × 10) > s_dev20  # 回落幅度足够大，相对于一阶动量有显著性
```

**语义**：EMA20 低于 VWAP（偏离存在），一阶动量 s_dev20 为正（偏离仍在扩大），
但 valley 已开始向下（s_valley < 0），且回落力度超过当前一阶动量，
预示偏离即将收敛，是较优的买入时机。

### 撤单条件

```
EMA5 < EMA10
```

快线下穿慢线，短期动量转弱，离场。撤单后可在下一个买点重新入场（可反复出入）。

---

## 参数汇总

| 参数 | 值 | 含义 |
|------|----|------|
| `EMA_FAST_SPAN` | 5 | EMA5 周期 |
| `EMA10_SPAN` | 10 | EMA10 周期 |
| `EMA_SLOW_SPAN` | 20 | EMA20 周期 |
| `DEV20_WINDOW` | 10 | s_dev20 的 Theil-Sen 窗口 |
| `S_DEV20_WINDOW` | 10 | ss_dev20 的 Theil-Sen 窗口 |
| `VALLEY_WINDOW` | 3 | s_valley 的 Theil-Sen 窗口 |
| Warmup | 20 bars | EMA20 稳定所需最少 bar 数 |

---

## 图表说明

价格面板：K线 + EMA5（粉红）/ EMA10（橙）/ EMA20（紫）/ VWAP（蓝虚线）
- 绿色上三角 `▲`：买点
- 红色下三角 `▼`：撤单点

指标面板（下方）：四条线共享同一 y 轴
- 蓝色实线：`s_dev20`
- 橙色虚线：`ss_dev20`
- 紫色粗实线：`valley`
- 青绿色点划线：`s_valley × 10`（放大 10 倍与其他指标对齐量级）

---

*最后更新：2026-04-23*
