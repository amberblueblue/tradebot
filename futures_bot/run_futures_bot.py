from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from futures_bot.config_loader import load_futures_config  # noqa: E402


def main() -> int:
    config = load_futures_config()
    enabled_symbols = config.enabled_symbols

    print(f"futures settings path: {config.settings_path.resolve()}")
    print(f"futures symbols path: {config.symbols_path.resolve()}")
    print(f"app.mode: {config.app.mode}")
    print(f"enabled futures symbols: {', '.join(enabled_symbols) or '-'}")
    print(f"base_url: {config.futures.base_url}")
    print("stage: public-data-only / no trading")

    if not enabled_symbols:
        print("[futures_idle] no_enabled_symbols")
        return 0

    print("[futures_idle] trading_disabled")
    return 0


if __name__ == "__main__":
    sys.exit(main())
