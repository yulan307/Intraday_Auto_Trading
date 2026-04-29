from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DataFetchPolicy:
    """Data source priority rules for TrendInputLoader.

    db_source_priority: ranking for choosing among multiple DB rows for the same
        (symbol, bar_size, ts) — highest priority wins.
    live_source_order: gateways tried in order when eval_time is today (ET).
        All sources exhausted with no result → RuntimeError.
    history_source_order: gateways tried in order when eval_time is a past date (ET).
        All sources exhausted with no result → RuntimeError.
    ibkr_options_enabled: whether to call the IBKR gateway for option quotes.
        Defaults to False because no option data permission is currently provisioned.
    """

    db_source_priority: list[str] = field(default_factory=lambda: ["ibkr"])
    live_source_order: list[str] = field(default_factory=lambda: ["ibkr"])
    history_source_order: list[str] = field(default_factory=lambda: ["ibkr"])
    ibkr_options_enabled: bool = False


def default_policy() -> DataFetchPolicy:
    return DataFetchPolicy()
