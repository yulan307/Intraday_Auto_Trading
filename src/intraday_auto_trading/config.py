from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import tomllib

from intraday_auto_trading.symbol_manager import SymbolGroupRegistry, load_symbol_groups


@dataclass(slots=True)
class StrategySettings:
    ema_fast_span: int
    ema10_span: int
    ema_slow_span: int
    dev20_window: int
    s_dev20_window: int
    valley_window: int
    opening_review_cutoff: str


@dataclass(slots=True)
class SelectionSettings:
    weak_tail_weight: float
    range_track_weight: float
    early_buy_weight: float
    unbought_bonus: float
    recent_fill_penalty_step: float


@dataclass(slots=True)
class ProjectSettings:
    name: str
    timezone: str
    paper_trading: bool
    currency: str


@dataclass(slots=True)
class DataSettings:
    market_data_db: str = "data/market_data.sqlite"
    providers: list[str] | None = None
    data_types: list[str] | None = None
    enable_direct_15m: bool = False
    enable_derived_15m: bool = False

    def __post_init__(self) -> None:
        self.providers = [provider.lower() for provider in (self.providers or ["ibkr"])]
        self.data_types = [data_type.lower() for data_type in (self.data_types or ["bars"])]


@dataclass(slots=True)
class IBKRProfileSettings:
    host: str
    port: int
    client_id: int
    account_id: str = ""
    readonly: bool = True
    account_client_id: int = 10
    broker_client_id: int = 11


@dataclass(slots=True)
class IBKRSettings:
    default_profile: str
    paper: IBKRProfileSettings
    live: IBKRProfileSettings

    def resolve_profile(self, override: str | None = None) -> tuple[str, IBKRProfileSettings]:
        profile_name = (override or self.default_profile).lower()
        if profile_name not in {"paper", "live"}:
            raise ValueError(f"Unsupported IBKR profile: {profile_name}")
        return profile_name, getattr(self, profile_name)


@dataclass(slots=True)
class MoomooSettings:
    enabled: bool
    host: str
    port: int
    account_id: str = ""
    market: str = "US"


@dataclass(slots=True)
class YfinanceSettings:
    enabled: bool = True
    request_timeout_seconds: int = 30


@dataclass(slots=True)
class Settings:
    project: ProjectSettings
    symbol_groups: SymbolGroupRegistry
    symbols: list[str]
    strategy: StrategySettings
    selection: SelectionSettings
    data: DataSettings
    ibkr: IBKRSettings
    moomoo: MoomooSettings
    yfinance: YfinanceSettings = field(default_factory=YfinanceSettings)


def load_settings(path: str | Path, symbol_groups_path: str | Path | None = None) -> Settings:
    config_path = Path(path)
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)

    resolved_symbol_groups_path = (
        Path(symbol_groups_path)
        if symbol_groups_path is not None
        else config_path.with_name("symbol_group.toml")
    )
    symbol_groups = load_symbol_groups(resolved_symbol_groups_path, raw)
    return Settings(
        project=ProjectSettings(**raw["project"]),
        symbol_groups=symbol_groups,
        symbols=symbol_groups.resolve().symbols,
        strategy=StrategySettings(**raw["strategy"]),
        selection=SelectionSettings(**raw["selection"]),
        data=DataSettings(**raw.get("data", {})),
        ibkr=_parse_ibkr_settings(raw.get("ibkr", {})),
        moomoo=MoomooSettings(**raw.get("moomoo", _default_moomoo_dict())),
        yfinance=YfinanceSettings(**raw.get("yfinance", {})),
    )


def _parse_ibkr_settings(raw: dict) -> IBKRSettings:
    if "paper" in raw or "live" in raw:
        default_profile = str(raw.get("default_profile", "paper")).lower()
        paper_raw = raw.get("paper", _default_ibkr_profile_dict("paper"))
        live_raw = raw.get("live", _default_ibkr_profile_dict("live"))
    else:
        default_profile = str(raw.get("account_mode", "paper")).lower()
        flat_profile = {
            "host": raw.get("host", "127.0.0.1"),
            "port": raw.get("port", 4002 if default_profile == "paper" else 4001),
            "client_id": raw.get("client_id", 9),
            "account_id": raw.get("account_id", ""),
            "readonly": raw.get("readonly", True),
        }
        paper_raw = flat_profile if default_profile == "paper" else _default_ibkr_profile_dict("paper")
        live_raw = flat_profile if default_profile == "live" else _default_ibkr_profile_dict("live")

    return IBKRSettings(
        default_profile=default_profile,
        paper=IBKRProfileSettings(**paper_raw),
        live=IBKRProfileSettings(**live_raw),
    )


def _default_ibkr_profile_dict(profile_name: str) -> dict[str, str | int | bool]:
    return {
        "host": "127.0.0.1",
        "port": 4002 if profile_name == "paper" else 4001,
        "client_id": 9,
        "account_id": "",
        "readonly": True,
    }


def _default_moomoo_dict() -> dict[str, str | int | bool]:
    return {
        "enabled": True,
        "host": "127.0.0.1",
        "port": 11111,
        "account_id": "",
        "market": "US",
    }
