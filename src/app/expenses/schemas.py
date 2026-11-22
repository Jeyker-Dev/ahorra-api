from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, Field, PlainSerializer, field_validator, model_validator

from app.expenses.domain import (
    MAX_CATEGORY_NAME_LENGTH,
    MIN_CATEGORY_NAME_LENGTH,
    Currency,
    ExpenseCategory,
    ExpenseSummaryItem,
    ExpenseWithCategory,
)

DecimalStr = Annotated[
    Decimal,
    PlainSerializer(str, return_type=str, when_used="json"),
]


class CategoryUpsertIn(BaseModel):
    """Request body for creating or renaming an expense category."""

    name: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        """Strip whitespace and enforce category name length bounds."""
        stripped = value.strip()
        if len(stripped) < MIN_CATEGORY_NAME_LENGTH or len(stripped) > MAX_CATEGORY_NAME_LENGTH:
            raise ValueError(
                f"Name must be between {MIN_CATEGORY_NAME_LENGTH} "
                f"and {MAX_CATEGORY_NAME_LENGTH} characters."
            )
        return stripped


class CategoryListQuery(BaseModel):
    """Query parameters for listing expense categories."""

    include_archived: bool = False


class CategoryOut(BaseModel):
    """Public HTTP representation of an expense category."""

    id: UUID
    name: str
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, category: ExpenseCategory) -> Self:
        """Build an HTTP response model from a domain ExpenseCategory."""
        return cls(
            id=category.id,
            name=category.name,
            archived_at=category.archived_at,
            created_at=category.created_at,
            updated_at=category.updated_at,
        )


class ExpenseUpsertIn(BaseModel):
    """Request body for creating or updating an expense."""

    category_id: UUID
    amount: Decimal
    currency: Currency
    spent_on: date
    note: str | None = None


class ExpenseOut(BaseModel):
    """Public HTTP representation of an expense."""

    id: UUID
    category_id: UUID
    category_name: str
    amount: DecimalStr
    currency: Currency
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
    def from_with_category(cls, item: ExpenseWithCategory) -> Self:
        """Build an HTTP response model from an ExpenseWithCategory."""
        exp = item.expense
        return cls(
            id=exp.id,
            category_id=exp.category_id,
            category_name=item.category_name,
            amount=exp.amount,
            currency=exp.currency,
            spent_on=exp.spent_on,
            note=exp.note,
            bcv_rate_value=exp.bcv_rate_value,
            bcv_rate_date=exp.bcv_rate_date,
            usdt_rate_value=exp.usdt_rate_value,
            usdt_rate_date=exp.usdt_rate_date,
            deleted_at=exp.deleted_at,
            created_at=exp.created_at,
            updated_at=exp.updated_at,
        )


class ExpenseListQuery(BaseModel):
    """Query parameters for listing expenses within a date range."""

    from_date: date = Field(alias="from")
    to_date: date = Field(alias="to")
    category_id: UUID | None = None

    @model_validator(mode="after")
    def check_date_range(self) -> Self:
        """Reject requests where the start date is after the end date."""
        if self.from_date > self.to_date:
            raise ValueError("'from' must be less than or equal to 'to'.")
        return self


class ExpenseSummaryQuery(BaseModel):
    """Query parameters for summarizing expenses by category within a date range."""

    from_date: date = Field(alias="from")
    to_date: date = Field(alias="to")

    @model_validator(mode="after")
    def check_date_range(self) -> Self:
        """Reject requests where the start date is after the end date."""
        if self.from_date > self.to_date:
            raise ValueError("'from' must be less than or equal to 'to'.")
        return self


class ExpenseSummaryOut(BaseModel):
    """Public HTTP representation of one category row in the expense summary."""

    category_id: UUID
    category_name: str
    count: int
    total_usd: DecimalStr

    @classmethod
    def from_domain(cls, item: ExpenseSummaryItem) -> Self:
        """Build an HTTP response model from a domain ExpenseSummaryItem."""
        return cls(
            category_id=item.category_id,
            category_name=item.category_name,
            count=item.count,
            total_usd=item.total_usd,
        )
