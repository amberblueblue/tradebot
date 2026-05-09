from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from onchain_bot.config_loader import onchain_symbols_payload  # noqa: E402


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Show Onchain Bot read-only status.")
    parser.add_argument(
        "--symbols",
        action="store_true",
        help="Show configured onchain symbol mappings.",
    )
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args(sys.argv[1:])
    if not args.symbols:
        print(json.dumps({"error": "missing_mode", "message": "use --symbols"}, indent=2, sort_keys=True))
        return 1

    try:
        payload = onchain_symbols_payload()
    except Exception as exc:
        print(json.dumps({"error": "onchain_config_error", "message": str(exc)}, indent=2, sort_keys=True))
        return 1

    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

