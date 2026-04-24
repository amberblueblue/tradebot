from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


ENTRY_SIGNAL = "entry"
EXECUTED = "executed"
FAILED = "failed"
ATTEMPTED = "attempted"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_signal_record(
    *,
    symbol: str,
    signal_type: str,
    signal_time: str,
    action: str,
    status: str,
    error: str | None = None,
) -> dict[str, Any]:
    timestamp = utc_now()
    record: dict[str, Any] = {
        "symbol": symbol,
        "signal_type": signal_type,
        "signal_time": signal_time,
        "action": action,
        "last_executed_at": timestamp if status == EXECUTED else None,
        "last_attempted_at": timestamp,
        "status": status,
    }
    if error:
        record["error"] = error
    return record


def is_same_signal(
    record: dict[str, Any] | None,
    *,
    symbol: str,
    signal_type: str,
    signal_time: str,
    action: str,
) -> bool:
    if not isinstance(record, dict):
        return False
    return (
        record.get("symbol") == symbol
        and record.get("signal_type") == signal_type
        and record.get("signal_time") == signal_time
        and record.get("action") == action
    )
