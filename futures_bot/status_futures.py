from __future__ import annotations

import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from futures_bot.config_loader import load_futures_config  # noqa: E402


def build_status_payload() -> dict[str, object]:
    config = load_futures_config()
    enabled_symbols = list(config.enabled_symbols)

    return {
        "mode": config.app.mode,
        "base_url": config.futures.base_url,
        "enabled_symbols": enabled_symbols,
        "symbols_count": len(enabled_symbols),
        "live_allowed": config.safety.allow_live_trading,
        "public_data_only": True,
    }


def main() -> int:
    try:
        payload = build_status_payload()
    except Exception as exc:
        payload = {
            "error": "futures_config_error",
            "message": str(exc),
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 1

    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
