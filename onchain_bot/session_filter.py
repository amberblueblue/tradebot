from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo


ALLOWED_EXECUTION_SESSION_FILTERS = ("none", "us_regular")
US_EASTERN = ZoneInfo("America/New_York")
US_REGULAR_START = time(9, 30)
US_REGULAR_END = time(16, 0)


def get_execution_session_status(
    execution_session_filter: str | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    normalized_filter = (execution_session_filter or "us_regular").strip().lower()
    if normalized_filter not in ALLOWED_EXECUTION_SESSION_FILTERS:
        normalized_filter = "us_regular"

    current_utc = now or datetime.now(timezone.utc)
    if current_utc.tzinfo is None:
        current_utc = current_utc.replace(tzinfo=timezone.utc)
    current_ny = current_utc.astimezone(US_EASTERN)

    if normalized_filter == "none":
        return {
            "execution_session_filter": normalized_filter,
            "session_allowed": True,
            "session_name": "none",
            "session_time_now": current_ny.isoformat(),
        }

    is_weekday = current_ny.weekday() < 5
    current_time = current_ny.time()
    allowed = is_weekday and US_REGULAR_START <= current_time < US_REGULAR_END
    return {
        "execution_session_filter": normalized_filter,
        "session_allowed": allowed,
        "session_name": "us_regular",
        "session_time_now": current_ny.isoformat(),
    }
