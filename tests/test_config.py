from __future__ import annotations

from pathlib import Path

from intraday_auto_trading.config import load_settings


def test_load_settings_supports_dual_ibkr_profiles(tmp_path: Path) -> None:
    config_path = tmp_path / "settings.toml"
    config_path.write_text(
        """
[project]
name = "intraday-auto-trading"
timezone = "America/New_York"
paper_trading = true
currency = "USD"

[symbols]
pool = ["SPY"]

[strategy]
tracking_confirmation_bars = 2
tracking_limit_price_factor = 1.01
force_buy_last_minutes = 15
opening_review_cutoff = "10:00"

[selection]
weak_tail_weight = 3.0
range_track_weight = 2.0
early_buy_weight = 1.0
unbought_bonus = 2.0
recent_fill_penalty_step = 0.5

[data]
market_data_db = "data/market_data.sqlite"
providers = ["ibkr"]
data_types = ["bars"]
enable_direct_15m = true
enable_derived_15m = true

[ibkr]
default_profile = "live"

[ibkr.paper]
host = "127.0.0.1"
port = 4002
client_id = 9
account_id = "DU123"
readonly = true

[ibkr.live]
host = "127.0.0.1"
port = 4001
client_id = 10
account_id = "U123"
readonly = false

[moomoo]
enabled = true
host = "127.0.0.1"
port = 11111
account_id = "876"
market = "US"
""".strip(),
        encoding="utf-8",
    )

    settings = load_settings(config_path)

    profile_name, profile = settings.ibkr.resolve_profile()

    assert profile_name == "live"
    assert profile.port == 4001
    assert settings.data.providers == ["ibkr"]
    assert settings.moomoo.account_id == "876"
