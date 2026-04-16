from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from intraday_auto_trading.app import build_backtest_data_service, build_market_data_sync_service
from intraday_auto_trading.config import load_settings
from intraday_auto_trading.gateways.ibkr_account import IBKRAccountGateway
from intraday_auto_trading.models import SyncStatus


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="intraday-auto-trading")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("show-config", help="Print the resolved project configuration.")

    sync_parser = subparsers.add_parser(
        "sync-market-data",
        help="Probe providers, fetch market data, and persist successful results into SQLite.",
    )
    sync_parser.add_argument("--symbols", nargs="+", help="Optional symbol override.")
    sync_parser.add_argument("--providers", nargs="+", help="Optional provider override.")
    sync_parser.add_argument(
        "--ibkr-profile",
        choices=["paper", "live"],
        help="Override the configured IBKR profile for this run.",
    )
    sync_parser.add_argument("--start", help="Start datetime in ISO format, for example 2026-04-15T09:30.")
    sync_parser.add_argument("--end", help="End datetime in ISO format, for example 2026-04-15T10:30.")

    fetch_parser = subparsers.add_parser(
        "fetch-bars",
        help="Fetch OHLCV bars: DB first, then ibkr/moomoo/yfinance fallback.",
    )
    fetch_parser.add_argument("--symbols", nargs="+", help="Optional symbol override.")
    fetch_parser.add_argument(
        "--bar-size",
        choices=["1m", "15m"],
        default="1m",
        help="Bar size to fetch (default: 1m).",
    )
    fetch_parser.add_argument(
        "--ibkr-profile",
        choices=["paper", "live"],
        help="Override the configured IBKR profile for this run.",
    )
    fetch_parser.add_argument("--start", help="Start datetime in ISO format, for example 2026-04-15T09:30.")
    fetch_parser.add_argument("--end", help="End datetime in ISO format, for example 2026-04-15T10:30.")

    account_parser = subparsers.add_parser(
        "show-account",
        help="Query and display IBKR account summary, positions, and open orders.",
    )
    account_parser.add_argument(
        "--ibkr-profile",
        choices=["paper", "live"],
        help="Override the configured IBKR profile for this run.",
    )
    return parser


def main() -> None:
    config_path = Path("config/settings.toml")
    if not config_path.exists():
        print("Missing config/settings.toml. Copy config/settings.example.toml first.")
        return

    parser = build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return

    settings = load_settings(config_path)

    if args.command == "show-config":
        print_config(settings)
        return

    if args.command == "sync-market-data":
        symbols = [symbol.upper() for symbol in (args.symbols or settings.symbols)]
        providers = [provider.lower() for provider in (args.providers or settings.data.providers)]
        start, end = resolve_window(args.start, args.end, settings.project.timezone)
        service = build_market_data_sync_service(settings, ibkr_profile_override=args.ibkr_profile)
        summary = service.sync_market_data(symbols=symbols, providers=providers, start=start, end=end)
        print_sync_summary(summary)
        return

    if args.command == "fetch-bars":
        symbols = [symbol.upper() for symbol in (args.symbols or settings.symbols)]
        start, end = resolve_window(args.start, args.end, settings.project.timezone)
        service = build_backtest_data_service(settings, ibkr_profile_override=args.ibkr_profile)
        results = service.get_bars(symbols=symbols, bar_size=args.bar_size, start=start, end=end)
        print_fetch_results(results)
        if all(r.source == "none" for r in results):
            raise SystemExit(1)
        return

    if args.command == "show-account":
        profile_name, profile = settings.ibkr.resolve_profile(args.ibkr_profile)
        gateway = IBKRAccountGateway(profile_name=profile_name, profile=profile)
        capabilities = gateway.probe_capabilities()
        print(f"[ibkr-{profile_name}] account_summary={capabilities.account_summary.value}  "
              f"positions={capabilities.positions.value}  "
              f"open_orders={capabilities.open_orders.value}")
        from intraday_auto_trading.models import CapabilityStatus
        if capabilities.account_summary is not CapabilityStatus.AVAILABLE:
            print("IB Gateway is not reachable. Start IB Gateway and try again.")
            raise SystemExit(1)
        summary = gateway.get_account_summary()
        print(f"\nAccount: {summary.account_id or profile.account_id}")
        print(f"  Net liquidation : ${summary.net_liquidation:>14,.2f}")
        print(f"  Cash balance    : ${summary.cash_balance:>14,.2f}")
        print(f"  Buying power    : ${summary.buying_power:>14,.2f}")
        positions = gateway.get_positions()
        print(f"\nPositions ({len(positions)}):")
        if positions:
            print(f"  {'Symbol':<10} {'Qty':>10} {'Avg Cost':>12} {'Mkt Value':>12} {'Unreal PnL':>12}")
            for pos in positions:
                print(f"  {pos.symbol:<10} {pos.quantity:>10.2f} {pos.avg_cost:>12.4f} "
                      f"{pos.market_value:>12.2f} {pos.unrealized_pnl:>12.2f}")
        else:
            print("  (none)")
        orders = gateway.get_open_orders()
        print(f"\nOpen orders ({len(orders)}):")
        if orders:
            print(f"  {'ID':<10} {'Symbol':<10} {'Action':<6} {'Qty':>8} {'Filled':>8} {'Status':<14} {'Limit':>10}")
            for o in orders:
                limit_str = f"{o.limit_price:.4f}" if o.limit_price is not None else "MKT"
                print(f"  {o.broker_order_id:<10} {o.symbol:<10} {o.action:<6} "
                      f"{o.total_qty:>8.0f} {o.filled_qty:>8.0f} {o.status:<14} {limit_str:>10}")
        else:
            print("  (none)")
        return


def print_config(settings) -> None:
    print(f"Project: {settings.project.name}")
    print(f"Timezone: {settings.project.timezone}")
    print(f"Symbols: {', '.join(settings.symbols)}")
    print(f"Providers: {', '.join(settings.data.providers)}")
    print(f"Market data DB: {settings.data.market_data_db}")
    print(f"IBKR default profile: {settings.ibkr.default_profile}")


def resolve_window(
    start_raw: str | None,
    end_raw: str | None,
    timezone_name: str,
) -> tuple[datetime, datetime]:
    timezone = ZoneInfo(timezone_name)
    now = datetime.now(timezone).replace(tzinfo=None, second=0, microsecond=0)
    start = datetime.fromisoformat(start_raw) if start_raw else now.replace(hour=9, minute=30)
    end = datetime.fromisoformat(end_raw) if end_raw else now
    if end < start:
        raise SystemExit("--end must be later than or equal to --start")
    return start, end


def print_sync_summary(summary) -> None:
    for capabilities in summary.provider_capabilities:
        print(f"[{capabilities.provider}] capabilities")
        for capability in (
            capabilities.bars_1m,
            capabilities.bars_15m_direct,
            capabilities.bars_15m_derived,
            capabilities.opening_imbalance,
            capabilities.options,
        ):
            suffix = f" - {capability.message}" if capability.message else ""
            print(f"  {capability.data_type.value}: {capability.status.value}{suffix}")

    if summary.results:
        print("results")

    for result in summary.results:
        suffix = f" - {result.message}" if result.message else ""
        print(
            f"  {result.provider}/{result.symbol}/{result.data_type.value}: "
            f"{result.status.value} ({result.saved_row_count}){suffix}"
        )

    if summary.has_failures():
        raise SystemExit(1)

    if not summary.results:
        raise SystemExit(0)

    if all(result.status in {SyncStatus.UNAVAILABLE, SyncStatus.UNSUPPORTED, SyncStatus.SKIPPED} for result in summary.results):
        raise SystemExit(2)


def print_fetch_results(results) -> None:
    for result in results:
        suffix = f" ({result.message})" if result.message else ""
        print(f"  {result.symbol:<6}: {result.bar_count} bars from {result.source}{suffix}")


if __name__ == "__main__":
    main()
