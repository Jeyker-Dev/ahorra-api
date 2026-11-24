from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, PlainSerializer

from app.goals.domain import (
    ContributionKind,
    Currency,
    GoalContribution,
    GoalProgress,
    GoalWithProgress,
    RateSnapshot,
)

DecimalStr = Annotated[
    Decimal,
    PlainSerializer(str, return_type=str, when_used="json"),
]


class GoalUpsertIn(BaseModel):
    """Request body for creating or updating a savings goal."""

    name: str
    target_usd: Decimal
    target_date: date | None = None
    note: str | None = None


class GoalListQuery(BaseModel):
    """Query parameters for listing savings goals."""

    include_archived: bool = False


class RateSnapshotOut(BaseModel):
    """Public HTTP representation of an exchange rate snapshot."""

    value: DecimalStr
    value_date: date

    @classmethod
    def from_domain(cls, snapshot: RateSnapshot) -> Self:
        """Build an HTTP response model from a domain RateSnapshot."""
        return cls(value=snapshot.value, value_date=snapshot.value_date)


class GoalProgressOut(BaseModel):
    """Public HTTP representation of computed savings goal progress."""

    saved_usd: DecimalStr
    remaining_usd: DecimalStr
    percent: DecimalStr
    reached: bool
    saved_ves_bcv: DecimalStr | None
    saved_ves_usdt: DecimalStr | None
    remaining_ves_bcv: DecimalStr | None
    remaining_ves_usdt: DecimalStr | None
    bcv_rate: RateSnapshotOut | None
    usdt_rate: RateSnapshotOut | None

    @classmethod
    def from_domain(cls, progress: GoalProgress) -> Self:
        """Build an HTTP response model from a domain GoalProgress."""
        return cls(
            saved_usd=progress.saved_usd,
            remaining_usd=progress.remaining_usd,
            percent=progress.percent,
            reached=progress.reached,
            saved_ves_bcv=progress.saved_ves_bcv,
            saved_ves_usdt=progress.saved_ves_usdt,
            remaining_ves_bcv=progress.remaining_ves_bcv,
            remaining_ves_usdt=progress.remaining_ves_usdt,
            bcv_rate=(
                RateSnapshotOut.from_domain(progress.bcv_rate)
                if progress.bcv_rate is not None
                else None
            ),
            usdt_rate=(
                RateSnapshotOut.from_domain(progress.usdt_rate)
                if progress.usdt_rate is not None
                else None
            ),
        )


class GoalOut(BaseModel):
    """Public HTTP representation of a savings goal with its progress."""

    id: UUID
    name: str
    target_usd: DecimalStr
    target_date: date | None
    note: str | None
    archived_at: datetime | None
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime
    progress: GoalProgressOut

    @classmethod
    def from_with_progress(cls, item: GoalWithProgress) -> Self:
        """Build an HTTP response model from a domain GoalWithProgress."""
        goal = item.goal
        return cls(
            id=goal.id,
            name=goal.name,
            target_usd=goal.target_usd,
            target_date=goal.target_date,
            note=goal.note,
            archived_at=goal.archived_at,
            deleted_at=goal.deleted_at,
            created_at=goal.created_at,
            updated_at=goal.updated_at,
            progress=GoalProgressOut.from_domain(item.progress),
        )


class ContributionUpsertIn(BaseModel):
    """Request body for creating or updating a goal contribution."""

    kind: ContributionKind
    amount: Decimal
    currency: Currency
    contributed_on: date
    note: str | None = None


class ContributionOut(BaseModel):
    """Public HTTP representation of a goal contribution."""

    id: UUID
    goal_id: UUID
    kind: ContributionKind
    amount: DecimalStr
    currency: Currency
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
    def from_domain(cls, contribution: GoalContribution) -> Self:
        """Build an HTTP response model from a domain GoalContribution."""
        return cls(
            id=contribution.id,
            goal_id=contribution.goal_id,
            kind=contribution.kind,
            amount=contribution.amount,
            currency=contribution.currency,
            contributed_on=contribution.contributed_on,
            note=contribution.note,
            bcv_rate_value=contribution.bcv_rate_value,
            bcv_rate_date=contribution.bcv_rate_date,
            usdt_rate_value=contribution.usdt_rate_value,
            usdt_rate_date=contribution.usdt_rate_date,
            deleted_at=contribution.deleted_at,
            created_at=contribution.created_at,
            updated_at=contribution.updated_at,
        )
