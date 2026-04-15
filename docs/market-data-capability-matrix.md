# Market Data Capability Matrix

## Scope

This matrix summarizes the current evidence for the three target data categories across the two broker integrations:

- `bars / session metrics`
- `opening imbalance`
- `options`

Each entry is evaluated on two axes:

- `API capability`: whether the broker API appears to support the data type.
- `Current environment`: whether the data is usable today in the local verified setup.

## Matrix

| Data Type | IBKR | Moomoo |
| --- | --- | --- |
| `bars / session metrics` | `Supported and currently usable.` Historical bar requests through `IB Gateway` are already implemented and verified for `1m`, provider direct `15m`, and locally derived `15m`. Session metrics can be computed from bars when needed. | `Supported and currently usable.` `OpenD` can return minute K-lines and market snapshots. Verified locally with `US.AAPL` via `get_cur_kline` and `get_market_snapshot`, which provide enough fields for `official_open`, `last_price`, and a session metric approximation. |
| `opening imbalance` | `API capability exists, but not currently usable in this environment.` `IBKR` exposes auction-related ticks, and the code now requests them through `reqMktData(..., genericTickList='225')`. Local verification returned `IBKR error 10089`, which indicates the paper account is missing the required real-time market data entitlement. | `No confirmed public API capability.` No official OpenD endpoint was found for auction imbalance, indicative open, or paired shares. A local SDK surface scan also did not reveal a corresponding public method. |
| `options` | `API capability exists, but not currently usable in this environment.` Local verification confirmed that `reqSecDefOptParams` can return expirations and strikes for `SPY`, so option chain discovery is available. Real-time quote requests for a valid option contract currently return `IBKR error 354`, which indicates missing option market data subscriptions. | `Supported and currently usable.` `OpenD` option chain plus market snapshot requests are already wired into the sync pipeline and were verified locally with `AAPL`, producing and persisting live option contracts and quotes. |

## Verified Local Findings

### IBKR

- `bars / session metrics`
  - Verified through `IB Gateway paper`.
  - Real requests and replies were observed in the IBKR client.
  - SQLite persistence was validated.
- `opening imbalance`
  - Request path implemented and verified at transport level.
  - Current blocker is entitlement `10089`, not code connectivity.
- `options`
  - Contract universe discovery works.
  - Quote and Greeks retrieval are blocked by subscription `354`.

### Moomoo

- `bars / session metrics`
  - Verified through `OpenD` at `127.0.0.1:11111`.
  - `get_cur_kline('US.AAPL', ..., K_1M)` returned minute bars.
  - `get_market_snapshot(['US.AAPL'])` returned `open_price`, `last_price`, `avg_price`, `volume`, and `turnover`.
- `opening imbalance`
  - No verified API path yet.
- `options`
  - Verified through `get_option_chain` plus `get_market_snapshot`.
  - `AAPL` option quotes were successfully written to SQLite.

## Current Recommendation

If the source choice were made strictly on today's verified environment:

- `bars / session metrics`: both `IBKR` and `Moomoo` are viable candidates.
- `opening imbalance`: neither source is ready for production use today.
- `options`: `Moomoo` is the only currently usable source.

Source selection should remain configurable because the broker capabilities are asymmetric and may change after entitlements are added.
