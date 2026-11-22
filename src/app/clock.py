from datetime import UTC, date, datetime
from typing import Protocol
from zoneinfo import ZoneInfo

CARACAS_TZ = ZoneInfo("America/Caracas")


def caracas_date(ts: datetime) -> date:
    """Convert an aware timestamp to its calendar date in America/Caracas."""
    return ts.astimezone(CARACAS_TZ).date()


class Clock(Protocol):
    """Port for obtaining the current UTC timestamp."""

    def now(self) -> datetime: ...


class SystemClock:
    """Production clock returning the current UTC datetime."""

    def now(self) -> datetime:
        return datetime.now(UTC)
