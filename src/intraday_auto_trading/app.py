from __future__ import annotations

from intraday_auto_trading.config import Settings
from intraday_auto_trading.models import AccountSymbolState, SelectionResult, TrendSignal
from intraday_auto_trading.services.executor import ExecutionPlanner
from intraday_auto_trading.services.selector import SymbolSelector


class TradingWorkflow:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.selector = SymbolSelector(settings.selection)
        self.execution_planner = ExecutionPlanner()

    def choose_symbol(
        self,
        signals: list[TrendSignal],
        account_states: dict[str, AccountSymbolState],
    ) -> SelectionResult:
        return self.selector.select(signals, account_states)

