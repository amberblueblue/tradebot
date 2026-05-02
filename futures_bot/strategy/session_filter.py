from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo


ALLOWED_MARKET_SESSION_FILTERS = {"none", "us_regular"}
US_EASTERN = ZoneInfo("America/New_York")
US_REGULAR_OPEN = time(9, 30)
US_REGULAR_CLOSE = time(16, 0)


def filter_klines_by_session(klines: list[Any], session_filter: str) -> list[Any]:
    if session_filter == "none":
        return list(klines)
    if session_filter != "us_regular":
        raise ValueError(
            f"session_filter must be one of {sorted(ALLOWED_MARKET_SESSION_FILTERS)}"
        )
    return [kline for kline in klines if _is_us_regular_kline(kline)]


def kline_open_time_utc(open_time_ms: int | float | str) -> str:
    return _kline_datetime_utc(open_time_ms).isoformat()


def kline_open_time_local(open_time_ms: int | float | str) -> str:
    return _kline_datetime_utc(open_time_ms).astimezone(US_EASTERN).isoformat()


def _is_us_regular_kline(kline: Any) -> bool:
    if not isinstance(kline, (list, tuple)) or not kline:
        return False
    try:
        opened_at = _kline_datetime_utc(kline[0]).astimezone(US_EASTERN)
    except (TypeError, ValueError, OSError):
        return False
    if opened_at.weekday() >= 5:
        return False
    return US_REGULAR_OPEN <= opened_at.time() < US_REGULAR_CLOSE


def _kline_datetime_utc(open_time_ms: int | float | str) -> datetime:
    return datetime.fromtimestamp(float(open_time_ms) / 1000, tz=timezone.utc)
