from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, PlainSerializer

from app.sync.domain import (
    SyncChangesResult,
    SyncEntity,
    SyncExpense,
    SyncExpenseCategory,
    SyncGoal,
    SyncGoalContribution,
    SyncIncomeOccurrence,
    SyncIncomeRule,
    SyncProduct,
    SyncProductPrice,
    SyncShoppingList,
    SyncShoppingListItem,
    Tombstone,
)

DecimalStr = Annotated[
    Decimal,
    PlainSerializer(str, return_type=str, when_used="json"),
]


class SyncChangesQuery(BaseModel):
    """Query parameters for incremental sync pull."""

    since: str | None = None


class SyncIncomeRuleOut(BaseModel):
    """Raw stored income_rules row in sync payload."""

    id: UUID
    name: str
    amount: DecimalStr
    currency: str
    days: list[int]
    start_date: date
    end_date: date | None
    ended_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, row: SyncIncomeRule) -> Self:
        """Build schema from SyncIncomeRule."""
        return cls(
            id=row.id,
            name=row.name,
            amount=row.amount,
            currency=row.currency,
            days=list(row.days),
            start_date=row.start_date,
            end_date=row.end_date,
            ended_at=row.ended_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class SyncIncomeOccurrenceOut(BaseModel):
    """Raw stored income_occurrences row in sync payload."""

    id: UUID
    rule_id: UUID
    expected_date: date
    expected_amount: DecimalStr
    currency: str
    status: str
    received_date: date | None
    received_amount: DecimalStr | None
    rate_value: DecimalStr | None
    rate_date: date | None
    received_at: datetime | None
    updated_at: datetime

    @classmethod
    def from_domain(cls, row: SyncIncomeOccurrence) -> Self:
        """Build schema from SyncIncomeOccurrence."""
        return cls(
            id=row.id,
            rule_id=row.rule_id,
            expected_date=row.expected_date,
            expected_amount=row.expected_amount,
            currency=row.currency,
            status=row.status,
            received_date=row.received_date,
            received_amount=row.received_amount,
            rate_value=row.rate_value,
            rate_date=row.rate_date,
            received_at=row.received_at,
            updated_at=row.updated_at,
        )


class SyncExpenseCategoryOut(BaseModel):
    """Raw stored expense_categories row in sync payload."""

    id: UUID
    name: str
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, row: SyncExpenseCategory) -> Self:
        """Build schema from SyncExpenseCategory."""
        return cls(
            id=row.id,
            name=row.name,
            archived_at=row.archived_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class SyncExpenseOut(BaseModel):
    """Raw stored expenses row in sync payload."""

    id: UUID
    category_id: UUID
    amount: DecimalStr
    currency: str
    spent_on: date
    note: str | None
    bcv_rate_value: DecimalStr
    bcv_rate_date: date
    usdt_rate_value: DecimalStr
    usdt_rate_date: date
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, row: SyncExpense) -> Self:
        """Build schema from SyncExpense."""
        return cls(
            id=row.id,
            category_id=row.category_id,
            amount=row.amount,
            currency=row.currency,
            spent_on=row.spent_on,
            note=row.note,
            bcv_rate_value=row.bcv_rate_value,
            bcv_rate_date=row.bcv_rate_date,
            usdt_rate_value=row.usdt_rate_value,
            usdt_rate_date=row.usdt_rate_date,
            deleted_at=row.deleted_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class SyncProductOut(BaseModel):
    """Raw stored products row in sync payload."""

    id: UUID
    name: str
    unit: str
    last_price: DecimalStr | None
    last_price_currency: str | None
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, row: SyncProduct) -> Self:
        """Build schema from SyncProduct."""
        return cls(
            id=row.id,
            name=row.name,
            unit=row.unit,
            last_price=row.last_price,
            last_price_currency=row.last_price_currency,
            archived_at=row.archived_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class SyncProductPriceOut(BaseModel):
    """Raw stored product_prices row in sync payload."""

    id: UUID
    product_id: UUID
    price: DecimalStr
    currency: str
    recorded_on: date
    source: str
    list_item_id: UUID | None
    bcv_rate_value: DecimalStr
    bcv_rate_date: date
    usdt_rate_value: DecimalStr
    usdt_rate_date: date
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, row: SyncProductPrice) -> Self:
        """Build schema from SyncProductPrice."""
        return cls(
            id=row.id,
            product_id=row.product_id,
            price=row.price,
            currency=row.currency,
            recorded_on=row.recorded_on,
            source=row.source,
            list_item_id=row.list_item_id,
            bcv_rate_value=row.bcv_rate_value,
            bcv_rate_date=row.bcv_rate_date,
            usdt_rate_value=row.usdt_rate_value,
            usdt_rate_date=row.usdt_rate_date,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class SyncShoppingListOut(BaseModel):
    """Raw stored shopping_lists row in sync payload."""

    id: UUID
    name: str
    status: str
    closed_at: datetime | None
    expense_id: UUID | None
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, row: SyncShoppingList) -> Self:
        """Build schema from SyncShoppingList."""
        return cls(
            id=row.id,
            name=row.name,
            status=row.status,
            closed_at=row.closed_at,
            expense_id=row.expense_id,
            deleted_at=row.deleted_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class SyncShoppingListItemOut(BaseModel):
    """Raw stored shopping_list_items row in sync payload."""

    id: UUID
    list_id: UUID
    product_id: UUID
    quantity: DecimalStr
    status: str
    bought_price: DecimalStr | None
    bought_currency: str | None
    bought_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, row: SyncShoppingListItem) -> Self:
        """Build schema from SyncShoppingListItem."""
        return cls(
            id=row.id,
            list_id=row.list_id,
            product_id=row.product_id,
            quantity=row.quantity,
            status=row.status,
            bought_price=row.bought_price,
            bought_currency=row.bought_currency,
            bought_at=row.bought_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class SyncGoalOut(BaseModel):
    """Raw stored goals row in sync payload."""

    id: UUID
    name: str
    target_usd: DecimalStr
    target_date: date | None
    note: str | None
    archived_at: datetime | None
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, row: SyncGoal) -> Self:
        """Build schema from SyncGoal."""
        return cls(
            id=row.id,
            name=row.name,
            target_usd=row.target_usd,
            target_date=row.target_date,
            note=row.note,
            archived_at=row.archived_at,
            deleted_at=row.deleted_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class SyncGoalContributionOut(BaseModel):
    """Raw stored goal_contributions row in sync payload."""

    id: UUID
    goal_id: UUID
    kind: str
    amount: DecimalStr
    currency: str
    contributed_on: date
    note: str | None
    bcv_rate_value: DecimalStr
    bcv_rate_date: date
    usdt_rate_value: DecimalStr
    usdt_rate_date: date
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, row: SyncGoalContribution) -> Self:
        """Build schema from SyncGoalContribution."""
        return cls(
            id=row.id,
            goal_id=row.goal_id,
            kind=row.kind,
            amount=row.amount,
            currency=row.currency,
            contributed_on=row.contributed_on,
            note=row.note,
            bcv_rate_value=row.bcv_rate_value,
            bcv_rate_date=row.bcv_rate_date,
            usdt_rate_value=row.usdt_rate_value,
            usdt_rate_date=row.usdt_rate_date,
            deleted_at=row.deleted_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class SyncTombstoneOut(BaseModel):
    """Hard-deletion tombstone in sync payload."""

    entity: SyncEntity
    id: UUID
    deleted_at: datetime

    @classmethod
    def from_domain(cls, tombstone: Tombstone) -> Self:
        """Build schema from Tombstone."""
        return cls(
            entity=tombstone.entity,
            id=tombstone.id,
            deleted_at=tombstone.deleted_at,
        )


class SyncChangesOut(BaseModel):
    """HTTP response payload for GET /sync/changes."""

    next_cursor: datetime
    income_rules: list[SyncIncomeRuleOut]
    income_occurrences: list[SyncIncomeOccurrenceOut]
    expense_categories: list[SyncExpenseCategoryOut]
    expenses: list[SyncExpenseOut]
    products: list[SyncProductOut]
    product_prices: list[SyncProductPriceOut]
    shopping_lists: list[SyncShoppingListOut]
    shopping_list_items: list[SyncShoppingListItemOut]
    goals: list[SyncGoalOut]
    goal_contributions: list[SyncGoalContributionOut]
    tombstones: list[SyncTombstoneOut]

    @classmethod
    def from_domain(cls, result: SyncChangesResult) -> Self:
        """Build HTTP response model from SyncChangesResult."""
        return cls(
            next_cursor=result.next_cursor,
            income_rules=[SyncIncomeRuleOut.from_domain(r) for r in result.income_rules],
            income_occurrences=[
                SyncIncomeOccurrenceOut.from_domain(r) for r in result.income_occurrences
            ],
            expense_categories=[
                SyncExpenseCategoryOut.from_domain(r) for r in result.expense_categories
            ],
            expenses=[SyncExpenseOut.from_domain(r) for r in result.expenses],
            products=[SyncProductOut.from_domain(r) for r in result.products],
            product_prices=[SyncProductPriceOut.from_domain(r) for r in result.product_prices],
            shopping_lists=[SyncShoppingListOut.from_domain(r) for r in result.shopping_lists],
            shopping_list_items=[
                SyncShoppingListItemOut.from_domain(r) for r in result.shopping_list_items
            ],
            goals=[SyncGoalOut.from_domain(r) for r in result.goals],
            goal_contributions=[
                SyncGoalContributionOut.from_domain(r) for r in result.goal_contributions
            ],
            tombstones=[SyncTombstoneOut.from_domain(t) for t in result.tombstones],
        )
