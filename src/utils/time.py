from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


UTC = timezone.utc


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


def local_now(timezone_name: str) -> datetime:
    return utc_now().astimezone(ZoneInfo(timezone_name))


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def to_iso_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat()


def clamp(value: float, low: float, high: float) -> float:
    return min(max(value, low), high)

