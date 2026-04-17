from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib
from typing import Any, Mapping


@dataclass(slots=True)
class SymbolGroupSettings:
    name: str
    symbols: list[str]
    single_buy_amount: float

    def __post_init__(self) -> None:
        self.symbols = [symbol.upper() for symbol in self.symbols]


@dataclass(slots=True)
class SelectedSymbolGroup:
    name: str
    symbols: list[str]
    single_buy_amount: float


@dataclass(slots=True)
class SymbolGroupRegistry:
    groups: dict[str, SymbolGroupSettings]
    default_group: str | None = None

    def __post_init__(self) -> None:
        if not self.groups:
            raise ValueError("At least one symbol group must be configured.")
        if self.default_group is None:
            self.default_group = next(iter(self.groups))
        self.default_group = self._match_group_name(self.default_group)

    def list_names(self) -> list[str]:
        return list(self.groups)

    def resolve(self, name: str | None = None) -> SelectedSymbolGroup:
        matched_name = self._match_group_name(name or self.default_group)
        group = self.groups[matched_name]
        return SelectedSymbolGroup(
            name=group.name,
            symbols=list(group.symbols),
            single_buy_amount=group.single_buy_amount,
        )

    def _match_group_name(self, name: str | None) -> str:
        if not name:
            raise KeyError("No symbol group name provided.")
        for group_name in self.groups:
            if group_name.lower() == name.lower():
                return group_name
        raise KeyError(f"Unknown symbol group: {name}")


def load_symbol_groups(
    path: str | Path | None,
    settings_raw: Mapping[str, Any],
) -> SymbolGroupRegistry:
    if path is not None and Path(path).exists():
        with Path(path).open("rb") as handle:
            return parse_symbol_groups(tomllib.load(handle), settings_raw=settings_raw)
    return parse_symbol_groups(settings_raw, settings_raw=settings_raw)


def parse_symbol_groups(
    raw: Mapping[str, Any],
    *,
    settings_raw: Mapping[str, Any] | None = None,
) -> SymbolGroupRegistry:
    settings_raw = settings_raw or raw
    symbol_groups_raw = raw.get("symbol_groups", raw)
    groups_raw = symbol_groups_raw.get("groups", {})

    groups: dict[str, SymbolGroupSettings] = {}
    for group_name, group_raw in groups_raw.items():
        groups[group_name] = SymbolGroupSettings(
            name=group_name,
            symbols=list(group_raw["symbols"]),
            single_buy_amount=float(group_raw["single_buy_amount"]),
        )

    if groups:
        default_group = symbol_groups_raw.get("default_group")
        return SymbolGroupRegistry(groups=groups, default_group=default_group)

    legacy_symbols = [symbol.upper() for symbol in settings_raw["symbols"]["pool"]]
    legacy_single_buy_amount = float(settings_raw.get("symbols", {}).get("single_buy_amount", 0.0))
    legacy_group_name = str(symbol_groups_raw.get("default_group", "default"))
    return SymbolGroupRegistry(
        groups={
            legacy_group_name: SymbolGroupSettings(
                name=legacy_group_name,
                symbols=legacy_symbols,
                single_buy_amount=legacy_single_buy_amount,
            )
        },
        default_group=legacy_group_name,
    )


def prompt_for_symbol_group(symbol_groups: SymbolGroupRegistry, input_fn=input) -> SelectedSymbolGroup:
    print("Available symbol groups:")
    for group_name in symbol_groups.list_names():
        group = symbol_groups.resolve(group_name)
        suffix = " (default)" if group.name == symbol_groups.default_group else ""
        print(
            f"  - {group.name}{suffix}: "
            f"{', '.join(group.symbols)} | single_buy_amount={group.single_buy_amount:.2f}"
        )

    while True:
        default_group = symbol_groups.default_group
        prompt = f"Select symbol group [{default_group}]: "
        group_name = input_fn(prompt).strip()
        try:
            return symbol_groups.resolve(group_name or None)
        except KeyError:
            print(f"Unknown symbol group: {group_name}. Please try again.")


def resolve_symbols_for_run(selected_group: SelectedSymbolGroup, override_symbols: list[str] | None) -> list[str]:
    if not override_symbols:
        return list(selected_group.symbols)

    selected_symbols = {symbol.upper() for symbol in selected_group.symbols}
    requested_symbols = [symbol.upper() for symbol in override_symbols]
    invalid_symbols = [symbol for symbol in requested_symbols if symbol not in selected_symbols]
    if invalid_symbols:
        invalid_list = ", ".join(invalid_symbols)
        raise SystemExit(
            f"--symbols contains symbols outside selected group '{selected_group.name}': {invalid_list}"
        )
    return requested_symbols
