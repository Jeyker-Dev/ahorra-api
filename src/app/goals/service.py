from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from uuid import UUID

from app.clock import caracas_date
from app.errors import BusinessRuleError
from app.goals.domain import (
    MAX_GOAL_NAME_LENGTH,
    MAX_NOTE_LENGTH,
    MIN_GOAL_NAME_LENGTH,
    ContributionInput,
    ContributionKind,
    Currency,
    Goal,
    GoalCollaborators,
    GoalContribution,
    GoalInput,
    GoalProgress,
    GoalWithProgress,
    RateSnapshot,
    UpsertContributionResult,
    UpsertGoalResult,
)
from app.goals.errors import (
    ContributionDeleted,
    ContributionNotFound,
    GoalArchived,
    GoalDeleted,
    GoalNameTaken,
    GoalNotFound,
    InvalidContributionAmount,
    InvalidContributionNote,
    InvalidGoalName,
    InvalidGoalNote,
    InvalidGoalTarget,
    RateNotAvailableForDate,
)

MONEY_QUANTUM = Decimal("0.01")
PERCENT_QUANTUM = Decimal("0.1")
PERCENT_SCALE = Decimal("100")


@dataclass(frozen=True, slots=True)
class _NormalizedGoalData:
    name: str
    target_usd: Decimal
    target_date: date | None
    note: str | None


@dataclass(frozen=True, slots=True)
class _NormalizedContributionData:
    goal_id: UUID
    kind: ContributionKind
    amount: Decimal
    currency: Currency
    contributed_on: date
    note: str | None


@dataclass(frozen=True, slots=True)
class _ContributionRates:
    bcv: RateSnapshot
    usdt: RateSnapshot


def _quantize_money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_EVEN)


def _validate_goal_name(raw_name: str) -> str:
    stripped = raw_name.strip()
    if len(stripped) < MIN_GOAL_NAME_LENGTH or len(stripped) > MAX_GOAL_NAME_LENGTH:
        raise InvalidGoalName(
            f"Goal name must be between {MIN_GOAL_NAME_LENGTH} "
            f"and {MAX_GOAL_NAME_LENGTH} characters."
        )
    return stripped


def _validate_positive_money(
    value: Decimal,
    error_cls: type[BusinessRuleError],
    message: str,
) -> Decimal:
    if not value.is_finite() or value <= Decimal("0"):
        raise error_cls(message)
    return _quantize_money(value)


def _normalize_note(
    raw_note: str | None,
    error_cls: type[BusinessRuleError],
    label: str,
) -> str | None:
    if raw_note is None:
        return None
    stripped = raw_note.strip()
    if not stripped:
        return None
    if len(stripped) > MAX_NOTE_LENGTH:
        raise error_cls(f"{label} note must be at most {MAX_NOTE_LENGTH} characters.")
    return stripped


def _normalize_goal_input(payload: GoalInput) -> _NormalizedGoalData:
    return _NormalizedGoalData(
        name=_validate_goal_name(payload.name),
        target_usd=_validate_positive_money(
            payload.target_usd,
            InvalidGoalTarget,
            "Goal target_usd must be greater than zero.",
        ),
        target_date=payload.target_date,
        note=_normalize_note(payload.note, InvalidGoalNote, "Goal"),
    )


def _normalize_contribution_input(
    payload: ContributionInput,
) -> _NormalizedContributionData:
    return _NormalizedContributionData(
        goal_id=payload.goal_id,
        kind=payload.kind,
        amount=_validate_positive_money(
            payload.amount,
            InvalidContributionAmount,
            "Contribution amount must be greater than zero.",
        ),
        currency=payload.currency,
        contributed_on=payload.contributed_on,
        note=_normalize_note(payload.note, InvalidContributionNote, "Contribution"),
    )


def _matches_goal(existing: Goal, normalized: _NormalizedGoalData) -> bool:
    return (
        existing.name == normalized.name
        and existing.target_usd == normalized.target_usd
        and existing.target_date == normalized.target_date
        and existing.note == normalized.note
    )


def _matches_contribution(
    existing: GoalContribution,
    normalized: _NormalizedContributionData,
) -> bool:
    return (
        existing.goal_id == normalized.goal_id
        and existing.kind == normalized.kind
        and existing.amount == normalized.amount
        and existing.currency == normalized.currency
        and existing.contributed_on == normalized.contributed_on
        and existing.note == normalized.note
    )


def _build_progress(
    goal: Goal,
    raw_saved_usd: Decimal,
    today_rates: tuple[RateSnapshot, RateSnapshot] | None,
) -> GoalProgress:
    saved_usd = _quantize_money(raw_saved_usd)
    remaining_usd = max(goal.target_usd - saved_usd, Decimal("0.00"))
    percent = (saved_usd / goal.target_usd * PERCENT_SCALE).quantize(
        PERCENT_QUANTUM,
        rounding=ROUND_HALF_EVEN,
    )
    reached = saved_usd >= goal.target_usd
    if today_rates is None:
        return GoalProgress(
            saved_usd=saved_usd,
            remaining_usd=remaining_usd,
            percent=percent,
            reached=reached,
            saved_ves_bcv=None,
            saved_ves_usdt=None,
            remaining_ves_bcv=None,
            remaining_ves_usdt=None,
            bcv_rate=None,
            usdt_rate=None,
        )
    bcv_rate, usdt_rate = today_rates
    return GoalProgress(
        saved_usd=saved_usd,
        remaining_usd=remaining_usd,
        percent=percent,
        reached=reached,
        saved_ves_bcv=_quantize_money(saved_usd * bcv_rate.value),
        saved_ves_usdt=_quantize_money(saved_usd * usdt_rate.value),
        remaining_ves_bcv=_quantize_money(remaining_usd * bcv_rate.value),
        remaining_ves_usdt=_quantize_money(remaining_usd * usdt_rate.value),
        bcv_rate=bcv_rate,
        usdt_rate=usdt_rate,
    )


class GoalsService:
    """Application service managing savings goals, contributions, and progress."""

    def __init__(self, collaborators: GoalCollaborators) -> None:
        self._goals = collaborators.goals
        self._contributions = collaborators.contributions
        self._rates = collaborators.rates
        self._clock = collaborators.clock

    async def upsert_goal(self, payload: GoalInput) -> UpsertGoalResult:
        """Create or idempotently update a savings goal."""
        normalized = _normalize_goal_input(payload)
        now = self._clock.now()

        existing = await self._goals.get_by_id(payload.id)
        if existing is not None:
            return await self._update_existing_goal(existing, normalized, now)

        await self._ensure_name_available(normalized.name, exclude_id=None)
        candidate = Goal(
            id=payload.id,
            name=normalized.name,
            target_usd=normalized.target_usd,
            target_date=normalized.target_date,
            note=normalized.note,
            archived_at=None,
            deleted_at=None,
            created_at=now,
            updated_at=now,
        )
        saved, inserted = await self._goals.insert_if_absent(candidate)
        if not inserted:
            return await self._update_existing_goal(saved, normalized, now)
        item = await self._attach_single_progress(saved)
        return UpsertGoalResult(item=item, created=True)

    async def get_goal(self, goal_id: UUID) -> GoalWithProgress:
        """Return a non-deleted goal with its computed progress."""
        goal = await self._require_non_deleted_goal(goal_id)
        return await self._attach_single_progress(goal)

    async def list_active_goals(self) -> list[GoalWithProgress]:
        """Return active non-deleted goals with progress, ordered by name and id."""
        goals = await self._goals.list_active()
        return await self._attach_progress(goals)

    async def list_all_goals(self) -> list[GoalWithProgress]:
        """Return all non-deleted goals (active first, then archived) with progress."""
        goals = await self._goals.list_all()
        return await self._attach_progress(goals)

    async def list_goals_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[Goal]:
        """Return goals changed in (since, until] ordered by (updated_at, id)."""
        return await self._goals.list_changed_between(since, until)

    async def list_contributions_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[GoalContribution]:
        """Return goal contributions changed in (since, until] ordered by (updated_at, id)."""
        return await self._contributions.list_changed_between(since, until)

    async def archive_goal(self, goal_id: UUID) -> GoalWithProgress:
        """Archive a non-deleted goal idempotently."""
        existing = await self._require_non_deleted_goal(goal_id)
        if existing.archived_at is not None:
            return await self._attach_single_progress(existing)
        now = self._clock.now()
        saved = await self._goals.update(replace(existing, archived_at=now, updated_at=now))
        return await self._attach_single_progress(saved)

    async def unarchive_goal(self, goal_id: UUID) -> GoalWithProgress:
        """Unarchive a non-deleted goal idempotently."""
        existing = await self._require_non_deleted_goal(goal_id)
        if existing.archived_at is None:
            return await self._attach_single_progress(existing)
        now = self._clock.now()
        saved = await self._goals.update(replace(existing, archived_at=None, updated_at=now))
        return await self._attach_single_progress(saved)

    async def delete_goal(self, goal_id: UUID) -> GoalWithProgress:
        """Soft-delete a goal idempotently."""
        existing = await self._goals.get_by_id(goal_id)
        if existing is None:
            raise GoalNotFound(f"Goal {goal_id} was not found.")
        if existing.deleted_at is not None:
            return await self._attach_single_progress(existing)
        now = self._clock.now()
        saved = await self._goals.update(replace(existing, deleted_at=now, updated_at=now))
        return await self._attach_single_progress(saved)

    async def upsert_contribution(
        self,
        payload: ContributionInput,
    ) -> UpsertContributionResult:
        """Create or idempotently update/move a goal contribution."""
        normalized = _normalize_contribution_input(payload)
        existing = await self._contributions.get_by_id(payload.id)
        if existing is not None and existing.deleted_at is not None:
            raise ContributionDeleted(f"Contribution {payload.id} has been deleted.")

        goal = await self._require_non_deleted_goal(normalized.goal_id)
        now = self._clock.now()

        if existing is not None:
            return await self._update_existing_contribution(existing, normalized, goal, now)

        if goal.archived_at is not None:
            raise GoalArchived(f"Goal {goal.id} is archived.")

        rates = await self._lookup_rates_required(normalized.contributed_on)
        candidate = GoalContribution(
            id=payload.id,
            goal_id=normalized.goal_id,
            kind=normalized.kind,
            amount=normalized.amount,
            currency=normalized.currency,
            contributed_on=normalized.contributed_on,
            note=normalized.note,
            bcv_rate_value=rates.bcv.value,
            bcv_rate_date=rates.bcv.value_date,
            usdt_rate_value=rates.usdt.value,
            usdt_rate_date=rates.usdt.value_date,
            deleted_at=None,
            created_at=now,
            updated_at=now,
        )
        saved, inserted = await self._contributions.insert_if_absent(candidate)
        if not inserted:
            return await self._update_existing_contribution(saved, normalized, goal, now)
        return UpsertContributionResult(contribution=saved, created=True)

    async def delete_contribution(
        self,
        goal_id: UUID,
        contribution_id: UUID,
    ) -> GoalContribution:
        """Soft-delete a goal contribution idempotently."""
        await self._require_non_deleted_goal(goal_id)
        existing = await self._contributions.get_by_id(contribution_id)
        if existing is None or existing.goal_id != goal_id:
            raise ContributionNotFound(
                f"Contribution {contribution_id} was not found on goal {goal_id}."
            )
        if existing.deleted_at is not None:
            return existing
        now = self._clock.now()
        return await self._contributions.update(replace(existing, deleted_at=now, updated_at=now))

    async def list_contributions(self, goal_id: UUID) -> list[GoalContribution]:
        """List non-deleted contributions for a goal."""
        await self._require_non_deleted_goal(goal_id)
        return await self._contributions.list_for_goal(goal_id)

    async def _require_non_deleted_goal(self, goal_id: UUID) -> Goal:
        goal = await self._goals.get_by_id(goal_id)
        if goal is None or goal.deleted_at is not None:
            raise GoalNotFound(f"Goal {goal_id} was not found.")
        return goal

    async def _ensure_name_available(self, name: str, *, exclude_id: UUID | None) -> None:
        existing = await self._goals.get_by_lower_name(name)
        if existing is not None and existing.id != exclude_id:
            raise GoalNameTaken(f"Goal name {name!r} is already taken.")

    async def _update_existing_goal(
        self,
        existing: Goal,
        normalized: _NormalizedGoalData,
        now: datetime,
    ) -> UpsertGoalResult:
        if existing.deleted_at is not None:
            raise GoalDeleted(f"Goal {existing.id} has been deleted.")
        if _matches_goal(existing, normalized):
            return UpsertGoalResult(
                item=await self._attach_single_progress(existing),
                created=False,
            )
        await self._ensure_name_available(normalized.name, exclude_id=existing.id)
        saved = await self._goals.update(
            replace(
                existing,
                name=normalized.name,
                target_usd=normalized.target_usd,
                target_date=normalized.target_date,
                note=normalized.note,
                updated_at=now,
            )
        )
        return UpsertGoalResult(
            item=await self._attach_single_progress(saved),
            created=False,
        )

    async def _update_existing_contribution(
        self,
        existing: GoalContribution,
        normalized: _NormalizedContributionData,
        goal: Goal,
        now: datetime,
    ) -> UpsertContributionResult:
        if existing.deleted_at is not None:
            raise ContributionDeleted(f"Contribution {existing.id} has been deleted.")
        if _matches_contribution(existing, normalized):
            return UpsertContributionResult(contribution=existing, created=False)
        if existing.goal_id != normalized.goal_id and goal.archived_at is not None:
            raise GoalArchived(f"Goal {goal.id} is archived.")

        rates = (
            await self._lookup_rates_required(normalized.contributed_on)
            if existing.contributed_on != normalized.contributed_on
            else _ContributionRates(
                bcv=RateSnapshot(existing.bcv_rate_value, existing.bcv_rate_date),
                usdt=RateSnapshot(existing.usdt_rate_value, existing.usdt_rate_date),
            )
        )
        saved = await self._contributions.update(
            replace(
                existing,
                goal_id=normalized.goal_id,
                kind=normalized.kind,
                amount=normalized.amount,
                currency=normalized.currency,
                contributed_on=normalized.contributed_on,
                note=normalized.note,
                bcv_rate_value=rates.bcv.value,
                bcv_rate_date=rates.bcv.value_date,
                usdt_rate_value=rates.usdt.value,
                usdt_rate_date=rates.usdt.value_date,
                updated_at=now,
            )
        )
        return UpsertContributionResult(contribution=saved, created=False)

    async def _lookup_rates_required(self, on: date) -> _ContributionRates:
        bcv = await self._rates.bcv_in_force(on)
        if bcv is None:
            raise RateNotAvailableForDate(
                f"No BCV exchange rate is available for {on.isoformat()}."
            )
        usdt = await self._rates.usdt_in_force(on)
        if usdt is None:
            raise RateNotAvailableForDate(
                f"No USDT exchange rate is available for {on.isoformat()}."
            )
        return _ContributionRates(bcv=bcv, usdt=usdt)

    async def _lookup_today_rates_optional(self) -> tuple[RateSnapshot, RateSnapshot] | None:
        today = caracas_date(self._clock.now())
        bcv = await self._rates.bcv_in_force(today)
        if bcv is None:
            return None
        usdt = await self._rates.usdt_in_force(today)
        if usdt is None:
            return None
        return bcv, usdt

    async def _attach_single_progress(self, goal: Goal) -> GoalWithProgress:
        items = await self._attach_progress([goal])
        return items[0]

    async def _attach_progress(self, goals: list[Goal]) -> list[GoalWithProgress]:
        if not goals:
            return []
        sums = await self._contributions.sum_saved_usd_by_goal_ids([g.id for g in goals])
        today_rates = await self._lookup_today_rates_optional()
        return [
            GoalWithProgress(
                goal=goal,
                progress=_build_progress(goal, sums.get(goal.id, Decimal("0")), today_rates),
            )
            for goal in goals
        ]
