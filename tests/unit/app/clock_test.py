from datetime import UTC, date, datetime

from app.clock import Clock, SystemClock, caracas_date


def test_system_clock_returns_utc_datetime() -> None:
    clock: Clock = SystemClock()

    now = clock.now()

    assert isinstance(now, datetime)
    assert now.tzinfo is UTC


def test_caracas_date_converts_utc_timestamp_to_caracas_calendar_date() -> None:
    ts = datetime(2026, 11, 22, 2, 30, tzinfo=UTC)

    assert caracas_date(ts) == date(2026, 11, 21)
