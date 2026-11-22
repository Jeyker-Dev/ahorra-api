from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

import app.income.domain as domain_module
from app.income.domain import (
    Currency,
    IncomeRule,
    add_months,
    normalize_days,
    occurrence_dates,
)
from app.income.errors import InvalidIncomeDays


def _make_rule(
    *,
    days: tuple[int, ...] = (15, 30),
    start_date: date = date(2026, 1, 1),
    end_date: date | None = None,
) -> IncomeRule:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    return IncomeRule(
        id=uuid4(),
        name="Salary",
        amount=Decimal("1000.00"),
        currency=Currency.usd,
        days=days,
        start_date=start_date,
        end_date=end_date,
        ended_at=None,
        created_at=now,
        updated_at=now,
    )


def test_income_domain_does_not_import_framework_libraries() -> None:
    assert domain_module.__file__ is not None
    source_text = Path(domain_module.__file__).read_text(encoding="utf-8")
    for forbidden in ("fastapi", "pydantic", "sqlalchemy"):
        assert forbidden not in source_text


def test_income_rule_is_frozen() -> None:
    rule = _make_rule()
    with pytest.raises(FrozenInstanceError):
        rule.name = "Other"  # type: ignore[misc]


def test_occurrence_dates_clamps_day_31_in_30_day_months() -> None:
    rule = _make_rule(days=(31,), start_date=date(2026, 3, 1))
    dates = occurrence_dates(rule, date(2026, 3, 1), date(2026, 6, 30))
    assert dates == [
        date(2026, 3, 31),
        date(2026, 4, 30),
        date(2026, 5, 31),
        date(2026, 6, 30),
    ]


@pytest.mark.parametrize(
    ("year", "expected_feb_day"),
    [
        (2025, 28),
        (2024, 29),
        (2100, 28),
    ],
)
def test_occurrence_dates_handles_february_leap_and_non_leap_and_collapsing_days(
    year: int,
    expected_feb_day: int,
) -> None:
    rule = _make_rule(days=(29, 30, 31), start_date=date(year, 1, 1))
    dates = occurrence_dates(rule, date(year, 2, 1), date(year, 2, expected_feb_day))
    assert dates == [date(year, 2, expected_feb_day)]


def test_occurrence_dates_respects_inclusive_start_and_end_date_bounds() -> None:
    rule = _make_rule(
        days=(1, 15, 30),
        start_date=date(2026, 5, 15),
        end_date=date(2026, 6, 15),
    )
    dates = occurrence_dates(rule, date(2026, 5, 1), date(2026, 7, 31))
    assert dates == [
        date(2026, 5, 15),
        date(2026, 5, 30),
        date(2026, 6, 1),
        date(2026, 6, 15),
    ]


def test_occurrence_dates_returns_empty_when_window_or_period_is_disjoint() -> None:
    rule = _make_rule(days=(15,), start_date=date(2026, 5, 1), end_date=date(2026, 5, 31))
    assert occurrence_dates(rule, date(2026, 6, 1), date(2026, 5, 1)) == []
    assert occurrence_dates(rule, date(2026, 6, 1), date(2026, 6, 30)) == []
    assert occurrence_dates(rule, date(2026, 4, 1), date(2026, 4, 30)) == []


def test_occurrence_dates_crosses_year_boundary_sorted() -> None:
    rule = _make_rule(days=(15, 31), start_date=date(2026, 11, 1))
    dates = occurrence_dates(rule, date(2026, 11, 20), date(2027, 2, 28))
    assert dates == [
        date(2026, 11, 30),
        date(2026, 12, 15),
        date(2026, 12, 31),
        date(2027, 1, 15),
        date(2027, 1, 31),
        date(2027, 2, 15),
        date(2027, 2, 28),
    ]


def test_add_months_crosses_year_and_clamps_day_to_month_length() -> None:
    assert add_months(date(2026, 11, 30), 3) == date(2027, 2, 28)
    assert add_months(date(2023, 11, 30), 3) == date(2024, 2, 29)
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2026, 10, 15), 3) == date(2027, 1, 15)


def test_normalize_days_sorts_deduplicates_and_rejects_invalid_values() -> None:
    assert normalize_days([30, 15, 30, 1]) == (1, 15, 30)

    with pytest.raises(InvalidIncomeDays):
        normalize_days([])
    with pytest.raises(InvalidIncomeDays):
        normalize_days([0, 15])
    with pytest.raises(InvalidIncomeDays):
        normalize_days([15, 32])
