from __future__ import annotations

from pathlib import Path

from intraday_auto_trading.config import load_settings


def main() -> None:
    config_path = Path("config/settings.toml")
    if not config_path.exists():
        print("未找到 config/settings.toml，请先从 config/settings.example.toml 复制一份。")
        return

    settings = load_settings(config_path)
    print(f"项目: {settings.project.name}")
    print(f"时区: {settings.project.timezone}")
    print(f"标的池: {', '.join(settings.symbols)}")
    print("项目骨架已就绪，下一步请接入真实 MarketDataGateway 和 BrokerGateway。")

