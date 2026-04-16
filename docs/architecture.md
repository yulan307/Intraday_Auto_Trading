# Architecture Overview

## 模块拆分

### 1. 交易日触发

- 负责判断当前是否为美东有效交易日
- 在开盘后指定时间点触发选股和执行逻辑
- 后续可接入 APScheduler、Windows Task Scheduler 或 GitHub Actions 调度外围流程

### 2. 趋势判定

- 按 `docs/trend-classification-spec.md` 中的接口规格获取数据
- 输出三分类结果：
  - `EARLY_BUY`
  - `RANGE_TRACK_15M`
  - `WEAK_TAIL`

### 3. 标的选择

- 将趋势分类与账户历史买入信息组合评分
- 倾向优先选择“未买过且更可能买在低位”的标的
- 输出唯一交易标的与买入策略

### 4. 交易执行

- `IMMEDIATE_BUY`: 市价/快速限价直接执行
- `TRACKING_BUY`: 进入 15 分钟追踪流程
- `FORCE_BUY`: 最后 15 分钟兜底买入

### 5. 账户与数据网关

- `MarketDataGateway`: 获取官方开盘价、1m bar、VWAP、期权快照
- `MarketDataRepository`: 将标准化市场数据写入 SQLite 并提供历史查询
- `BrokerGateway`: 下单、撤单、查询状态
- `AccountGateway`: 提供订单数、持仓与预算约束

### 6. 行情数据 Gateway 实现（gateways/）

- `gateways/ibkr_market_data.py`: IBKR IB Gateway 适配器，提供 1m/15m bar、session metrics、开盘 imbalance（受 entitlement 限制）、期权链发现（期权报价受 subscription 限制）
- `gateways/moomoo_options.py`: Moomoo OpenD 适配器，提供期权合约与快照；bar/snapshot 已本地验证，可扩展为 bar 主源
- `services/market_data_sync.py`: 编排 provider 能力探测与数据同步，CLI 入口为 `sync-market-data`

能力矩阵详见 `docs/market-data-capability-matrix.md`。

## 当前实现边界

- IBKR 与 Moomoo 真实 API 已接入，含能力探测、SQLite 持久化与 CLI 同步命令
- IBKR opening imbalance 已实现请求路径，但受 entitlement `10089` 限制，paper 环境不可用
- IBKR 期权报价受 subscription `354` 限制，chain 发现可用，实时报价不可用
- Moomoo opening imbalance 尚无已知公开 API，暂标记为不支持
- bars/session metrics 来源尚未做 provider 级可配置切换
- 趋势判定逻辑先用基础规则占位，便于后续替换为量化因子模型
- 产品级需求说明已整理到 `docs/product-requirements.md`

## 推荐下一步

1. 使 bars/session metrics 来源可在 IBKR 与 Moomoo 之间配置切换
2. 若以 Moomoo 为 bar 主源，补充独立的 Moomoo bar gateway
3. 将趋势分类逻辑替换为文档定义的完整开盘主导模型
4. 将 tracker 状态持久化到 SQLite 或 Redis，避免进程重启丢单
5. 增加回放测试，覆盖开盘强势、震荡、弱势拖尾三种场景
