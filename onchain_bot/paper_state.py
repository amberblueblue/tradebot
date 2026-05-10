from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PAPER_STATE_PATH = PROJECT_ROOT / "runtime" / "onchain_paper_state.json"


def _empty_state() -> dict[str, Any]:
    return {
        "positions": {},
        "closed_trades": [],
        "daily_stats": {
            "date": _today_date(),
            "opens_count": 0,
            "closes_count": 0,
        },
        "last_trade_times": {},
    }


def _today_date(now: datetime | None = None) -> str:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).date().isoformat()


def _normalize_daily_stats(value: Any, *, now: datetime | None = None) -> dict[str, Any]:
    today = _today_date(now)
    if not isinstance(value, dict) or value.get("date") != today:
        return {
            "date": today,
            "opens_count": 0,
            "closes_count": 0,
        }
    opens_count = value.get("opens_count", 0)
    closes_count = value.get("closes_count", 0)
    return {
        "date": today,
        "opens_count": opens_count if isinstance(opens_count, int) and opens_count >= 0 else 0,
        "closes_count": closes_count if isinstance(closes_count, int) and closes_count >= 0 else 0,
    }


def normalize_paper_state(state: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    positions = state.get("positions")
    closed_trades = state.get("closed_trades")
    last_trade_times = state.get("last_trade_times")
    return {
        "positions": positions if isinstance(positions, dict) else {},
        "closed_trades": closed_trades if isinstance(closed_trades, list) else [],
        "daily_stats": _normalize_daily_stats(state.get("daily_stats"), now=now),
        "last_trade_times": last_trade_times if isinstance(last_trade_times, dict) else {},
    }


def load_paper_state(state_path: Path | None = None) -> dict[str, Any]:
    path = state_path or DEFAULT_PAPER_STATE_PATH
    if not path.exists():
        return _empty_state()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_state()
    if not isinstance(payload, dict):
        return _empty_state()

    return normalize_paper_state(payload)


def save_paper_state(
    state: dict[str, Any],
    state_path: Path | None = None,
) -> dict[str, Any]:
    path = state_path or DEFAULT_PAPER_STATE_PATH
    state = normalize_paper_state(state)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(state, indent=2, sort_keys=True, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as exc:
        return {
            "ok": False,
            "error": "onchain_paper_state_write_failed",
            "message": str(exc),
            "path": str(path),
        }
    return {
        "ok": True,
        "path": str(path),
        "positions_count": len(state.get("positions", {})),
        "closed_trades_count": len(state.get("closed_trades", [])),
    }


def get_positions(state_path: Path | None = None) -> list[dict[str, Any]]:
    positions = load_paper_state(state_path).get("positions", {})
    if not isinstance(positions, dict):
        return []
    return [
        position
        for _, position in sorted(positions.items())
        if isinstance(position, dict)
    ]


def get_closed_trades(state_path: Path | None = None) -> list[dict[str, Any]]:
    trades = load_paper_state(state_path).get("closed_trades", [])
    if not isinstance(trades, list):
        return []
    return [trade for trade in trades if isinstance(trade, dict)]
