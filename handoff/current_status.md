# Current Handoff Status

## Snapshot

- Date: `2026-04-16`
- Updated by: `Codex`
- Current branch: `main`
- Remote status: `main` is in sync with `origin/main`
- Latest merged commit: `3709321` `implement market data pipeline`

## What Was Just Completed

- Merged `feat/market-data-pipeline` into `main`
- Pushed the merged result to `origin/main`
- Added and verified the market data pipeline for:
  - provider capability probing
  - SQLite persistence
  - CLI sync command
  - `IBKR` via `IB Gateway`
  - `Moomoo` via `OpenD`
- Added project handoff and capability documents for the next agent

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

- Main market data entrypoint:
  - `python -m intraday_auto_trading.cli sync-market-data`
- Main implementation areas:
  - `src/intraday_auto_trading/cli.py`
  - `src/intraday_auto_trading/app.py`
  - `src/intraday_auto_trading/config.py`
  - `src/intraday_auto_trading/models.py`
  - `src/intraday_auto_trading/interfaces/brokers.py`
  - `src/intraday_auto_trading/services/market_data_sync.py`
  - `src/intraday_auto_trading/gateways/ibkr_market_data.py`
  - `src/intraday_auto_trading/gateways/moomoo_options.py`

## Validation Status

- `pytest` passes
- Latest verified result before handoff:
  - `10 passed`

## Important Constraints

- `config/settings.toml` is local-only and is ignored by git
- `IBKR opening imbalance` is implemented but not usable without the required market data entitlement
- `IBKR options` are not usable for live quotes until option market data subscriptions are enabled
- `Moomoo opening imbalance` should be treated as unsupported until a confirmed public API path is found

## Best Next Steps For Claude

1. Make the `bars / session metrics` source configurable between `IBKR` and `Moomoo`
2. Decide whether `session metrics` should remain provider-native or may be derived from bars and snapshots
3. Keep `opening imbalance` behind explicit capability checks and feature flags
4. Keep `IBKR options` behind capability checks until subscriptions are available
5. If `Moomoo` is selected as the preferred bar source, add a dedicated `Moomoo` bar gateway instead of routing only options through it

## Useful Commands

```powershell
$env:PYTHONPATH='src'
python -m intraday_auto_trading.cli show-config
python -m intraday_auto_trading.cli sync-market-data --providers ibkr --symbols SPY QQQ --start 2026-04-14T09:30 --end 2026-04-14T10:00
python -m intraday_auto_trading.cli sync-market-data --providers moomoo --symbols AAPL --end 2026-04-15T19:25
pytest
```
