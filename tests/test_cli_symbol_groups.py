from __future__ import annotations

from intraday_auto_trading.config import (
    Settings,
)
from intraday_auto_trading.symbol_manager import (
    SelectedSymbolGroup,
    SymbolGroupRegistry,
    SymbolGroupSettings,
    prompt_for_symbol_group,
    resolve_symbols_for_run,
)


def build_settings() -> Settings:
    from intraday_auto_trading.config import (
        DataSettings,
        IBKRProfileSettings,
        IBKRSettings,
        MoomooSettings,
        ProjectSettings,
        SelectionSettings,
        StrategySettings,
        YfinanceSettings,
    )

    symbol_groups = SymbolGroupRegistry(
        groups={
            "core": SymbolGroupSettings(
                name="core",
                symbols=["SPY", "QQQ"],
                single_buy_amount=1000,
            ),
            "tech": SymbolGroupSettings(
                name="tech",
                symbols=["NVDA", "AMD"],
                single_buy_amount=1500,
            ),
        },
        default_group="core",
    )

    return Settings(
        project=ProjectSettings(
            name="intraday-auto-trading",
            timezone="America/New_York",
            paper_trading=True,
            currency="USD",
        ),
        symbol_groups=symbol_groups,
        symbols=symbol_groups.resolve().symbols,
        strategy=StrategySettings(
            ema_fast_span=5,
            ema10_span=10,
            ema_slow_span=20,
            dev20_window=10,
            s_dev20_window=10,
            valley_window=3,
            opening_review_cutoff="10:00",
        ),
        selection=SelectionSettings(
            weak_tail_weight=3.0,
            range_track_weight=2.0,
            early_buy_weight=1.0,
            unbought_bonus=2.0,
            recent_fill_penalty_step=0.5,
        ),
        data=DataSettings(),
        ibkr=IBKRSettings(
            default_profile="paper",
            paper=IBKRProfileSettings(host="127.0.0.1", port=4002, client_id=9),
            live=IBKRProfileSettings(host="127.0.0.1", port=4001, client_id=9),
        ),
        moomoo=MoomooSettings(enabled=True, host="127.0.0.1", port=11111),
        yfinance=YfinanceSettings(),
    )


def test_prompt_for_symbol_group_uses_default_on_enter(capsys) -> None:
    settings = build_settings()

    selected_group = prompt_for_symbol_group(settings.symbol_groups, input_fn=lambda _: "")

    captured = capsys.readouterr()
    assert "Available symbol groups:" in captured.out
    assert selected_group.name == "core"
    assert selected_group.symbols == ["SPY", "QQQ"]
    assert selected_group.single_buy_amount == 1000


def test_prompt_for_symbol_group_retries_invalid_input(capsys) -> None:
    settings = build_settings()
    answers = iter(["bad-group", "tech"])

    selected_group = prompt_for_symbol_group(settings.symbol_groups, input_fn=lambda _: next(answers))

    captured = capsys.readouterr()
    assert "Unknown symbol group: bad-group" in captured.out
    assert selected_group.name == "tech"


def test_resolve_symbols_for_run_limits_to_selected_group() -> None:
    selected_group = SelectedSymbolGroup(
        name="core",
        symbols=["SPY", "QQQ"],
        single_buy_amount=1000,
    )

    assert resolve_symbols_for_run(selected_group, None) == ["SPY", "QQQ"]
    assert resolve_symbols_for_run(selected_group, ["qqq"]) == ["QQQ"]


def test_resolve_symbols_for_run_rejects_symbols_outside_group() -> None:
    selected_group = SelectedSymbolGroup(
        name="core",
        symbols=["SPY", "QQQ"],
        single_buy_amount=1000,
    )

    try:
        resolve_symbols_for_run(selected_group, ["AAPL"])
    except SystemExit as exc:
        assert "outside selected group 'core'" in str(exc)
    else:
        raise AssertionError("Expected SystemExit for symbols outside selected group")
