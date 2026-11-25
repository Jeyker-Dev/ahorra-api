from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from app.clock import Clock

MIN_GOAL_NAME_LENGTH = 1
MAX_GOAL_NAME_LENGTH = 50
MAX_NOTE_LENGTH = 500


class Currency(StrEnum):
    """Supported currencies for goal contributions."""

    usd = "usd"
    ves = "ves"


class ContributionKind(StrEnum):
    """Kind of goal contribution."""

    deposit = "deposit"
    withdrawal = "withdrawal"


@dataclass(frozen=True, slots=True)
class Goal:
    """Domain entity representing a savings goal."""

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
class GoalContribution:
    """Domain entity representing a deposit or withdrawal toward a goal."""

    id: UUID
    goal_id: UUID
    kind: ContributionKind
    amount: Decimal
    currency: Currency
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
class RateSnapshot:
    """Minimal exchange rate snapshot stored on contributions or used for equivalents."""

    value: Decimal
    value_date: date


@dataclass(frozen=True, slots=True)
class GoalProgress:
    """Computed progress summary for a savings goal."""

    saved_usd: Decimal
    remaining_usd: Decimal
    percent: Decimal
    reached: bool
    saved_ves_bcv: Decimal | None
    saved_ves_usdt: Decimal | None
    remaining_ves_bcv: Decimal | None
    remaining_ves_usdt: Decimal | None
    bcv_rate: RateSnapshot | None
    usdt_rate: RateSnapshot | None


@dataclass(frozen=True, slots=True)
class GoalWithProgress:
    """Goal paired with its computed progress."""

    goal: Goal
    progress: GoalProgress


@dataclass(frozen=True, slots=True)
class GoalInput:
    """Input payload for creating or updating a savings goal."""

    id: UUID
    name: str
    target_usd: Decimal
    target_date: date | None = None
    note: str | None = None


@dataclass(frozen=True, slots=True)
class ContributionInput:
    """Input payload for creating or updating a goal contribution."""

    id: UUID
    goal_id: UUID
    kind: ContributionKind
    amount: Decimal
    currency: Currency
    contributed_on: date
    note: str | None = None


@dataclass(frozen=True, slots=True)
class UpsertGoalResult:
    """Result of upserting a goal, indicating whether it was created."""

    item: GoalWithProgress
    created: bool


@dataclass(frozen=True, slots=True)
class UpsertContributionResult:
    """Result of upserting a contribution, indicating whether it was created."""

    contribution: GoalContribution
    created: bool


class RateLookup(Protocol):
    """Port for looking up BCV and USDT exchange rates in force on a given date."""

    async def bcv_in_force(self, on: date) -> RateSnapshot | None: ...

    async def usdt_in_force(self, on: date) -> RateSnapshot | None: ...


class GoalRepository(Protocol):
    """Persistence port for savings goals."""

    async def get_by_id(self, goal_id: UUID) -> Goal | None: ...

    async def get_by_lower_name(self, name: str) -> Goal | None: ...

    async def list_active(self) -> list[Goal]: ...

    async def list_all(self) -> list[Goal]: ...

    async def insert_if_absent(self, goal: Goal) -> tuple[Goal, bool]: ...

    async def update(self, goal: Goal) -> Goal: ...

    async def list_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[Goal]: ...


class GoalContributionRepository(Protocol):
    """Persistence port for goal contributions."""

    async def get_by_id(self, contribution_id: UUID) -> GoalContribution | None: ...

    async def list_for_goal(self, goal_id: UUID) -> list[GoalContribution]: ...

    async def sum_saved_usd_by_goal_ids(
        self,
        goal_ids: Sequence[UUID],
    ) -> dict[UUID, Decimal]: ...

    async def insert_if_absent(
        self,
        contribution: GoalContribution,
    ) -> tuple[GoalContribution, bool]: ...

    async def update(self, contribution: GoalContribution) -> GoalContribution: ...

    async def list_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[GoalContribution]: ...


@dataclass(frozen=True, slots=True)
class GoalCollaborators:
    """Grouped collaborators injected into GoalsService."""

    goals: GoalRepository
    contributions: GoalContributionRepository
    rates: RateLookup
    clock: Clock
