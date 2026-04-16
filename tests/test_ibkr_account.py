from __future__ import annotations

import pytest

from intraday_auto_trading.config import IBKRProfileSettings
from intraday_auto_trading.gateways.ibkr_account import IBKRAccountGateway, IBKRBrokerGateway
from intraday_auto_trading.models import BuyStrategy, CapabilityStatus, OrderInstruction


def _unreachable_profile() -> IBKRProfileSettings:
    return IBKRProfileSettings(
        host="127.0.0.1",
        port=19999,  # nothing listening here
        client_id=9,
        account_id="TEST",
        readonly=True,
        account_client_id=10,
        broker_client_id=11,
    )


class TestIBKRAccountGatewayCapabilities:
    def test_reports_unavailable_without_runtime_backend(self):
        gateway = IBKRAccountGateway(
            profile_name="paper",
            profile=_unreachable_profile(),
        )
        capabilities = gateway.probe_capabilities()
        assert capabilities.account_summary in {CapabilityStatus.UNAVAILABLE, CapabilityStatus.UNTESTED}
        assert capabilities.positions in {CapabilityStatus.UNAVAILABLE, CapabilityStatus.UNTESTED}
        assert capabilities.open_orders in {CapabilityStatus.UNAVAILABLE, CapabilityStatus.UNTESTED}

    def test_probe_capabilities_does_not_raise(self):
        gateway = IBKRAccountGateway(
            profile_name="paper",
            profile=_unreachable_profile(),
        )
        # Must not raise regardless of whether IB Gateway is available
        capabilities = gateway.probe_capabilities()
        assert capabilities.provider == "ibkr-paper"


class TestIBKRBrokerGatewayReadonlyGuard:
    def test_place_order_raises_when_readonly(self):
        gateway = IBKRBrokerGateway(
            profile_name="paper",
            profile=_unreachable_profile(),
        )
        instruction = OrderInstruction(
            symbol="SPY",
            strategy=BuyStrategy.IMMEDIATE_BUY,
            quantity=1,
            limit_price=500.0,
        )
        with pytest.raises(RuntimeError, match="readonly"):
            gateway.place_order(instruction)

    def test_cancel_order_raises_when_readonly(self):
        gateway = IBKRBrokerGateway(
            profile_name="paper",
            profile=_unreachable_profile(),
        )
        with pytest.raises(RuntimeError, match="readonly"):
            gateway.cancel_order("12345")

    def test_place_order_readonly_check_precedes_network(self):
        """readonly guard must fire before any socket connection attempt."""
        gateway = IBKRBrokerGateway(
            profile_name="paper",
            profile=_unreachable_profile(),
            socket_timeout_seconds=0.01,  # would time out quickly if reached
        )
        instruction = OrderInstruction(
            symbol="AAPL",
            strategy=BuyStrategy.IMMEDIATE_BUY,
            quantity=1,
        )
        # Should raise RuntimeError about readonly, not a connection timeout
        with pytest.raises(RuntimeError, match="readonly"):
            gateway.place_order(instruction)
