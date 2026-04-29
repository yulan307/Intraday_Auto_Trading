# Current Handoff Status

## Snapshot

- Date: `2026-04-20`
- Updated by: `Claude`
- Current branch: `feat/unified-bar-data-service` → merged to `main`
- Remote status: pushed & merged

## In Progress - 2026-04-28 Codex

- Rebuild market-data storage around bar-only data for the current phase.
- Back up the existing `data/market_data.sqlite` before creating a new database.
- Replace `daily_coverage` semantics with `bar_request_log`, recording fetch windows, source, status, expected/actual bars, and messages.
- Keep all stored bar timestamps as UTC-naive; exchange-local session windows must be converted with `ZoneInfo("America/New_York")` so EST/EDT changes are automatic.

### Risks / Blockers

- Existing `session_metrics`, `option_contracts`, `option_quotes`, `opening_imbalance`, and `trend_snapshots` data will remain only in the backup database for now.
- Bar request cache records must be rebuilt after timezone fixes; stale `daily_coverage` rows must not influence new fetches.
- Historical 1m data availability still depends on the provider; yfinance cannot provide older 1m ranges beyond its retention window.
- The IBKR-only fetch must wait until IB Gateway is running and reachable; do not execute it before the user confirms Gateway startup.
- Existing `no_data` rows created during provider failures may need cleanup or force refresh before the next VPN-off retry.
- IBKR/HMDS errors such as "Trading TWS session is connected from a different IP address" should remain visible in `bar_request_log.message`.
- Existing non-IBKR bar rows, if any, should be ignored by fixed-source reads unless an explicit legacy path is intentionally used.

### Next Steps

1. Replace the market-data schema with `symbols`, `price_bars`, and `bar_request_log`.
2. Update `BarDataService` and repository methods to use `bar_request_log`.
3. Make `fetch-symbol-pool-data` fetch bar data only for now.
4. Add DST-aware tests for EST and EDT session windows and provider timestamp normalization.
5. Run `fetch-symbol-pool-data --start-date 2026-02-01 --end-date 2026-04-27 --bar-providers ibkr` after IB Gateway is started.
6. Add failed/error request-log status handling and daily bar provider implementations before the next data retry.

## Completed - 2026-04-28 Codex

- Backed up the previous database to `data/market_data.backup_20260428_before_bar_schema.sqlite`.
- Recreated `data/market_data.sqlite` with only `symbols`, `price_bars`, and `bar_request_log`.
- Replaced `daily_coverage` usage with `bar_request_log` in the unified bar fetch path.
- Updated `fetch-symbol-pool-data` to fetch bar data only for the current phase.
- Fixed provider timestamp normalization so stored bars are UTC-naive and session windows use `America/New_York` for automatic EST/EDT conversion.
- Added DST tests for 2026-02-02 EST (`14:30-21:00 UTC`) and 2026-04-16 EDT (`13:30-20:00 UTC`).
- Full test suite: `131 passed`.
- Added `fetch-symbol-pool-data --bar-providers` so the next symbol-pool bar fetch can be restricted to IBKR Gateway only.
- Added a `BarDataService.get_bars(..., source_order=...)` override for explicit per-run provider routing.
- Added tests for IBKR-only source override and symbol-pool provider forwarding.
- Full test suite after provider override: `133 passed`.
- Hardened bar fetching after IBKR/HMDS failures: provider exceptions are now recorded as `failed` request logs with messages, not cached as `no_data`.
- Added per-symbol fallback when provider batch calls fail, preventing one symbol failure from marking the whole day/group as no-data.
- Added daily bar methods for IBKR, Moomoo, and yfinance gateways.
- Stopped active market-data sync from writing direct or derived `15m` bars; `fetch-bars` CLI now accepts only `1m` and `1d`.
- Added `fetch-symbol-pool-data --force-refresh` to ignore existing request-log rows when retrying after network/provider failures.
- Full test suite after failure handling/daily-bar changes: `137 passed`.
- Fixed current bar-fetch defaults to IB Gateway (`ibkr`) for `DataFetchPolicy`, symbol-pool fetches, `fetch-bars`, and backtest-chain validation.
- Restricted `fetch-symbol-pool-data --bar-providers` to the compatibility value `ibkr`; programmatic non-IBKR bar provider requests now raise `ValueError`.
- Added `services/bar_metrics.py` and derived `session_vwap`, official open, and last price from loaded `1m` bars in `TrendInputLoader`.
- Updated default config and examples to `providers = ["ibkr"]`, `data_types = ["bars"]`, with direct/derived 15m disabled.
- Full test suite after IBKR-only/VWAP consolidation: `143 passed`.

---

## What Was Just Completed

### `feat/unified-bar-data-service` — 统一 Bar 数据服务重构

#### 背景 / 动机

`BacktestDataService._fetch_one()` 在 DB 有任何记录时就立即返回（不检查数量），导致部分数据（如 4/16 仅 30 bars）被误判为完整而不再补全。

本次重构新增 `daily_coverage` 表追踪每日数据完整性，并将 live/backtest 数据获取统一为单一入口。

#### 新增 / 改动文件

| 文件 | 改动 |
|------|------|
| `src/.../persistence/schema.py` | 新增 `daily_coverage` 表 |
| `src/.../models.py` | 新增 `DailyCoverage` dataclass |
| `src/.../interfaces/repositories.py` | 新增 `save/load/load_range` 三个 coverage 方法到 Protocol |
| `src/.../persistence/market_data_repository.py` | 实现三个 coverage 方法（SQLite upsert / select） |
| `src/.../services/bar_data_service.py` | **新建** — `BarDataService` 统一入口 |
| `src/.../app.py` | 新增 `build_bar_data_service()` 工厂方法 |
| `scripts/tracker_chart.py` | 改用 `build_bar_data_service` + `get_bars(date, date)` |
| `scripts/backfill_daily_coverage.py` | **新建** — 一次性回填迁移脚本 |
| `tests/test_bar_data_service.py` | **新建** — 6 个单元测试，全通过 |
| `docs/architecture.md` | 第 7 节更新为 `BarDataService` 说明 |
| `config/symbol_group.toml` | 修复全角逗号导致的 TOML 解析错误 |

#### `daily_coverage` 语义

```
is_complete=1, actual_bars>0  → DB 数据完整，直接使用
is_complete=1, actual_bars=0  → 确认该日无数据（未上市等）
is_complete=0                 → 部分或未拉取，触发重新获取
```

#### `BarDataService.get_bars()` 接口

```python
get_bars(
    symbols: list[str],
    bar_size: str,          # "1m" / "15m" / "1d"
    start_date: date,
    end_date: date,
) -> dict[str, list[MinuteBar]]
```

- bar_size < 1d 时自动使用当天完整交易时段（9:30–16:00 ET，转为 UTC 存储）
- `trade_date >= today ET` → `live_source_order`（ibkr → moomoo）
- `trade_date < today ET`  → `history_source_order`（yfinance → moomoo → ibkr）

#### 迁移注意

部署到新环境后，需运行一次 backfill：
```bash
PYTHONPATH=src python3 scripts/backfill_daily_coverage.py
```
已在本地 DB 执行，写入 47 条 coverage 记录。

#### 测试结果

```
109 passed, 1 failed (pre-existing moomoo-api env issue), 0 new failures
```

---

## What Was Previously Completed

### `feat/ibkr-v2-signal` — V2 日内低点信号（已合并）

- 删除 `FifteenMinuteTracker`，以 `IntradayLowSignalService` 替代
- 新增 `services/intraday_low_signal.py`：`pullback_ok (close < ema20) AND reversal_ok (A|B|C)`
- limit_price = `round(min(vwap, prev_bar_mid), 2)`
- `scripts/tracker_chart.py`：1m K 线 + EMA5/EMA20/VWAP/PrevBarMid 参考线 + PLACE/FORCE 标注

### `feat/data-pipeline-refactor` — 数据链路统一重构（已合并）

- **`DataFetchPolicy`**：`db_source_priority` / `live_source_order` / `history_source_order`
- **`TrendInputLoader`**：DB 优先，eval_time 自动判 live vs historical，session metrics 推算兜底
- `app.py` 单一 `build_trend_input_loader()` 工厂方法

### `feat/backtest-virtual-account` — 虚拟账户模块（已完成，待合并）

- `VirtualAccount`：纯内存，满足 `AccountGateway + BrokerGateway`，支持 `process_bar()` 自动撮合
- `SqliteBacktestAccountRepository`：持久化回测运行元数据和订单历史

### `feat/ibkr-account-order-info` — 账户/订单 Gateway（已合并）

- `IBKRAccountGateway` + `IBKRBrokerGateway`：账户摘要、持仓、挂单、下单、撤单（paper 验证通过）

---

## Key Documents To Read First

1. `handoff/current_status.md`
2. `docs/architecture.md`
3. `docs/market-data-handoff.md`
4. `config/settings.example.toml`

---

## Code Entry Points

```bash
# 统一 bar 数据（实盘/回测共用）
from intraday_auto_trading.app import build_bar_data_service
svc = build_bar_data_service(settings)
bars = svc.get_bars(["JEPI","JEPQ"], "1m", date(2026,4,13), date(2026,4,17))

# 可视化分析图
PYTHONPATH=src python3 scripts/tracker_chart.py

# 回填 daily_coverage（新环境首次运行）
PYTHONPATH=src python3 scripts/backfill_daily_coverage.py

# 全量测试
python3 -m pytest --ignore=tests/test_moomoo_gateway.py
```

---

## Important Constraints

- `config/settings.toml` 本地私有，不入 git
- `config/symbol_group.toml` 需保持 ASCII 标点（已修复全角逗号问题）
- `BacktestDataService` 保留供 `BacktestChainValidationService` 使用，不影响现有链路
- yfinance 1m bars 仅支持最近 7 天，15m bars 最近 60 天

---

## Best Next Steps

1. **构建 BacktestRunner**：用 `BarDataService` + `TrendInputLoader` + `VirtualAccount` 串联完整回测流程
2. **合并 `feat/backtest-virtual-account`**：VirtualAccount 已完成，尚未并入 main
3. **CLI `fetch-bars` 迁移**：将 `fetch-bars` 命令对接 `BarDataService`，替换旧 `BacktestDataService` 调用
4. **接入实盘链路**：`IBKRAccountGateway` + `IBKRBrokerGateway` 接入 `app.py` / executor
5. **Opening imbalance**：接口已预留，待 IBKR entitlement 或 Moomoo API 支持后接入 `TrendClassifier`
