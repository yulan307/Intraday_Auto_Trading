# Current Handoff Status

## Snapshot

- Date: `2026-04-17`
- Updated by: `Claude`
- Current branch: `test/backtest-chain-validation`
- Remote status: pushed to `origin/test/backtest-chain-validation`
- Latest commit: `a910029` `update backtest validation handoff status`

## What Was Just Completed

### `feat/backtest-virtual-account` — 虚拟账户模块（进行中）

- **`VirtualAccount`**（`gateways/virtual_account.py`）：同时满足 `AccountGateway` + `BrokerGateway` 协议，纯内存运行
  - `place_order()` / `cancel_order()` — 挂单/撤单
  - `process_bar(symbol, bar)` — 自动撮合（MKT @ open，LMT @ limit_price when bar.low ≤ limit）
  - `fill_order()` — 手动强制成交；`reset()` — 重置初始状态
  - `get_account_summary()` / `get_positions()` / `get_open_orders()` 等完整 AccountGateway 接口
- **`SqliteBacktestAccountRepository`**（`persistence/backtest_account_repository.py`）：持久化回测运行元数据和订单历史
- **Schema 新增**（`persistence/schema.py`）：`backtest_runs`、`backtest_orders` 两张表
- **协议完善**（`interfaces/repositories.py`）：`BacktestAccountRepository` 新增 `save_order()` / `load_orders()`
- **`app.py`**：新增 `build_virtual_account()` 工厂方法
- **测试**：`tests/test_virtual_account.py`（20 cases），全量 77 passed

### `feat/trend-classifier-v2` — 趋势判断模块（已合并）

- **`TrendClassifier`** 完全替换为量化评分模型（`services/trend_classifier.py`）
  - 价格信号（price_score）：open_change、vwap_bias、bar_slope、range_position 四个特征，加权合并
  - 期权信号（option_score）：IV skew（call-put）、IV skew 变化、delta bias、IV 绝对水平变化
  - Graceful degradation：iv/delta 为 None 时退化到 call/put mid 差；option_quotes 为空时纯价格驱动
  - vol_surge 调节因子：首 bar 放量时自动上调价格信号权重
- **`TrendInputLoader`** 新增（`services/trend_input_loader.py`）
  - `LiveTrendInputLoader`：从 IBKR/Moomoo gateway 实时获取
  - `BacktestTrendInputLoader`：从 SQLite 读取（DB 优先），metrics 缺失时从 bars 推算
- **Repository 新增 read 方法**（`interfaces/repositories.py` + `persistence/market_data_repository.py`）
  - `load_session_metrics(symbol, at_time)` → 取 at_time 前最近一条
  - `load_option_quotes(symbol, start, end)` → 连接 option_contracts 表返回完整 OptionQuote 列表
- **`app.py`** 新增 `build_live_trend_input_loader()` / `build_backtest_trend_input_loader()` 工厂方法
- **测试**：`tests/test_trend_classifier.py` (12 cases) + `tests/test_trend_input_loader.py` (10 cases)，全量 57 passed

### `feat/backtest-data-pipeline` — 回测数据链路（已合并）

- **`load_price_bars_with_source_priority()`**：repository 新增带优先级去重的读取方法，SQL 按 source 优先级排序，Python 端按 ts 去重，返回 `(bars, winning_source)`
- **`YfinanceMarketDataGateway`**（`gateways/yfinance_market_data.py`）：yfinance 第三方 bar 数据源，含 `YfinanceBackend` Protocol 和 `RealYfinanceBackend`；1m 最近 7 天，15m 最近 60 天；options/imbalance 标记为 UNSUPPORTED
- **`BacktestDataService`**（`services/backtest_data_service.py`）：DB 优先读取 → ibkr → moomoo → yfinance 顺序 fallback，成功拉取后立即写入 DB 缓存；`FetchResult` 记录每个 symbol 的来源与 bar 数量
- **`YfinanceSettings`**（`config.py`）：新增 `enabled` 和 `request_timeout_seconds`
- **CLI `fetch-bars`**：`--symbols`, `--bar-size`, `--start`, `--end`, `--ibkr-profile`

### `feat/ibkr-account-order-info` — 账户/订单 Gateway（已合并）

- **`IBKRAccountGateway`**（`gateways/ibkr_account.py`）：账户摘要、持仓、挂单查询
  - `get_account_summary()` / `get_positions()` / `get_open_orders()` 均已本地验证
- **`IBKRBrokerGateway`**（`gateways/ibkr_account.py`）：下单与撤单
  - `place_order()` / `cancel_order()` 完整流程本地验证（paper 账户，LMT BUY → PreSubmitted → 撤单成功）
  - Bug 修复：`eTradeOnly=False` / `firmQuoteOnly=False`（避免 error 10268）；`cancelOrder` try/except 版本兼容
- **CLI `show-account`**：显示账户摘要、持仓、挂单
- **`scripts/test_order_flow.py`**：端到端手动测试脚本

## Key Documents To Read First

1. `handoff/current_status.md`
2. `docs/architecture.md`
3. `docs/market-data-handoff.md`
4. `docs/market-data-capability-matrix.md`
5. `config/settings.example.toml`

## Verified Local Findings

### 账户/订单（paper account, IB Gateway running）

| Feature | Status |
| --- | --- |
| `probe_capabilities()` | ✅ Verified |
| `get_account_summary()` | ✅ Verified — net liquidation, cash, buying power |
| `get_positions()` | ✅ Verified |
| `get_open_orders()` | ✅ Verified — `reqAllOpenOrders` always triggers `openOrderEnd` |
| `place_order()` (LMT) | ✅ Verified — order reaches IBKR, status PreSubmitted |
| `cancel_order()` | ✅ Verified — order removed from open orders |

### 回测数据链路

| Feature | Status |
| --- | --- |
| DB hit (cached bars) | ✅ Verified via unit tests |
| ibkr fallback + DB write | ✅ Verified via unit tests |
| moomoo fallback | ✅ Verified via unit tests |
| yfinance fallback | ✅ Verified via unit tests |
| source priority dedup | ✅ Verified via unit tests |

## Code State

- 实盘行情入口：`python -m intraday_auto_trading.cli sync-market-data`
- 回测数据入口：`python -m intraday_auto_trading.cli fetch-bars`
- 账户查询入口：`python -m intraday_auto_trading.cli show-account`
- 主要实现文件：
  - `src/intraday_auto_trading/gateways/ibkr_market_data.py`
  - `src/intraday_auto_trading/gateways/ibkr_account.py`
  - `src/intraday_auto_trading/gateways/moomoo_options.py`
  - `src/intraday_auto_trading/gateways/yfinance_market_data.py`
  - `src/intraday_auto_trading/services/market_data_sync.py`
  - `src/intraday_auto_trading/services/backtest_data_service.py`
  - `src/intraday_auto_trading/persistence/market_data_repository.py`
  - `src/intraday_auto_trading/cli.py`
  - `src/intraday_auto_trading/app.py`

## Validation Status

- `pytest` passes（合并后预期 35 passed；需 `pytest` 确认）
- Real API verified (paper account): account summary / positions / open orders / place / cancel ✅
- Manual test script: `scripts/test_order_flow.py`

## Important Constraints

- `config/settings.toml` 本地私有，不入 git
- `profile.readonly=True`（代码层）阻止下单/撤单，不触碰网络
- IB Gateway 应用层 "Read-Only API" 开关（Configure → API → Settings）需关闭才能实际下单
- `account_client_id=10` / `broker_client_id=11` 须与行情 gateway 的 `client_id=9` 不同，避免连接冲突
- yfinance 为 optional dep（`pip install -e ".[yfinance]"`）；未安装时 `probe_capabilities()` 返回 UNAVAILABLE，不抛异常
- yfinance 1m bars 仅支持最近 7 天，15m bars 最近 60 天

## Best Next Steps For Claude

1. 合并 `feat/backtest-virtual-account` 到 main
2. 构建回测主循环（BacktestRunner）：用 `BacktestTrendInputLoader` + `TrendClassifier` + `VirtualAccount` 串联完整回测流程
3. 将 `IBKRAccountGateway` 和 `IBKRBrokerGateway` 接入 `app.py` / executor，实现完整实盘链路
4. 在回测中标定趋势分类阈值（当前初值：EARLY_BUY ≥ 0.25，WEAK_TAIL ≤ -0.20）
5. 将 tracker 状态持久化到 SQLite 或 Redis，避免进程重启丢单
6. Imbalance 数据可用后，为 TrendClassifier 增加第三路信号维度（接口已预留）

## Useful Commands

```powershell
$env:PYTHONPATH='src'
# 行情同步
python -m intraday_auto_trading.cli sync-market-data --providers ibkr --symbols SPY QQQ --start 2026-04-14T09:30 --end 2026-04-14T10:00
# 回测数据
python -m intraday_auto_trading.cli fetch-bars --symbols SPY --start 2026-04-15T09:30 --end 2026-04-15T10:00
python -m intraday_auto_trading.cli fetch-bars --symbols SPY --bar-size 15m --start 2026-04-14T09:30 --end 2026-04-14T16:00
# 账户查询
python -m intraday_auto_trading.cli show-account --ibkr-profile paper
# 下单/撤单端到端测试
python scripts/test_order_flow.py
# 测试
pytest
```

## Latest Validation Notes

- `Moomoo` bars are now connected for both `1m` and direct `15m`, and validated against a live OpenD session.
- `yfinance` single-symbol `15m` parsing was fixed so direct `yf.download` results now flow through the gateway correctly.
- Backtest-chain validation now covers:
  - `2026-04-16 09:30-10:00` TrendSignal generation
  - selection diagnostics CSV output
  - `2026-04-06` to `2026-04-10` 15m low-tracking validation
- Full-session 15m tracking was validated with `Moomoo` as the working source:
  - `JEPI`: `130` bars, `16` tracking events
  - `JEPQ`: `130` bars, `17` tracking events
  - `SCHD`: `130` bars, `16` tracking events
  - `DGRW`: `130` bars, `14` tracking events
- Validation artifacts were written under:
  - `artifacts/backtest_chain_validation/2026-04-16/core_tracking_window_2026-04-06_2026-04-10_full_session/`
- Current observed issue:
  - tracking limit prices are still too high relative to the intended low-follow behavior
- Focus for the next agent:
  - separate live/backtest source policies
  - tune the low-follow pricing rule before relying on tracking-order outputs

## TODO

- Separate live-trading and backtest data-source logic into different policies.
- Fix the current mismatch where live validation, live sync, and backtest flows do not share the same source-selection model.
- Tracking limit prices in the 15m low-follow module are still too aggressive/high and need separate tuning or a different pricing rule.
- Define live-source policy explicitly:
  - real-time trading input should use its own provider selection rules
  - no accidental reuse of backtest fallback behavior
- Define backtest-source policy explicitly:
  - local DB cache first
  - then broker and fallback sources only when appropriate for backtest
  - validation commands must not silently override the default source policy without a clear reason
- Review source priority independently for:
  - bars
  - session metrics
  - option quotes
  - opening imbalance
- Refactor the entry points so source priority is configured in one place per runtime mode instead of being scattered across services.
