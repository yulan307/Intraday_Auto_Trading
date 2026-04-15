# Market Data Handoff

## Branch

- Current working branch: `feat/market-data-pipeline`

## What Is Implemented

### Shared Pipeline

- Provider capability reporting and per-symbol sync results are modeled and exposed through the CLI.
- `intraday-auto-trading sync-market-data` can:
  - probe provider capabilities
  - fetch data
  - persist successful results to SQLite
  - show partial failures without hiding successful writes
- The sync service now treats provider-specific failures as item-level failures instead of aborting the entire run.

### IBKR

- Runtime baseline is `IB Gateway`.
- Dual profile config is supported through `ibkr.paper` and `ibkr.live`.
- Verified implementation exists for:
  - `1m bars`
  - provider direct `15m bars`
  - locally derived `15m bars`
  - session metrics fallback from bars
- Opening imbalance support is now implemented through auction market data ticks:
  - auction volume
  - auction price
  - auction imbalance
- Current verified blocker:
  - `10089` on opening imbalance requests in the tested paper environment
- Option discovery capability was validated experimentally with `reqSecDefOptParams`.
- Current verified blocker for option quotes:
  - `354` missing option market data subscription

### Moomoo

- Runtime baseline is `OpenD`.
- Single-account config is supported through `moomoo.*`.
- Verified implementation exists for:
  - option chain discovery
  - option snapshot retrieval
  - persistence of option contracts and option quotes
- Local capability check also confirmed:
  - `get_cur_kline` can support bars
  - `get_market_snapshot` can support session metrics inputs
- Opening imbalance is still unimplemented because no confirmed public OpenD API was found for it.

## Documents Added

- `docs/market-data-pipeline-plan.md`
- `docs/market-data-capability-matrix.md`
- `docs/market-data-handoff.md`

## Suggested Next Steps

1. Make the `bars / session metrics` source configurable between `IBKR` and `Moomoo`.
2. Decide whether `session metrics` should be strictly provider-native or allowed to be derived from snapshots and bars.
3. Leave `opening imbalance` behind a clear feature flag until a usable broker entitlement or public API path is confirmed.
4. Leave `IBKR options` behind capability probing until option subscriptions are available.
5. If `Moomoo` becomes the preferred bar source, add a real `Moomoo` bar gateway alongside the current options gateway.

## Useful Local Validation Commands

```powershell
$env:PYTHONPATH='src'
python -m intraday_auto_trading.cli show-config
python -m intraday_auto_trading.cli sync-market-data --providers ibkr --symbols SPY QQQ --start 2026-04-14T09:30 --end 2026-04-14T10:00
python -m intraday_auto_trading.cli sync-market-data --providers moomoo --symbols AAPL --end 2026-04-15T19:25
```

## Known Constraints

- `config/settings.toml` is local-only and should not be committed.
- `IBKR opening imbalance` currently depends on extra market data entitlements.
- `IBKR options` currently depend on option market data subscriptions.
- `Moomoo opening imbalance` has no confirmed public API path yet.
