# Trend Classification Specification

## 1. 文档目的

本文档定义“开盘主导型三分类”接口的输入、数据采集要求、输出结构及分类语义，用于支撑标的选择模块的趋势判定。

## 2. 接口定义

### 2.1 输入

```python
symbol: str
now_ts: datetime   # 当前时间，按 America/New_York 解释
```

### 2.2 输出

```python
{
    "symbol": str,
    "eval_time": str,
    "regime": "EARLY_BUY" | "RANGE_TRACK_15M" | "WEAK_TAIL"
}
```

## 3. 结果语义

### 3.1 `EARLY_BUY`

- 表示开盘后走势偏强。
- 适合直接执行早买策略。

### 3.2 `RANGE_TRACK_15M`

- 表示价格更可能处于震荡区间。
- 不宜立即买入，应进入 15 分钟追踪确认流程。

### 3.3 `WEAK_TAIL`

- 表示价格偏弱，更接近日内低位。
- 在“买低点”的业务目标下，应优先考虑此类标的，但通常需要追踪确认后再挂单。

## 4. 数据采集要求

### 4.1 标的基础行情

必须获取以下字段：

1. 当日官方开盘价 `official_open`
2. 当前最新价 `last_price`
3. 当日实时 VWAP `session_vwap`
4. 当天 1 分钟 bar：
   - `open`
   - `high`
   - `low`
   - `close`
   - `volume`

### 4.2 开盘后 1 分钟 bar 采样范围

由 `now_ts` 决定：

- `09:30 <= now_ts < 09:35`
  - 获取 `09:30 ~ now_ts` 的全部 1 分钟 bar。
- `09:35 <= now_ts < 10:00`
  - 获取 `09:30 ~ now_ts` 的全部 1 分钟 bar。
- `now_ts >= 10:00`
  - 获取 `09:30 ~ 10:00` 的全部 1 分钟 bar。
  - 额外获取当前 `last_price` 和 `session_vwap`。

## 5. 可选数据

### 5.1 开盘撮合数据

若数据源支持，建议获取以下字段：

1. `opening_imbalance_side`
   - `BUY`
   - `SELL`
   - `NONE`
2. `opening_imbalance_qty`
3. `paired_shares`
4. `indicative_open_price`

### 5.2 期权数据

#### 合约选择规则

1. 优先最近到期合约：
   - 优先 0DTE；
   - 否则选择最近到期且 `DTE <= 7` 的合约。
2. 以当前价格为中心，获取：
   - ATM strike；
   - ATM 下 1 档 strike；
   - ATM 上 1 档 strike。
3. 每个 strike 同时获取 call / put。

#### 每个合约需要的字段

- `bid`
- `ask`
- `bid_size`
- `ask_size`
- `last`
- `volume`
- `iv`（若有）
- `delta`（若有）
- `gamma`（若有）

### 5.3 期权快照时间点

至少获取以下两个时间点：

1. `t_open_snapshot`
   - 开盘后首个有效快照。
2. `t_now_snapshot`
   - 当前时刻快照。

若 `now_ts >= 10:00`，则建议获取三个时间点：

1. `t_open_snapshot`
2. `t_10m_snapshot`（约 `09:40`）
3. `t_now_snapshot`

## 6. 实现要求

### 6.1 时间要求

- 所有交易时段判断统一采用 `America/New_York`。
- 文档中的开盘时间默认指美股常规交易时段开盘 `09:30`。

### 6.2 接口职责边界

- 本接口只负责分类，不负责最终选股排序。
- 本接口输出的 `regime` 将被上层选股逻辑结合账户状态再次加权。

### 6.3 与上层流程的关系

- `EARLY_BUY` 通常映射为立即买入策略。
- `RANGE_TRACK_15M` 通常映射为 15 分钟追踪买入策略。
- `WEAK_TAIL` 在选股阶段通常会获得更高的“低位买入”权重，但执行时仍应通过追踪机制确认。

## 7. 开发注意事项

- 若数据源暂不支持开盘撮合数据或部分期权 Greeks，可先返回空值并保留接口扩展位。
- 若 `now_ts >= 10:00`，分类逻辑需要区分“开盘前 30 分钟走势”和“10:00 之后的当前状态”。
- 若行情源字段命名不一致，应在网关适配层统一转换，不要把供应商细节泄漏到分类器内部。

