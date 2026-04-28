from __future__ import annotations

import sys

from futures_bot.config_loader import load_futures_config


def main() -> int:
    config = load_futures_config()
    settings = config.settings
    symbols = config.symbols.get("symbols", {})

    app_config = settings.get("app", {})
    safety_config = settings.get("safety", {})

    print(f"futures_settings.yaml path: {config.settings_path.resolve()}")
    print(f"futures_symbols.yaml path: {config.symbols_path.resolve()}")
    print(f"app.mode: {app_config.get('mode')}")
    print(f"configured_symbols: {', '.join(symbols.keys()) or '-'}")
    print(f"safety.allow_live_trading: {safety_config.get('allow_live_trading')}")
    print(f"safety.live_execute_enabled: {safety_config.get('live_execute_enabled')}")
    print("execution_path: paper_skeleton_only; real futures order API is not implemented")
    return 0


if __name__ == "__main__":
    sys.exit(main())
