import calendar
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from app.clock import Clock
from app.income.errors import InvalidIncomeDays

MIN_RULE_NAME_LENGTH = 1
MAX_RULE_NAME_LENGTH = 100
MIN_DAY_OF_MONTH = 1
MAX_DAY_OF_MONTH = 31
MONTHS_PER_YEAR = 12


class Currency(StrEnum):
    """Supported currencies for recurring income."""

    usd = "usd"
    ves = "ves"


class OccurrenceStatus(StrEnum):
    """Lifecycle status of an income occurrence."""

    expected = "expected"
    received = "received"


@dataclass(frozen=True, slots=True)
class IncomeRule:
    """Domain entity representing a recurring income rule."""

    id: UUID
    name: str
    amount: Decimal
    currency: Currency
    days: tuple[int, ...]
    start_date: date
    end_date: date | None
    ended_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class IncomeOccurrence:
    """Domain entity representing one concrete expected or received occurrence."""

    id: UUID
    rule_id: UUID
    expected_date: date
    expected_amount: Decimal
    currency: Currency
    status: OccurrenceStatus
    received_date: date | None
    received_amount: Decimal | None
    rate_value: Decimal | None
    rate_date: date | None
    received_at: datetime | None
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class OccurrenceWithRule:
    """Occurrence paired with its parent rule name for query responses."""

    occurrence: IncomeOccurrence
    rule_name: str


@dataclass(frozen=True, slots=True)
class IncomeRuleInput:
    """Input payload for creating or updating an income rule."""

    id: UUID
    name: str
    amount: Decimal
    currency: Currency
    days: Sequence[int]
    start_date: date
    end_date: date | None = None


@dataclass(frozen=True, slots=True)
class UpsertRuleResult:
    """Result of upserting an income rule, indicating whether it was created."""

    rule: IncomeRule
    created: bool


@dataclass(frozen=True, slots=True)
class RateSnapshot:
    """Minimal BCV exchange rate snapshot needed when receiving an occurrence."""

    value: Decimal
    value_date: date


class BcvRateLookup(Protocol):
    """Port for looking up the BCV exchange rate in force on a given date."""

    async def bcv_in_force(self, on: date) -> RateSnapshot | None: ...


class IncomeRuleRepository(Protocol):
    """Persistence port for recurring income rules."""

    async def get_by_id(self, rule_id: UUID) -> IncomeRule | None: ...

    async def list_all(self) -> list[IncomeRule]: ...

    async def list_active(self, today: date) -> list[IncomeRule]: ...

    async def insert_if_absent(self, rule: IncomeRule) -> tuple[IncomeRule, bool]: ...

    async def update(self, rule: IncomeRule) -> IncomeRule: ...


class IncomeOccurrenceRepository(Protocol):
    """Persistence port for recurring income occurrences."""

    async def get_by_id(self, occurrence_id: UUID) -> IncomeOccurrence | None: ...

    async def list_dates_for_rule(self, rule_id: UUID) -> set[date]: ...

    async def add_many_ignore_conflicts(
        self,
        occurrences: Sequence[IncomeOccurrence],
    ) -> int: ...

    async def delete_future_expected(self, rule_id: UUID, from_date: date) -> None: ...

    async def update(self, occurrence: IncomeOccurrence) -> IncomeOccurrence: ...

    async def list_in_range(
        self,
        from_date: date,
        to_date: date,
        status: OccurrenceStatus | None = None,
    ) -> list[OccurrenceWithRule]: ...


@dataclass(frozen=True, slots=True)
class IncomeCollaborators:
    """Grouped collaborators injected into IncomeService."""

    rules: IncomeRuleRepository
    occurrences: IncomeOccurrenceRepository
    rates: BcvRateLookup
    clock: Clock


def normalize_days(days: Sequence[int]) -> tuple[int, ...]:
    """Validate, deduplicate, and sort monthly recurrence days within 1..31."""
    if not days or any(day < MIN_DAY_OF_MONTH or day > MAX_DAY_OF_MONTH for day in days):
        raise InvalidIncomeDays("Income days must be non-empty and within 1 to 31.")
    return tuple(sorted(set(days)))


def add_months(base: date, months: int) -> date:
    """Add calendar months to a date, clamping the day to the target month's length."""
    total_months = base.year * MONTHS_PER_YEAR + (base.month - 1) + months
    target_year, zero_based_month = divmod(total_months, MONTHS_PER_YEAR)
    target_month = zero_based_month + 1
    last_day = calendar.monthrange(target_year, target_month)[1]
    return date(target_year, target_month, min(base.day, last_day))


def occurrence_dates(rule: IncomeRule, from_date: date, to_date: date) -> list[date]:
    """Return sorted occurrence dates for a rule within [from_date, to_date] and rule bounds."""
    window_start = max(from_date, rule.start_date)
    window_end = min(to_date, rule.end_date) if rule.end_date is not None else to_date
    if window_start > window_end:
        return []

    dates: set[date] = set()
    start_month_index = window_start.year * MONTHS_PER_YEAR + (window_start.month - 1)
    end_month_index = window_end.year * MONTHS_PER_YEAR + (window_end.month - 1)

    for month_index in range(start_month_index, end_month_index + 1):
        year, zero_based_month = divmod(month_index, MONTHS_PER_YEAR)
        month = zero_based_month + 1
        month_length = calendar.monthrange(year, month)[1]
        for day in rule.days:
            candidate = date(year, month, min(day, month_length))
            if window_start <= candidate <= window_end:
                dates.add(candidate)

    return sorted(dates)
