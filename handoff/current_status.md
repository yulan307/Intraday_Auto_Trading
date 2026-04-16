# Current Handoff Status

## Snapshot

- Date: `2026-04-16`
- Updated by: `Claude`
- Current branch: `feat/backtest-data-pipeline` (未合并，等待 review)
- Remote status: 本地分支，尚未 push
- Latest merged commit on main: `3709321` `implement market data pipeline`

## What Was Just Completed

### `feat/backtest-data-pipeline` — 回测数据链路

实现了独立的回测数据获取链路，与实盘 `sync-market-data` 并列：

- **`load_price_bars_with_source_priority()`**：repository 新增带优先级去重的读取方法，SQL 按 source 优先级排序，Python 端按 ts 去重，返回 `(bars, winning_source)`
- **`YfinanceMarketDataGateway`**（`gateways/yfinance_market_data.py`）：yfinance 第三方 bar 数据源，含 `YfinanceBackend` Protocol 和 `RealYfinanceBackend`；1m 最近 7 天，15m 最近 60 天；options/imbalance 标记为 UNSUPPORTED
- **`BacktestDataService`**（`services/backtest_data_service.py`）：DB 优先读取 → ibkr → moomoo → yfinance 顺序 fallback，成功拉取后立即写入 DB 缓存；`FetchResult` 记录每个 symbol 的来源与 bar 数量
- **`YfinanceSettings`**（`config.py`）：新增 `enabled` 和 `request_timeout_seconds`，`[yfinance]` 节已同步写入 `settings.example.toml`
- **`build_backtest_data_service()`**（`app.py`）：构建 BacktestDataService 的工厂函数
- **CLI `fetch-bars`**（`cli.py`）：`--symbols`, `--bar-size`, `--start`, `--end`, `--ibkr-profile`；输出每行 `SYMBOL: N bars from <source>`
- **测试**：`tests/test_yfinance_market_data.py`（11 项）、`tests/test_backtest_data_service.py`（9 项）；全套 30 tests 通过

## Key Documents To Read First

1. `handoff/current_status.md`
2. `docs/market-data-handoff.md`
3. `docs/market-data-capability-matrix.md`
4. `docs/market-data-pipeline-plan.md`
5. `config/settings.example.toml`

## Current Broker Capability Summary

| Data Type | IBKR | Moomoo |
| --- | --- | --- |
| `bars / session metrics` | Supported and verified | Supported and locally verified at API level |
| `opening imbalance` | Request path implemented, but blocked by entitlement `10089` in current paper environment | No confirmed public API path yet |
| `options` | Option chain discovery works, but quotes are blocked by subscription `354` | Supported and verified end-to-end |

## Verified Local Findings

### IBKR

- `1m` bars work
- direct `15m` bars work
- locally derived `15m` bars work
- session metrics can be derived from bars
- opening imbalance request is implemented through auction ticks
- current tested blocker for opening imbalance:
  - missing entitlement `10089`
- option chain discovery works through `reqSecDefOptParams`
- current tested blocker for option quotes:
  - missing subscription `354`

### Moomoo

- `OpenD` options pipeline is implemented and verified
- local run successfully fetched and persisted `AAPL` option contracts and quotes
- `get_cur_kline` and `get_market_snapshot` were verified locally, so `Moomoo` is a real candidate for `bars / session metrics`
- no confirmed public `opening imbalance` API was found

## Code State

- 实盘入口：`python -m intraday_auto_trading.cli sync-market-data`
- 回测入口：`python -m intraday_auto_trading.cli fetch-bars`
- 主要实现文件：
  - `src/intraday_auto_trading/cli.py`
  - `src/intraday_auto_trading/app.py`
  - `src/intraday_auto_trading/config.py`
  - `src/intraday_auto_trading/interfaces/repositories.py`
  - `src/intraday_auto_trading/persistence/market_data_repository.py`
  - `src/intraday_auto_trading/gateways/yfinance_market_data.py` ← 新增
  - `src/intraday_auto_trading/services/backtest_data_service.py` ← 新增
  - `src/intraday_auto_trading/gateways/ibkr_market_data.py`
  - `src/intraday_auto_trading/gateways/moomoo_options.py`

## Validation Status

- `pytest` passes — `30 passed`
- 测试环境：Python 3.11，无需真实网络连接（所有 gateway 均可 fake backend 注入）

## Important Constraints

- `config/settings.toml` 本地私有，不入 git
- yfinance 为 optional dep (`pip install -e ".[yfinance]"`)；未安装时 `probe_capabilities()` 返回 UNAVAILABLE，不抛异常
- yfinance 1m bars 仅支持最近 7 天，15m bars 最近 60 天；回测更长历史区间须从 ibkr/moomoo 获取
- `IBKR opening imbalance` 受 entitlement `10089` 限制，paper 环境不可用
- `IBKR options` 受 subscription `354` 限制，chain 发现可用，实时报价不可用
- `Moomoo opening imbalance` 暂无公开 API，标记为 UNSUPPORTED

## Best Next Steps For Claude

1. 合并 `feat/backtest-data-pipeline` 到 main（需先确认 CI/tests 通过）
2. 同步合并待 review 的 `feat/ibkr-account-order-info`（IBKRAccountGateway、IBKRBrokerGateway）
3. 使 bars/session metrics 来源可在 IBKR 与 Moomoo 之间配置切换
4. 补充更长历史区间的 bar 回测数据源（Polygon.io 或直接从 IBKR 历史 API 拉取）
5. 趋势分类逻辑替换为文档定义的完整开盘主导模型

## Useful Commands

```powershell
$env:PYTHONPATH='src'
python -m intraday_auto_trading.cli show-config
python -m intraday_auto_trading.cli sync-market-data --providers ibkr --symbols SPY QQQ --start 2026-04-14T09:30 --end 2026-04-14T10:00
python -m intraday_auto_trading.cli fetch-bars --symbols SPY --start 2026-04-15T09:30 --end 2026-04-15T10:00
python -m intraday_auto_trading.cli fetch-bars --symbols SPY --bar-size 15m --start 2026-04-14T09:30 --end 2026-04-14T16:00
pytest
```
