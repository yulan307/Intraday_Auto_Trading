"""
手动测试脚本：下单 → 查询 → 撤单完整流程
运行前确保：
  - IB Gateway (paper 账户) 已启动，监听 127.0.0.1:4002
  - IB Gateway 的 "Read-Only API" 已关闭（Configure → API → Settings）
  - client_id 10、11 未被其他进程占用

运行方式：
  python scripts/test_order_flow.py
  python scripts/test_order_flow.py --symbol AAPL --qty 1 --limit 1.00
"""
from __future__ import annotations

import argparse
import sys
import time

sys.path.insert(0, "src")

# Ensure stdout handles unicode on Windows consoles (cp932 etc.)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from intraday_auto_trading.config import IBKRProfileSettings
from intraday_auto_trading.gateways.ibkr_account import IBKRAccountGateway, IBKRBrokerGateway
from intraday_auto_trading.models import OrderInstruction, BuyStrategy


def build_profile(readonly: bool = False) -> IBKRProfileSettings:
    return IBKRProfileSettings(
        host="127.0.0.1",
        port=4002,
        client_id=9,
        account_client_id=10,
        broker_client_id=11,
        account_id="",
        readonly=readonly,
    )


def step(label: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Test IBKR order flow (place → query → cancel)")
    parser.add_argument("--symbol", default="SPY", help="Symbol to trade (default: SPY)")
    parser.add_argument("--qty", type=int, default=1, help="Order quantity (default: 1)")
    parser.add_argument("--limit", type=float, default=1.00,
                        help="Limit price far below market to avoid fill (default: 1.00)")
    args = parser.parse_args()

    profile_rw = build_profile(readonly=False)
    profile_ro = build_profile(readonly=True)
    account_gw = IBKRAccountGateway(profile_name="paper", profile=profile_ro)
    broker_gw = IBKRBrokerGateway(profile_name="paper", profile=profile_rw)

    # ── Step 0: probe capabilities ──────────────────────────────────────────
    step("0. Probe capabilities")
    caps = account_gw.probe_capabilities()
    print(f"  account_summary : {caps.account_summary.value}")
    print(f"  positions       : {caps.positions.value}")
    print(f"  open_orders     : {caps.open_orders.value}")

    # ── Step 1: account summary ─────────────────────────────────────────────
    step("1. Account summary")
    try:
        summary = account_gw.get_account_summary()
        print(f"  Net Liquidation : ${summary.net_liquidation:,.2f}")
        print(f"  Cash Balance    : ${summary.cash_balance:,.2f}")
        print(f"  Buying Power    : ${summary.buying_power:,.2f}")
    except Exception as e:
        print(f"  ERROR: {e}")
        sys.exit(1)

    # ── Step 2: existing positions ──────────────────────────────────────────
    step("2. Current positions")
    try:
        positions = account_gw.get_positions()
        if positions:
            for p in positions:
                print(f"  {p.symbol:<8} qty={p.quantity:>8.2f}  avg_cost={p.avg_cost:.4f}")
        else:
            print("  (no positions)")
    except Exception as e:
        print(f"  ERROR: {e}")

    # ── Step 3: existing open orders ────────────────────────────────────────
    step("3. Open orders (before placing)")
    try:
        orders_before = account_gw.get_open_orders()
        if orders_before:
            for o in orders_before:
                print(f"  id={o.broker_order_id}  {o.symbol:<6} {o.action}  qty={o.total_qty}  "
                      f"lmt={o.limit_price}  status={o.status}")
        else:
            print("  (no open orders)")
    except Exception as e:
        print(f"  ERROR: {e}")

    # ── Step 4: place limit order ───────────────────────────────────────────
    step(f"4. Place LMT BUY order: {args.symbol} x{args.qty} @ ${args.limit:.2f}")
    instruction = OrderInstruction(
        symbol=args.symbol,
        strategy=BuyStrategy.TRACKING_BUY,
        quantity=args.qty,
        limit_price=args.limit,
        rationale="manual test order",
    )
    try:
        order_id = broker_gw.place_order(instruction)
        print(f"  Order placed → broker_order_id = {order_id}")
    except Exception as e:
        print(f"  ERROR placing order: {e}")
        sys.exit(1)

    # 少量等待让 IB Gateway 处理
    time.sleep(2)

    # ── Step 5: query open orders after placing ─────────────────────────────
    step("5. Open orders (after placing)")
    try:
        orders_after = account_gw.get_open_orders()
        if orders_after:
            for o in orders_after:
                print(f"  id={o.broker_order_id}  {o.symbol:<6} {o.action}  qty={o.total_qty}  "
                      f"lmt={o.limit_price}  status={o.status}")
        else:
            print("  (no open orders — order may have been rejected or already filled)")
    except Exception as e:
        print(f"  ERROR: {e}")

    # ── Step 6: cancel order ────────────────────────────────────────────────
    step(f"6. Cancel order {order_id}")
    try:
        broker_gw.cancel_order(order_id)
        print(f"  cancelOrder({order_id}) sent")
    except Exception as e:
        print(f"  ERROR cancelling order: {e}")

    time.sleep(2)

    # ── Step 7: query open orders after cancel ──────────────────────────────
    step("7. Open orders (after cancel)")
    try:
        orders_final = account_gw.get_open_orders()
        target = next((o for o in orders_final if o.broker_order_id == order_id), None)
        if target:
            print(f"  id={target.broker_order_id}  {target.symbol}  status={target.status}")
        else:
            print(f"  Order {order_id} no longer in open orders ✓")
    except Exception as e:
        print(f"  ERROR: {e}")

    print("\n" + "="*60)
    print("  Test complete")
    print("="*60 + "\n")


if __name__ == "__main__":
    main()
