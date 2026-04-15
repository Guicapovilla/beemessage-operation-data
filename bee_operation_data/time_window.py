from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class WeekWindow:
    now: datetime
    week_start: datetime
    week_end: datetime
    prev_week_start: datetime


def current_week_window(now: datetime | None = None) -> WeekWindow:
    current = now or datetime.now(timezone.utc)
    days_since_monday = current.weekday()
    week_end = (current - timedelta(days=days_since_monday + 1)).replace(
        hour=23, minute=59, second=59, microsecond=999999
    )
    week_start = (week_end - timedelta(days=6)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    prev_week_start = week_start - timedelta(days=7)
    return WeekWindow(
        now=current,
        week_start=week_start,
        week_end=week_end,
        prev_week_start=prev_week_start,
    )
