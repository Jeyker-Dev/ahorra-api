from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from app.clock import Clock

SYNC_SAFETY_LAG = timedelta(seconds=30)


class SyncEntity(StrEnum):
    """Entity table names included in incremental sync."""

    income_rules = "income_rules"
    income_occurrences = "income_occurrences"
    expense_categories = "expense_categories"
    expenses = "expenses"
    products = "products"
    product_prices = "product_prices"
    shopping_lists = "shopping_lists"
    shopping_list_items = "shopping_list_items"
    goals = "goals"
    goal_contributions = "goal_contributions"


@dataclass(frozen=True, slots=True)
class SyncIncomeRule:
    """Raw stored income_rules row for sync."""

    id: UUID
    name: str
    amount: Decimal
    currency: str
    days: tuple[int, ...]
    start_date: date
    end_date: date | None
    ended_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SyncIncomeOccurrence:
    """Raw stored income_occurrences row for sync."""

    id: UUID
    rule_id: UUID
    expected_date: date
    expected_amount: Decimal
    currency: str
    status: str
    received_date: date | None
    received_amount: Decimal | None
    rate_value: Decimal | None
    rate_date: date | None
    received_at: datetime | None
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SyncExpenseCategory:
    """Raw stored expense_categories row for sync."""

    id: UUID
    name: str
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SyncExpense:
    """Raw stored expenses row for sync."""

    id: UUID
    category_id: UUID
    amount: Decimal
    currency: str
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
class SyncProduct:
    """Raw stored products row for sync."""

    id: UUID
    name: str
    unit: str
    last_price: Decimal | None
    last_price_currency: str | None
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SyncProductPrice:
    """Raw stored product_prices row for sync."""

    id: UUID
    product_id: UUID
    price: Decimal
    currency: str
    recorded_on: date
    source: str
    list_item_id: UUID | None
    bcv_rate_value: Decimal
    bcv_rate_date: date
    usdt_rate_value: Decimal
    usdt_rate_date: date
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SyncShoppingList:
    """Raw stored shopping_lists row for sync."""

    id: UUID
    name: str
    status: str
    closed_at: datetime | None
    expense_id: UUID | None
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SyncShoppingListItem:
    """Raw stored shopping_list_items row for sync."""

    id: UUID
    list_id: UUID
    product_id: UUID
    quantity: Decimal
    status: str
    bought_price: Decimal | None
    bought_currency: str | None
    bought_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SyncGoal:
    """Raw stored goals row for sync."""

    id: UUID
    name: str
    target_usd: Decimal
    target_date: date | None
    note: str | None
    archived_at: datetime | None
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SyncGoalContribution:
    """Raw stored goal_contributions row for sync."""

    id: UUID
    goal_id: UUID
    kind: str
    amount: Decimal
    currency: str
    contributed_on: date
    note: str | None
    bcv_rate_value: Decimal
    bcv_rate_date: date
    usdt_rate_value: Decimal
    usdt_rate_date: date
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class Tombstone:
    """Hard-deletion tombstone recorded for incremental sync."""

    entity: SyncEntity
    id: UUID
    deleted_at: datetime


@dataclass(frozen=True, slots=True)
class SyncChangesResult:
    """Complete snapshot or incremental delta returned by SyncService."""

    next_cursor: datetime
    income_rules: list[SyncIncomeRule]
    income_occurrences: list[SyncIncomeOccurrence]
    expense_categories: list[SyncExpenseCategory]
    expenses: list[SyncExpense]
    products: list[SyncProduct]
    product_prices: list[SyncProductPrice]
    shopping_lists: list[SyncShoppingList]
    shopping_list_items: list[SyncShoppingListItem]
    goals: list[SyncGoal]
    goal_contributions: list[SyncGoalContribution]
    tombstones: list[Tombstone]


class SyncTombstoneRepository(Protocol):
    """Persistence port for reading sync tombstones."""

    async def list_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[Tombstone]: ...


class IncomeSyncSource(Protocol):
    """Cross-domain port reading changed income entities from IncomeService."""

    async def list_rules_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[SyncIncomeRule]: ...

    async def list_occurrences_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[SyncIncomeOccurrence]: ...


class ExpensesSyncSource(Protocol):
    """Cross-domain port reading changed expense entities from ExpenseService."""

    async def list_categories_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[SyncExpenseCategory]: ...

    async def list_expenses_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[SyncExpense]: ...


class ProductsSyncSource(Protocol):
    """Cross-domain port reading changed product entities from ProductsService."""

    async def list_products_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[SyncProduct]: ...

    async def list_prices_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[SyncProductPrice]: ...


class ShoppingSyncSource(Protocol):
    """Cross-domain port reading changed shopping entities from ShoppingService."""

    async def list_lists_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[SyncShoppingList]: ...

    async def list_items_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[SyncShoppingListItem]: ...


class GoalsSyncSource(Protocol):
    """Cross-domain port reading changed goal entities from GoalsService."""

    async def list_goals_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[SyncGoal]: ...

    async def list_contributions_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[SyncGoalContribution]: ...


@dataclass(frozen=True, slots=True)
class SyncCollaborators:
    """Grouped collaborators injected into SyncService."""

    income: IncomeSyncSource
    expenses: ExpensesSyncSource
    products: ProductsSyncSource
    shopping: ShoppingSyncSource
    goals: GoalsSyncSource
    tombstones: SyncTombstoneRepository
    clock: Clock
