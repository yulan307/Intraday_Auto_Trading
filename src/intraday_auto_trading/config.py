from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib


@dataclass(slots=True)
class StrategySettings:
    tracking_confirmation_bars: int
    tracking_limit_price_factor: float
    force_buy_last_minutes: int
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
class Settings:
    project: ProjectSettings
    symbols: list[str]
    strategy: StrategySettings
    selection: SelectionSettings


def load_settings(path: str | Path) -> Settings:
    with Path(path).open("rb") as handle:
        raw = tomllib.load(handle)

    return Settings(
        project=ProjectSettings(**raw["project"]),
        symbols=list(raw["symbols"]["pool"]),
        strategy=StrategySettings(**raw["strategy"]),
        selection=SelectionSettings(**raw["selection"]),
    )

