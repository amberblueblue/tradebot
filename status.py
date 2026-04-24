from __future__ import annotations

import json
from pathlib import Path

from config.loader import load_execution_runtime, load_project_config


def main() -> None:
    settings = load_project_config()
    execution_config = load_execution_runtime(settings)
    status_path = Path(execution_config.status_file)
    if not status_path.exists():
        print(f"Status file not found: {status_path}")
        return

    payload = json.loads(status_path.read_text(encoding="utf-8"))
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
