# Implementation Plan: Data Persistence and Backtesting

## 1. 文档目标

本文档将增量需求进一步细化为可实施的开发方案，覆盖以下内容：

- 目录与模块落地
- SQLite 数据库拆分方案
- 关键数据表设计
- 接口抽象建议
- 实盘与回测两条调用链
- 分阶段开发顺序

本文档是以下文档的具体实施补充：

- `docs/product-requirements.md`
- `docs/trend-classification-spec.md`
- `docs/incremental-requirements-data-backtest.md`

## 2. 总体落地原则

### 2.1 推荐采用双库方案

建议拆分为两个 SQLite 数据库，而不是单库混放：

- `market_data.sqlite`
  - 存放行情、快照、期权、基础信息等市场数据
- `backtest.sqlite`
  - 存放回测运行、订单、成交、持仓、资金曲线和绩效结果

这样做的原因：

- 市场数据是可长期复用的研究资产
- 回测账户数据是按回测批次隔离的运行结果
- 两类数据生命周期不同，拆库后更容易维护、迁移和清理

### 2.2 推荐增加 Repository 层

现有骨架里只有 Gateway 协议，落地时建议新增 Repository 层：

- `Gateway`
  - 负责从 IBKR / Moomoo / 本地回放源获取原始数据
- `Repository`
  - 负责把标准化数据写入 SQLite，并提供查询能力
- `Service`
  - 负责趋势判断、选股、执行和回测编排

### 2.3 推荐统一接口，分离实现

上层策略只依赖抽象接口，不依赖具体环境：

- 实盘模式：`IBKRBrokerGateway`
- 回测模式：`BacktestBrokerGateway`
- 实时行情模式：`IBKR/Moomoo Gateway`
- 历史回放模式：`HistoricalMarketDataGateway`

## 3. 目录结构落地建议

建议在现有目录基础上扩展为：

```text
src/intraday_auto_trading/
├── app.py
├── cli.py
├── config.py
├── models.py
├── interfaces/
│   ├── brokers.py
│   ├── repositories.py
│   └── backtest.py
├── persistence/
│   ├── sqlite_base.py
│   ├── market_data_repository.py
│   ├── backtest_account_repository.py
│   └── schema.py
├── gateways/
│   ├── ibkr_market_data.py
│   ├── moomoo_options.py
│   ├── ibkr_broker.py
│   └── historical_market_data.py
├── backtest/
│   ├── broker.py
│   ├── engine.py
│   ├── account.py
│   ├── metrics.py
│   └── runner.py
└── services/
    ├── executor.py
    ├── selector.py
    ├── tracker.py
    └── trend_classifier.py
```

### 3.1 模块职责

- `interfaces/repositories.py`
  - 定义市场数据存储与回测账户存储的抽象接口
- `persistence/`
  - 实现 SQLite 的建表、写入、查询
- `gateways/`
  - 对接 IBKR、Moomoo 或历史回放数据源
- `backtest/`
  - 实现回测执行、状态推进、指标统计

## 4. SQLite 数据库设计

## 4.1 市场数据库：`market_data.sqlite`

### 表 1：`symbols`

用途：保存标的基础信息。

建议字段：

- `symbol` TEXT PRIMARY KEY
- `name` TEXT
- `exchange` TEXT
- `asset_type` TEXT
- `currency` TEXT
- `is_active` INTEGER
- `created_at` TEXT
- `updated_at` TEXT

### 表 2：`price_bars`

用途：统一保存 1m / 15m bar，避免拆成多张重复结构的表。

建议字段：

- `symbol` TEXT NOT NULL
- `bar_size` TEXT NOT NULL
- `ts` TEXT NOT NULL
- `open` REAL NOT NULL
- `high` REAL NOT NULL
- `low` REAL NOT NULL
- `close` REAL NOT NULL
- `volume` REAL NOT NULL
- `source` TEXT NOT NULL
- `created_at` TEXT NOT NULL

建议唯一约束：

- `(symbol, bar_size, ts, source)`

说明：

- `bar_size` 推荐使用 `1m`、`15m`
- 后续若扩展到 `5m` 或 `1d`，无需改表结构

### 表 3：`session_metrics`

用途：保存按时间点采样的会话级指标。

建议字段：

- `symbol` TEXT NOT NULL
- `ts` TEXT NOT NULL
- `official_open` REAL
- `last_price` REAL
- `session_vwap` REAL
- `source` TEXT NOT NULL
- `created_at` TEXT NOT NULL

建议唯一约束：

- `(symbol, ts, source)`

### 表 4：`opening_imbalance`

用途：保存开盘撮合数据。

建议字段：

- `symbol` TEXT NOT NULL
- `trade_date` TEXT NOT NULL
- `opening_imbalance_side` TEXT
- `opening_imbalance_qty` REAL
- `paired_shares` REAL
- `indicative_open_price` REAL
- `source` TEXT NOT NULL
- `created_at` TEXT NOT NULL

建议唯一约束：

- `(symbol, trade_date, source)`

### 表 5：`option_contracts`

用途：保存期权合约静态信息。

建议字段：

- `contract_id` TEXT PRIMARY KEY
- `symbol` TEXT NOT NULL
- `expiry` TEXT NOT NULL
- `strike` REAL NOT NULL
- `option_type` TEXT NOT NULL
- `exchange` TEXT
- `multiplier` INTEGER
- `created_at` TEXT NOT NULL
- `updated_at` TEXT NOT NULL

### 表 6：`option_quotes`

用途：保存期权快照。

建议字段：

- `contract_id` TEXT NOT NULL
- `symbol` TEXT NOT NULL
- `snapshot_ts` TEXT NOT NULL
- `bid` REAL
- `ask` REAL
- `bid_size` INTEGER
- `ask_size` INTEGER
- `last` REAL
- `volume` INTEGER
- `iv` REAL
- `delta` REAL
- `gamma` REAL
- `source` TEXT NOT NULL
- `created_at` TEXT NOT NULL

建议唯一约束：

- `(contract_id, snapshot_ts, source)`

### 表 7：`trend_snapshots`

用途：保存每次趋势判定的输入摘要和输出结果，便于回放和排障。

建议字段：

- `symbol` TEXT NOT NULL
- `eval_time` TEXT NOT NULL
- `regime` TEXT NOT NULL
- `score` REAL NOT NULL
- `reason` TEXT NOT NULL
- `official_open` REAL
- `last_price` REAL
- `session_vwap` REAL
- `source` TEXT NOT NULL
- `created_at` TEXT NOT NULL

建议唯一约束：

- `(symbol, eval_time, source)`

## 4.2 回测数据库：`backtest.sqlite`

### 表 1：`backtest_runs`

用途：标识一次独立回测任务。

建议字段：

- `run_id` TEXT PRIMARY KEY
- `name` TEXT
- `started_at` TEXT NOT NULL
- `finished_at` TEXT
- `symbols` TEXT NOT NULL
- `start_date` TEXT NOT NULL
- `end_date` TEXT NOT NULL
- `initial_cash` REAL NOT NULL
- `status` TEXT NOT NULL
- `config_snapshot` TEXT NOT NULL

### 表 2：`backtest_orders`

用途：保存回测订单生命周期。

建议字段：

- `order_id` TEXT PRIMARY KEY
- `run_id` TEXT NOT NULL
- `symbol` TEXT NOT NULL
- `side` TEXT NOT NULL
- `order_type` TEXT NOT NULL
- `strategy` TEXT NOT NULL
- `quantity` INTEGER NOT NULL
- `limit_price` REAL
- `status` TEXT NOT NULL
- `created_at` TEXT NOT NULL
- `updated_at` TEXT NOT NULL
- `reason` TEXT

### 表 3：`backtest_fills`

用途：保存成交记录。

建议字段：

- `fill_id` TEXT PRIMARY KEY
- `run_id` TEXT NOT NULL
- `order_id` TEXT NOT NULL
- `symbol` TEXT NOT NULL
- `fill_ts` TEXT NOT NULL
- `fill_price` REAL NOT NULL
- `fill_quantity` INTEGER NOT NULL
- `commission` REAL NOT NULL DEFAULT 0
- `slippage` REAL NOT NULL DEFAULT 0

### 表 4：`backtest_positions`

用途：保存持仓快照或最终持仓状态。

建议字段：

- `run_id` TEXT NOT NULL
- `symbol` TEXT NOT NULL
- `snapshot_ts` TEXT NOT NULL
- `quantity` INTEGER NOT NULL
- `avg_cost` REAL NOT NULL
- `market_price` REAL
- `market_value` REAL
- `unrealized_pnl` REAL

建议唯一约束：

- `(run_id, symbol, snapshot_ts)`

### 表 5：`backtest_cash_ledger`

用途：保存资金变化流水。

建议字段：

- `ledger_id` TEXT PRIMARY KEY
- `run_id` TEXT NOT NULL
- `event_ts` TEXT NOT NULL
- `event_type` TEXT NOT NULL
- `amount` REAL NOT NULL
- `cash_after` REAL NOT NULL
- `order_id` TEXT
- `note` TEXT

### 表 6：`backtest_metrics`

用途：保存回测汇总指标。

建议字段：

- `run_id` TEXT PRIMARY KEY
- `total_return` REAL
- `max_drawdown` REAL
- `win_rate` REAL
- `trade_count` INTEGER
- `ending_cash` REAL
- `ending_equity` REAL
- `created_at` TEXT NOT NULL

## 5. 接口落地建议

## 5.1 新增 Repository 协议

建议新增两个协议：

### `MarketDataRepository`

职责：

- upsert 标的基础信息
- 保存 bar 数据
- 保存 session 指标
- 保存期权合约
- 保存期权快照
- 查询某时段历史数据

### `BacktestAccountRepository`

职责：

- 创建回测 run
- 保存订单
- 更新订单状态
- 保存成交
- 保存持仓快照
- 保存资金流水
- 保存绩效指标
- 按 `run_id` 查询回测结果

## 5.2 调整 Broker 接口

当前 `BrokerGateway` 只有：

- `place_order`
- `cancel_order`

建议扩展为至少：

- `place_order`
- `modify_order`
- `cancel_order`
- `get_order_status`

这样实盘和回测都能复用同一套上层调用方式。

## 5.3 调整 Account 接口

建议将当前账户接口拆成两类视角：

- `LiveAccountGateway`
  - 面向实盘 / paper
- `BacktestAccountService`
  - 面向回测运行态

上层若只关心“标的是否已有仓位、周内是否买过”，可以保留统一的读取抽象。

## 6. 调用链落地

## 6.1 实盘 / paper 模式调用链

1. Scheduler 触发交易任务
2. `MarketDataGateway` 拉取实时数据
3. `MarketDataRepository` 自动落库
4. `TrendClassifier` 计算三分类
5. `SymbolSelector` 选择交易标的
6. `BrokerGateway` 下单或进入追踪逻辑
7. 订单与执行结果同步写入账户层

说明：

- 第 3 步是本次新增的关键变化，实时获取的数据要先沉淀

## 6.2 回测模式调用链

1. `BacktestRunner` 创建 `run_id`
2. `HistoricalMarketDataGateway` 从 `market_data.sqlite` 读取历史数据
3. 按历史时间推进策略流程
4. `BacktestBrokerGateway` 模拟下单、撤单、改单、成交
5. `BacktestAccountRepository` 写入订单、成交、持仓和资金流水
6. `MetricsService` 计算收益率、最大回撤、胜率等指标
7. 将汇总结果写入 `backtest_metrics`

## 7. 开发顺序建议

## 阶段一：先打数据底座

目标：

- 能把实时或历史行情写入 `market_data.sqlite`

建议任务：

1. 新增 SQLite 基础连接和 schema 初始化
2. 实现 `MarketDataRepository`
3. 接通 `price_bars`、`session_metrics`、`option_quotes` 的写入
4. 在实时数据获取流程中加入自动落库

验收标准：

- 拉一次指定标的行情后，SQLite 中能看到完整入库记录

## 阶段二：补回测账户与模拟 broker

目标：

- 能在不连券商的情况下完整记录订单生命周期

建议任务：

1. 建立 `backtest.sqlite`
2. 实现 `backtest_runs`、`backtest_orders`、`backtest_fills`
3. 实现 `BacktestBrokerGateway`
4. 定义订单状态机：
   - `NEW`
   - `OPEN`
   - `PARTIALLY_FILLED`
   - `FILLED`
   - `CANCELED`
   - `REJECTED`

验收标准：

- 回测接口可以完成“下单 -> 撤单 / 改单 -> 成交 -> 记账”

## 阶段三：搭回测引擎

目标：

- 能基于历史数据跑完整策略

建议任务：

1. 实现 `HistoricalMarketDataGateway`
2. 实现 `BacktestRunner` 和 `BacktestEngine`
3. 让现有 `TrendClassifier`、`SymbolSelector`、`Tracker` 在回测链路复用
4. 实现基础指标统计

验收标准：

- 能对单个标的、单日区间完成一次完整回测

## 阶段四：补研究能力

目标：

- 能稳定用于策略验证和调优

建议任务：

1. 保存 `trend_snapshots`
2. 输出交易日志和资金曲线
3. 增加多标的、多日期批量回测
4. 增加参数扫描能力

## 8. 代码层的第一批修改点

建议最先改这些文件和模块：

- `src/intraday_auto_trading/interfaces/brokers.py`
  - 扩展 broker 接口
- `src/intraday_auto_trading/models.py`
  - 增加订单状态、成交记录、回测运行模型
- `src/intraday_auto_trading/interfaces/repositories.py`
  - 新增 repository 抽象
- `src/intraday_auto_trading/persistence/schema.py`
  - 新增 SQLite schema 初始化
- `src/intraday_auto_trading/persistence/market_data_repository.py`
  - 新增市场数据存储实现
- `src/intraday_auto_trading/backtest/broker.py`
  - 新增回测 broker
- `src/intraday_auto_trading/backtest/engine.py`
  - 新增回测引擎

## 9. 关键设计决策

当前建议先按以下决策推进：

1. 使用双 SQLite 库，而不是单库混放
2. `price_bars` 采用统一表加 `bar_size` 字段
3. 回测账户按 `run_id` 逻辑隔离，不物理清空历史结果
4. 策略层不直接访问 SQLite，只通过 repository 和 gateway
5. 回测 broker 与实盘 broker 使用统一接口

## 10. 结论

如果按这份落地方案推进，项目会形成一条清晰的演进路径：

1. 先把实时数据沉淀下来
2. 再把回测账户和模拟执行跑通
3. 最后把现有策略逻辑无缝迁移到历史回放场景

这样可以最大程度复用当前骨架，并把后续实盘、研究和回测三条能力线统一到同一套接口设计中。

