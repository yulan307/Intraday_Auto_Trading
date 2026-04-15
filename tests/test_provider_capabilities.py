from __future__ import annotations

from intraday_auto_trading.config import IBKRProfileSettings, MoomooSettings
from intraday_auto_trading.gateways.ibkr_market_data import IBKRMarketDataGateway
from intraday_auto_trading.gateways.moomoo_options import MoomooMarketDataGateway
from intraday_auto_trading.models import CapabilityStatus


def test_ibkr_gateway_reports_unavailable_without_runtime_backend() -> None:
    gateway = IBKRMarketDataGateway(
        profile_name="paper",
        profile=IBKRProfileSettings(host="127.0.0.1", port=4002, client_id=9),
    )

    capabilities = gateway.probe_capabilities()

    assert capabilities.bars_1m.status in {CapabilityStatus.UNAVAILABLE, CapabilityStatus.UNTESTED}
    assert capabilities.options.status is CapabilityStatus.UNSUPPORTED


def test_moomoo_gateway_reports_disabled_when_not_enabled() -> None:
    gateway = MoomooMarketDataGateway(
        MoomooSettings(enabled=False, host="127.0.0.1", port=11111),
    )

    capabilities = gateway.probe_capabilities()

    assert capabilities.options.status is CapabilityStatus.UNAVAILABLE
    assert capabilities.bars_1m.status is CapabilityStatus.UNSUPPORTED
