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
ema_fast_span = 5
ema_slow_span = 20
recent_high_lookback = 3
force_buy_minutes_before_close = 15
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


def test_load_settings_supports_symbol_groups(tmp_path: Path) -> None:
    config_path = tmp_path / "settings.toml"
    config_path.write_text(
        """
[project]
name = "intraday-auto-trading"
timezone = "America/New_York"
paper_trading = true
currency = "USD"

[symbol_groups]
default_group = "tech"

[symbol_groups.groups.core]
symbols = ["SPY", "QQQ"]
single_buy_amount = 1000

[symbol_groups.groups.tech]
symbols = ["NVDA", "AMD"]
single_buy_amount = 1500

[strategy]
ema_fast_span = 5
ema_slow_span = 20
recent_high_lookback = 3
force_buy_minutes_before_close = 15
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
default_profile = "paper"

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
    selected_group = settings.symbol_groups.resolve()

    assert settings.symbol_groups.default_group == "tech"
    assert settings.symbol_groups.list_names() == ["core", "tech"]
    assert selected_group.name == "tech"
    assert selected_group.symbols == ["NVDA", "AMD"]
    assert selected_group.single_buy_amount == 1500
    assert settings.symbols == ["NVDA", "AMD"]


def test_load_settings_prefers_external_symbol_group_file(tmp_path: Path) -> None:
    config_path = tmp_path / "settings.toml"
    symbol_group_path = tmp_path / "symbol_group.toml"
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
ema_fast_span = 5
ema_slow_span = 20
recent_high_lookback = 3
force_buy_minutes_before_close = 15
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
default_profile = "paper"

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
    symbol_group_path.write_text(
        """
[symbol_groups]
default_group = "watch"

[symbol_groups.groups.watch]
symbols = ["QQQ", "IWM"]
single_buy_amount = 2000
""".strip(),
        encoding="utf-8",
    )

    settings = load_settings(config_path, symbol_groups_path=symbol_group_path)
    selected_group = settings.symbol_groups.resolve()

    assert selected_group.name == "watch"
    assert selected_group.symbols == ["QQQ", "IWM"]
    assert selected_group.single_buy_amount == 2000
    assert settings.symbols == ["QQQ", "IWM"]


def test_load_settings_falls_back_to_legacy_symbols_pool(tmp_path: Path) -> None:
    config_path = tmp_path / "settings.toml"
    config_path.write_text(
        """
[project]
name = "intraday-auto-trading"
timezone = "America/New_York"
paper_trading = true
currency = "USD"

[symbols]
pool = ["SPY", "QQQ"]

[strategy]
ema_fast_span = 5
ema_slow_span = 20
recent_high_lookback = 3
force_buy_minutes_before_close = 15
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
default_profile = "paper"

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
    selected_group = settings.symbol_groups.resolve()

    assert settings.symbol_groups.default_group == "default"
    assert settings.symbol_groups.list_names() == ["default"]
    assert selected_group.name == "default"
    assert selected_group.symbols == ["SPY", "QQQ"]
    assert selected_group.single_buy_amount == 0.0
