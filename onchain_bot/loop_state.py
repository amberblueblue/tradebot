from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOOP_STATE_PATH = PROJECT_ROOT / "runtime" / "onchain_loop_state.json"


def _default_loop_state() -> dict[str, Any]:
    return {
        "running": False,
        "pid": None,
        "last_loop_at": None,
        "last_summary": {},
    }


def _pid_is_running(pid: Any) -> bool:
    try:
        normalized_pid = int(pid)
    except (TypeError, ValueError):
        return False
    if normalized_pid <= 0:
        return False
    try:
        os.kill(normalized_pid, 0)
    except OSError:
        return False
    return True


def load_loop_state(path: Path = DEFAULT_LOOP_STATE_PATH) -> dict[str, Any]:
    if not path.exists():
        return _default_loop_state()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    state = _default_loop_state()
    state.update(payload)
    state["running"] = bool(state.get("running")) and _pid_is_running(state.get("pid"))
    return state


def write_loop_state(
    *,
    running: bool,
    pid: int | None = None,
    last_loop_at: str | None = None,
    last_summary: dict[str, Any] | None = None,
    path: Path = DEFAULT_LOOP_STATE_PATH,
) -> dict[str, Any]:
    state = load_loop_state(path)
    state.update(
        {
            "running": bool(running),
            "pid": pid if running else None,
        }
    )
    if last_loop_at is not None:
        state["last_loop_at"] = last_loop_at
    if last_summary is not None:
        state["last_summary"] = last_summary
    if not running and state.get("last_loop_at") is None:
        state["last_loop_at"] = datetime.now(timezone.utc).isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    return state
