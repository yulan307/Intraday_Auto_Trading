# Architecture Overview

## 2026-04-28 Bar-Only Market Data Schema

- Current `data/market_data.sqlite` stores only bar-related data:
  - `symbols`
  - `price_bars`
  - `bar_request_log`
- `bar_request_log` replaces the previous `daily_coverage` cache. It records `(symbol, bar_size, trade_date)`, source, UTC request window, status, expected bars, actual bars, and message.
- `price_bars.ts`, `bar_request_log.request_start_ts`, and `bar_request_log.request_end_ts` are UTC-naive timestamps.
- Exchange-local market sessions are built with `ZoneInfo("America/New_York")` and then converted to UTC-naive timestamps, so EST/EDT changes are handled by the IANA timezone database.
- Current bar fetch defaults are fixed to IB Gateway (`ibkr`) for DB reads, live fetches, and historical fetches. `fetch-symbol-pool-data --bar-providers` is retained only as a compatibility option and only accepts `ibkr`.
- `TrendInputLoader` derives `session_vwap`, official open, and last price from the loaded `1m` bars; provider session metric VWAP is not used in the current active path.
- Provider exceptions are recorded as `bar_request_log.status='failed'` with the error message and are retried on later runs. Confirmed empty IBKR responses remain `no_data`.
- `fetch-symbol-pool-data` fetches only `1m` and `1d` bars. Active fetch paths no longer create direct or derived `15m` bars.
- `fetch-symbol-pool-data --force-refresh` ignores existing request-log rows for the requested window, which is useful when older `no_data` rows were produced by a provider or network failure.
- Option quotes, session metrics, opening imbalance, and trend snapshots are not part of the current market-data database. Older data for those tables is preserved only in the backup database created before this migration.

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
- `gateways/yfinance_market_data.py`: yfinance 适配器，1m（7天内）和 15m（60天内）bar；options/imbalance 不支持；作为回测第三候补源
- `services/market_data_sync.py`: 编排 provider 能力探测与数据同步，CLI 入口为 `sync-market-data`

能力矩阵详见 `docs/market-data-capability-matrix.md`。

### 7. 统一 Bar 数据服务（services/bar_data_service.py）

**`BarDataService`** 是实盘与回测共用的统一 bar 数据入口，取代旧的 `BacktestDataService`：

- **统一接口**：`get_bars(symbols, bar_size, start_date, end_date) → dict[str, list[MinuteBar]]`
  - 输入为 `date`（非 `datetime`），bar_size < 1d 时自动使用当天完整交易时段（9:30–16:00 ET）
- **`daily_coverage` 表**：记录每个 `(symbol, bar_size, trade_date)` 的数据完整性状态
  - `is_complete=1, actual_bars>0`：数据完整，直接从 DB 返回
  - `is_complete=1, actual_bars=0`：已确认该日无数据（symbol 尚未上市等）
  - `is_complete=0`：部分数据或未曾拉取，触发重新获取
- **Live vs Historical 路由**：当前默认 `live_source_order` 与 `history_source_order` 都固定为 `["ibkr"]`
- **DB 写回**：成功拉取后立即持久化，并更新 `bar_request_log`
- **`build_bar_data_service(settings)`**：`app.py` 中的工厂方法
- **迁移脚本**：`scripts/backfill_daily_coverage.py`，一次性从现有 `price_bars` 回填 `daily_coverage`

旧 `BacktestDataService` 保留供 `BacktestChainValidationService` 使用，不影响现有链路。

### 8. 账户/订单 Gateway 实现（gateways/ibkr_account.py）

- `IBKRAccountGateway`: 实现 `AccountGateway`，提供账户摘要、持仓查询、挂单查询
  - `get_account_summary()` → `reqAccountSummary`（净值、现金、购买力）
  - `get_positions()` → `reqPositions`
  - `get_open_orders()` → `reqAllOpenOrders`
  - `probe_capabilities()` → socket 连通性检测
- `IBKRBrokerGateway`: 实现 `BrokerGateway`，提供下单与撤单
  - `place_order(instruction)` → `placeOrder`（limit/market order）；需设置 `eTradeOnly=False`
  - `cancel_order(broker_order_id)` → `cancelOrder`（兼容新旧 ibapi 版本）
  - `readonly=True` 时在代码层直接拒绝，不触碰网络
- 两个 gateway 各自使用独立 client_id（`account_client_id` / `broker_client_id`），避免与行情 gateway 冲突
- CLI 入口：`show-account [--ibkr-profile paper|live]`

注意：`place_order` / `cancel_order` 还受 IB Gateway 应用层 "Read-Only API" 配置控制（Configure → API → Settings），需在 IB Gateway 中关闭后方可使用。

## 当前实现边界

- IBKR 与 Moomoo 真实 API 已接入，含能力探测、SQLite 持久化与 CLI 同步命令；当前 bar 主动抓取路径固定使用 IBKR
- yfinance 仍保留为 optional dep 和 legacy/test provider，但不再是当前默认 bar fallback
- 回测数据链路已实现（`fetch-bars` CLI），当前默认 DB 缓存与抓取优先使用 IBKR 数据
- IBKR 账户/持仓/挂单查询已实现并本地验证（paper 账户）
- IBKR 下单/撤单已实现并本地验证（paper 账户，LMT 挂单→查询→撤单完整流程通过）
- IBKR opening imbalance 已实现请求路径，但受 entitlement `10089` 限制，paper 环境不可用
- IBKR 期权报价受 subscription `354` 限制，chain 发现可用，实时报价不可用
- Moomoo opening imbalance 尚无已知公开 API，暂标记为不支持
- bars 来源当前固定为 IBKR；session VWAP 当前由 1m bar 计算
- 趋势判定逻辑先用基础规则占位，便于后续替换为量化因子模型
- 产品级需求说明已整理到 `docs/product-requirements.md`

## 推荐下一步

1. 继续验证 IBKR-only bar 数据完整性与 `bar_request_log` 重试行为
2. 将 `IBKRAccountGateway` 和 `IBKRBrokerGateway` 接入 `app.py` / executor，实现完整实盘链路
3. 若以 Moomoo 为 bar 主源，补充独立的 Moomoo bar gateway
4. 将趋势分类逻辑替换为文档定义的完整开盘主导模型
5. 将 tracker 状态持久化到 SQLite 或 Redis，避免进程重启丢单
6. 增加回放测试，覆盖开盘强势、震荡、弱势拖尾三种场景
