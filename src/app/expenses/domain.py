from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from app.clock import Clock

MIN_CATEGORY_NAME_LENGTH = 1
MAX_CATEGORY_NAME_LENGTH = 50
MAX_EXPENSE_NOTE_LENGTH = 500


class Currency(StrEnum):
    """Supported currencies for expenses."""

    usd = "usd"
    ves = "ves"


@dataclass(frozen=True, slots=True)
class ExpenseCategory:
    """Domain entity representing an expense category."""

    id: UUID
    name: str
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class Expense:
    """Domain entity representing an expense with its historical exchange rates."""

    id: UUID
    category_id: UUID
    amount: Decimal
    currency: Currency
    spent_on: date
    note: str | None
    bcv_rate_value: Decimal
    bcv_rate_date: date
    usdt_rate_value: Decimal
    usdt_rate_date: date
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ExpenseWithCategory:
    """Expense paired with its category name for responses."""

    expense: Expense
    category_name: str


@dataclass(frozen=True, slots=True)
class ExpenseSummaryItem:
    """Aggregated expense total in USD for one category."""

    category_id: UUID
    category_name: str
    count: int
    total_usd: Decimal


@dataclass(frozen=True, slots=True)
class ExpenseInput:
    """Input payload for creating or updating an expense."""

    id: UUID
    category_id: UUID
    amount: Decimal
    currency: Currency
    spent_on: date
    note: str | None = None


@dataclass(frozen=True, slots=True)
class UpsertCategoryResult:
    """Result of upserting an expense category, indicating whether it was created."""

    category: ExpenseCategory
    created: bool


@dataclass(frozen=True, slots=True)
class UpsertExpenseResult:
    """Result of upserting an expense, indicating whether it was created."""

    item: ExpenseWithCategory
    created: bool


@dataclass(frozen=True, slots=True)
class RateSnapshot:
    """Minimal exchange rate snapshot stored on an expense."""

    value: Decimal
    value_date: date


class RateLookup(Protocol):
    """Port for looking up BCV and USDT exchange rates in force on a given date."""

    async def bcv_in_force(self, on: date) -> RateSnapshot | None: ...

    async def usdt_in_force(self, on: date) -> RateSnapshot | None: ...


class ExpenseCategoryRepository(Protocol):
    """Persistence port for expense categories."""

    async def get_by_id(self, category_id: UUID) -> ExpenseCategory | None: ...

    async def get_by_lower_name(self, name: str) -> ExpenseCategory | None: ...

    async def list_active(self) -> list[ExpenseCategory]: ...

    async def list_all(self) -> list[ExpenseCategory]: ...

    async def insert_if_absent(
        self,
        category: ExpenseCategory,
    ) -> tuple[ExpenseCategory, bool]: ...

    async def update(self, category: ExpenseCategory) -> ExpenseCategory: ...

    async def list_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[ExpenseCategory]: ...


class ExpenseRepository(Protocol):
    """Persistence port for expenses."""

    async def get_by_id(self, expense_id: UUID) -> Expense | None: ...

    async def insert_if_absent(self, expense: Expense) -> tuple[Expense, bool]: ...

    async def update(self, expense: Expense) -> Expense: ...

    async def list_in_range(
        self,
        from_date: date,
        to_date: date,
        category_id: UUID | None = None,
    ) -> list[ExpenseWithCategory]: ...

    async def list_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[Expense]: ...


@dataclass(frozen=True, slots=True)
class ExpenseCollaborators:
    """Grouped collaborators injected into ExpenseService."""

    categories: ExpenseCategoryRepository
    expenses: ExpenseRepository
    rates: RateLookup
    clock: Clock
