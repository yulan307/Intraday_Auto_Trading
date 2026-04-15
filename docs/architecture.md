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

## 当前实现边界

- 目前仓库提供的是“可继续开发的启动骨架”
- 真实 IBKR/Moomoo API 尚未接入，使用 `Protocol` 预留接口
- 趋势判定逻辑先用基础规则占位，便于后续替换为量化因子模型
- 市场数据 SQLite schema 与 repository 已完成首批实现
- 产品级需求说明已整理到 `docs/product-requirements.md`

## 推荐下一步

1. 接入真实数据源，完成 `MarketDataGateway` 实现
2. 将趋势分类逻辑替换为文档定义的完整开盘主导模型
3. 将 tracker 状态持久化到 SQLite 或 Redis，避免进程重启丢单
4. 增加回放测试，覆盖开盘强势、震荡、弱势拖尾三种场景
