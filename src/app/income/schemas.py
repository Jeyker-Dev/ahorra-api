from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, Field, PlainSerializer, field_validator, model_validator

from app.income.domain import (
    MAX_RULE_NAME_LENGTH,
    MIN_RULE_NAME_LENGTH,
    Currency,
    IncomeOccurrence,
    IncomeRule,
    OccurrenceStatus,
    OccurrenceWithRule,
)

DecimalStr = Annotated[
    Decimal,
    PlainSerializer(str, return_type=str, when_used="json"),
]


class IncomeRuleUpsertIn(BaseModel):
    """Request body for creating or updating an income rule."""

    name: str
    amount: Decimal
    currency: Currency
    days: list[int]
    start_date: date
    end_date: date | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        """Strip whitespace and enforce rule name length bounds."""
        stripped = value.strip()
        if len(stripped) < MIN_RULE_NAME_LENGTH or len(stripped) > MAX_RULE_NAME_LENGTH:
            raise ValueError(
                f"Name must be between {MIN_RULE_NAME_LENGTH} "
                f"and {MAX_RULE_NAME_LENGTH} characters."
            )
        return stripped


class IncomeRuleOut(BaseModel):
    """Public HTTP representation of an income rule."""

    id: UUID
    name: str
    amount: DecimalStr
    currency: Currency
    days: list[int]
    start_date: date
    end_date: date | None
    ended_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, rule: IncomeRule) -> Self:
        """Build an HTTP response model from a domain IncomeRule."""
        return cls(
            id=rule.id,
            name=rule.name,
            amount=rule.amount,
            currency=rule.currency,
            days=list(rule.days),
            start_date=rule.start_date,
            end_date=rule.end_date,
            ended_at=rule.ended_at,
            created_at=rule.created_at,
            updated_at=rule.updated_at,
        )


class OccurrenceListQuery(BaseModel):
    """Query parameters for listing income occurrences within a date range."""

    from_date: date = Field(alias="from")
    to_date: date = Field(alias="to")
    status: OccurrenceStatus | None = None

    @model_validator(mode="after")
    def check_date_range(self) -> Self:
        """Reject requests where the start date is after the end date."""
        if self.from_date > self.to_date:
            raise ValueError("'from' must be less than or equal to 'to'.")
        return self


class OccurrenceOut(BaseModel):
    """Public HTTP representation of an income occurrence."""

    id: UUID
    rule_id: UUID
    expected_date: date
    expected_amount: DecimalStr
    currency: Currency
    status: OccurrenceStatus
    received_date: date | None
    received_amount: DecimalStr | None
    rate_value: DecimalStr | None
    rate_date: date | None
    received_at: datetime | None
    updated_at: datetime

    @classmethod
    def from_domain(cls, occurrence: IncomeOccurrence) -> Self:
        """Build an HTTP response model from a domain IncomeOccurrence."""
        return cls(
            id=occurrence.id,
            rule_id=occurrence.rule_id,
            expected_date=occurrence.expected_date,
            expected_amount=occurrence.expected_amount,
            currency=occurrence.currency,
            status=occurrence.status,
            received_date=occurrence.received_date,
            received_amount=occurrence.received_amount,
            rate_value=occurrence.rate_value,
            rate_date=occurrence.rate_date,
            received_at=occurrence.received_at,
            updated_at=occurrence.updated_at,
        )


class OccurrenceWithRuleOut(OccurrenceOut):
    """HTTP representation of an income occurrence including its parent rule name."""

    rule_name: str

    @classmethod
    def from_with_rule(cls, item: OccurrenceWithRule) -> Self:
        """Build an HTTP response model from an OccurrenceWithRule."""
        occ = item.occurrence
        return cls(
            id=occ.id,
            rule_id=occ.rule_id,
            rule_name=item.rule_name,
            expected_date=occ.expected_date,
            expected_amount=occ.expected_amount,
            currency=occ.currency,
            status=occ.status,
            received_date=occ.received_date,
            received_amount=occ.received_amount,
            rate_value=occ.rate_value,
            rate_date=occ.rate_date,
            received_at=occ.received_at,
            updated_at=occ.updated_at,
        )


class ReceiveOccurrenceIn(BaseModel):
    """Optional request body for marking or correcting a received occurrence."""

    received_amount: Decimal | None = None
    received_date: date | None = None
