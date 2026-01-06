"""
Time-based utilities for cache invalidation and logging.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def utc_now() -> datetime:
    """Return current UTC datetime."""
    return datetime.now(timezone.utc)


def is_file_stale(path: Path, *, max_age_minutes: int) -> bool:
    """
    Determine if a file is older than the supplied minutes.
    Non-existent files are considered stale.
    """
    if max_age_minutes <= 0:
        return True

    if not path.exists():
        return True

    mtime = path.stat().st_mtime
    age_seconds = utc_now().timestamp() - mtime
    return age_seconds > max_age_minutes * 60


def convert_hour_to_ampm(hour: int) -> str:
    if hour == 0:
        return "midnight"
    if hour == 12:
        return "noon"
    if hour < 12:
        return f"{hour}am"
    return f"{hour - 12}pm"


def get_local_now(timezone_name: str) -> datetime:
    try:
        tz = ZoneInfo(timezone_name)
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        tz = timezone.utc
    return datetime.now(tz)
