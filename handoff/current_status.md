# Current Handoff Status

## Snapshot

- Date: `2026-04-16`
- Updated by: `Claude`
- Current branch: `feat/ibkr-account-order-info`
- Base branch: `main`
- Remote status: pushed to `origin/feat/ibkr-account-order-info`
- Latest commit: `23d0e4f` `fix: resolve order placement and cancellation issues in IBKRBrokerGateway`

## What Was Just Completed

Implemented IBKR account info, position, and order querying, plus order placement and cancellation.

### New gateway: `gateways/ibkr_account.py`

- `IBKRAccountGateway` — implements `AccountGateway`:
  - `probe_capabilities()`: socket-level reachability check, returns `AccountCapabilities`
  - `get_account_summary()`: net liquidation, cash balance, buying power via `reqAccountSummary`
  - `get_positions()`: full position list via `reqPositions`
  - `get_open_orders()`: all open orders via `reqAllOpenOrders` (not `reqOpenOrders` — avoids timeout when no orders exist for current client_id)
  - `get_completed_orders_this_week(symbol)` and `has_open_position(symbol)`: derived from above
- `IBKRBrokerGateway` — implements `BrokerGateway`:
  - `place_order(instruction)`: limit or market order via `placeOrder`; `readonly=True` raises `RuntimeError` before any network call
  - `cancel_order(broker_order_id)`: via `cancelOrder`; same readonly guard

Both gateways share a single `_IBAccountApp` EWrapper class and use independent `client_id` values (`account_client_id`, `broker_client_id`) to avoid conflicts with the market data gateway.

### Other changes

- `models.py`: added `AccountSummary`, `Position`, `Order`, `AccountCapabilities`
- `interfaces/brokers.py`: expanded `AccountGateway` with 4 new methods; `BrokerGateway` unchanged (already had `place_order` / `cancel_order`)
- `config.py` + `settings.example.toml`: added `account_client_id=10` / `broker_client_id=11` to `IBKRProfileSettings`
- `cli.py`: added `show-account [--ibkr-profile paper|live]` command
- `gateways/__init__.py`: exports `IBKRAccountGateway`, `IBKRBrokerGateway`
- `tests/test_ibkr_account.py`: 5 offline tests (capability probe + readonly guard)

## Key Documents To Read First

1. `handoff/current_status.md`
2. `docs/architecture.md` (section 7 — account/broker gateway)
3. `docs/market-data-handoff.md`
4. `docs/market-data-capability-matrix.md`
5. `config/settings.example.toml`

## Verified Local Findings (this branch)

### Account query (paper account, IB Gateway running)

| Feature | Status |
| --- | --- |
| `probe_capabilities()` | ✅ Verified — correctly identifies IB Gateway as reachable |
| `get_account_summary()` | ✅ Verified — returns net liquidation, cash, buying power |
| `get_positions()` | ✅ Verified — returns empty list (paper account, no positions) |
| `get_open_orders()` | ✅ Verified after fix — `reqAllOpenOrders` always triggers `openOrderEnd` |
| `place_order` readonly guard | ✅ Verified — raises `RuntimeError` before any network call |
| `place_order` (LMT order) | ✅ Verified — order reaches IBKR, status PreSubmitted |
| `cancel_order` | ✅ Verified — order disappears from open orders after cancel |

### Fixed bugs (this session)

| Bug | Fix |
| --- | --- |
| `place_order` → IBKR error 10268 | Added `order.eTradeOnly = False` and `order.firmQuoteOnly = False` to `_build_order`; IBOrder defaults these to True which IBKR rejects |
| `cancel_order` → `TypeError: cancelOrder() takes 2 positional arguments but 3 were given` | Added try/except fallback: newer ibapi requires `cancelOrder(orderId, manualOrderCancelTime)`, older only `cancelOrder(orderId)` |

### Previous blocker (resolved)

IB Gateway "Read-Only API" toggle (Configure → API → Settings) must be **disabled** for place/cancel to work.
This is separate from `profile.readonly` in code. Error 321 is the symptom when it is still enabled.

## Code State

- Account/order query entrypoint: `python -m intraday_auto_trading.cli show-account --ibkr-profile paper`
- Main implementation files:
  - `src/intraday_auto_trading/gateways/ibkr_account.py`
  - `src/intraday_auto_trading/interfaces/brokers.py`
  - `src/intraday_auto_trading/models.py`
  - `src/intraday_auto_trading/config.py`
  - `src/intraday_auto_trading/cli.py`

## Validation Status

- `pytest` passes: `15 passed`
- Real API verified (paper account, IB Gateway running):
  - `get_account_summary()` ✅
  - `get_positions()` ✅
  - `get_open_orders()` ✅
  - `place_order()` ✅ — LMT BUY, status PreSubmitted
  - `cancel_order()` ✅ — order removed from open orders
- Manual test script: `scripts/test_order_flow.py`

## Important Constraints

- `config/settings.toml` is local-only and git-ignored
- `profile.readonly=True` (code-level guard) prevents place/cancel before any network call
- IB Gateway "Read-Only API" (application-level) is a separate toggle — must be disabled in IB Gateway UI to allow order placement
- `account_client_id` and `broker_client_id` must differ from `client_id` used by `IBKRMarketDataGateway` to avoid connection conflicts

## Best Next Steps For Claude

1. Merge this branch into `main` (place/cancel fully verified, ready)
2. Make `bars / session metrics` source configurable between IBKR and Moomoo
3. Decide whether `session metrics` should remain provider-native or derived from bars and snapshots
4. If Moomoo becomes the preferred bar source, add a dedicated Moomoo bar gateway
5. Wire `IBKRAccountGateway` and `IBKRBrokerGateway` into `app.py` / executor for live trading

## Useful Commands

```powershell
# Show account summary, positions, open orders
python -m intraday_auto_trading.cli show-account --ibkr-profile paper

# Full order flow test (place → query → cancel)
python scripts/test_order_flow.py
python scripts/test_order_flow.py --symbol AAPL --qty 1 --limit 1.00

# Run all tests
pytest

# Sync market data
python -m intraday_auto_trading.cli sync-market-data --providers ibkr --symbols SPY QQQ --start 2026-04-14T09:30 --end 2026-04-14T10:00
```
