from datetime import datetime, timezone

PRIORITY_MAP = {
    "urgent": "urgente",
    "high": "alta",
    "medium": "media",
    "low": "baixa",
    "none": "sem",
    None: "sem",
    "": "sem",
}
PRIORITY_KEYS = ["urgente", "alta", "media", "baixa", "sem"]


def parse_dt(value: str | None):
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def days_between(a, b):
    if not a or not b:
        return None
    return max(0, (b - a).total_seconds() / 86400)


def safe_round(v, d: int = 1):
    if v is None:
        return None
    return round(v, d)
