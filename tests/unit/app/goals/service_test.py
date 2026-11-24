from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.goals.domain import (
    ContributionInput,
    ContributionKind,
    Currency,
    Goal,
    GoalCollaborators,
    GoalContribution,
    GoalInput,
    RateSnapshot,
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
from app.goals.service import GoalsService


class _FakeClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def now(self) -> datetime:
        return self.current


class _InMemoryGoalRepository:
    def __init__(self, initial: list[Goal] | None = None) -> None:
        self.items: dict[UUID, Goal] = {g.id: g for g in initial or []}
        self.update_calls = 0
        self.simulate_concurrent_existing = False

    async def get_by_id(self, goal_id: UUID) -> Goal | None:
        return self.items.get(goal_id)

    async def get_by_lower_name(self, name: str) -> Goal | None:
        target = name.lower()
        for goal in self.items.values():
            if goal.deleted_at is None and goal.name.lower() == target:
                return goal
        return None

    async def list_active(self) -> list[Goal]:
        return sorted(
            (g for g in self.items.values() if g.deleted_at is None and g.archived_at is None),
            key=lambda g: (g.name, g.id),
        )

    async def list_all(self) -> list[Goal]:
        return sorted(
            (g for g in self.items.values() if g.deleted_at is None),
            key=lambda g: (g.archived_at is not None, g.name, g.id),
        )

    async def insert_if_absent(self, goal: Goal) -> tuple[Goal, bool]:
        if self.simulate_concurrent_existing:
            self.simulate_concurrent_existing = False
            self.items[goal.id] = goal
            return goal, False
        if goal.id in self.items:
            return self.items[goal.id], False
        existing_name = await self.get_by_lower_name(goal.name)
        if existing_name is not None:
            raise GoalNameTaken(f"Goal name {goal.name!r} is already taken.")
        self.items[goal.id] = goal
        return goal, True

    async def update(self, goal: Goal) -> Goal:
        self.update_calls += 1
        existing_name = await self.get_by_lower_name(goal.name)
        if existing_name is not None and existing_name.id != goal.id and goal.deleted_at is None:
            raise GoalNameTaken(f"Goal name {goal.name!r} is already taken.")
        self.items[goal.id] = goal
        return goal


class _InMemoryContributionRepository:
    def __init__(self, initial: list[GoalContribution] | None = None) -> None:
        self.items: dict[UUID, GoalContribution] = {c.id: c for c in initial or []}
        self.update_calls = 0
        self.batch_sum_calls = 0

    async def get_by_id(self, contribution_id: UUID) -> GoalContribution | None:
        return self.items.get(contribution_id)

    async def list_for_goal(self, goal_id: UUID) -> list[GoalContribution]:
        matching = [c for c in self.items.values() if c.goal_id == goal_id and c.deleted_at is None]
        matching.sort(
            key=lambda c: (-c.contributed_on.toordinal(), -c.created_at.timestamp(), c.id)
        )
        return matching

    async def sum_saved_usd_by_goal_ids(
        self,
        goal_ids: Sequence[UUID],
    ) -> dict[UUID, Decimal]:
        self.batch_sum_calls += 1
        target_ids = set(goal_ids)
        totals: dict[UUID, Decimal] = {}
        for contrib in self.items.values():
            if contrib.deleted_at is not None or contrib.goal_id not in target_ids:
                continue
            usd = (
                contrib.amount
                if contrib.currency == Currency.usd
                else contrib.amount / contrib.bcv_rate_value
            )
            signed = -usd if contrib.kind == ContributionKind.withdrawal else usd
            totals[contrib.goal_id] = totals.get(contrib.goal_id, Decimal("0")) + signed
        return totals

    async def insert_if_absent(
        self,
        contribution: GoalContribution,
    ) -> tuple[GoalContribution, bool]:
        if contribution.id in self.items:
            return self.items[contribution.id], False
        self.items[contribution.id] = contribution
        return contribution, True

    async def update(self, contribution: GoalContribution) -> GoalContribution:
        self.update_calls += 1
        self.items[contribution.id] = contribution
        return contribution


class _FakeRateLookup:
    def __init__(
        self,
        bcv_rates: dict[date, RateSnapshot] | None = None,
        usdt_rates: dict[date, RateSnapshot] | None = None,
    ) -> None:
        self.bcv_rates = bcv_rates or {}
        self.usdt_rates = usdt_rates or {}
        self.bcv_calls: list[date] = []
        self.usdt_calls: list[date] = []

    async def bcv_in_force(self, on: date) -> RateSnapshot | None:
        self.bcv_calls.append(on)
        eligible = [d for d in self.bcv_rates if d <= on]
        if not eligible:
            return None
        return self.bcv_rates[max(eligible)]

    async def usdt_in_force(self, on: date) -> RateSnapshot | None:
        self.usdt_calls.append(on)
        eligible = [d for d in self.usdt_rates if d <= on]
        if not eligible:
            return None
        return self.usdt_rates[max(eligible)]


def _default_rates() -> tuple[dict[date, RateSnapshot], dict[date, RateSnapshot]]:
    d1 = date(2026, 11, 20)
    d2 = date(2026, 11, 23)
    return (
        {
            d1: RateSnapshot(value=Decimal("800.00000000"), value_date=d1),
            d2: RateSnapshot(value=Decimal("900.00000000"), value_date=d2),
        },
        {
            d1: RateSnapshot(value=Decimal("850.00000000"), value_date=d1),
            d2: RateSnapshot(value=Decimal("950.00000000"), value_date=d2),
        },
    )


def _make_service(
    *,
    now: datetime | None = None,
    goals: list[Goal] | None = None,
    contributions: list[GoalContribution] | None = None,
    bcv_rates: dict[date, RateSnapshot] | None = None,
    usdt_rates: dict[date, RateSnapshot] | None = None,
) -> tuple[
    GoalsService,
    _InMemoryGoalRepository,
    _InMemoryContributionRepository,
    _FakeRateLookup,
    _FakeClock,
]:
    default_bcv, default_usdt = _default_rates()
    clock = _FakeClock(now or datetime(2026, 11, 23, 14, 0, tzinfo=UTC))
    goal_repo = _InMemoryGoalRepository(goals)
    contrib_repo = _InMemoryContributionRepository(contributions)
    rates = _FakeRateLookup(
        bcv_rates=default_bcv if bcv_rates is None else bcv_rates,
        usdt_rates=default_usdt if usdt_rates is None else usdt_rates,
    )
    service = GoalsService(
        GoalCollaborators(
            goals=goal_repo,
            contributions=contrib_repo,
            rates=rates,
            clock=clock,
        )
    )
    return service, goal_repo, contrib_repo, rates, clock


# Rule 1: upsert_goal


async def test_upsert_goal_creates_normalizes_and_is_idempotent() -> None:
    service, goal_repo, _, _, clock = _make_service()
    goal_id = uuid4()

    created = await service.upsert_goal(
        GoalInput(
            id=goal_id,
            name="  Emergency Fund  ",
            target_usd=Decimal("1000.125"),
            target_date=date(2027, 1, 1),
            note="   ",
        )
    )
    assert created.created is True
    assert created.item.goal.name == "Emergency Fund"
    assert created.item.goal.target_usd == Decimal("1000.12")
    assert created.item.goal.note is None
    assert created.item.goal.created_at == clock.current

    clock.current += timedelta(minutes=5)
    retry = await service.upsert_goal(
        GoalInput(
            id=goal_id,
            name="Emergency Fund",
            target_usd=Decimal("1000.12"),
            target_date=date(2027, 1, 1),
            note="",
        )
    )
    assert retry.created is False
    assert retry.item.goal.updated_at == created.item.goal.updated_at
    assert goal_repo.update_calls == 0


async def test_upsert_goal_updates_changed_fields_and_allows_archived() -> None:
    service, _, _, _, clock = _make_service()
    goal_id = uuid4()
    await service.upsert_goal(GoalInput(id=goal_id, name="Laptop", target_usd=Decimal("800")))
    await service.archive_goal(goal_id)

    clock.current += timedelta(minutes=5)
    updated = await service.upsert_goal(
        GoalInput(id=goal_id, name="Work Laptop", target_usd=Decimal("900"), note="Pro model")
    )
    assert updated.created is False
    assert updated.item.goal.name == "Work Laptop"
    assert updated.item.goal.target_usd == Decimal("900.00")
    assert updated.item.goal.note == "Pro model"


async def test_upsert_goal_handles_concurrent_insert_idempotently() -> None:
    service, goal_repo, _, _, _ = _make_service()
    goal_repo.simulate_concurrent_existing = True
    goal_id = uuid4()

    result = await service.upsert_goal(
        GoalInput(id=goal_id, name="Trip", target_usd=Decimal("500"))
    )
    assert result.created is False
    assert result.item.goal.id == goal_id


async def test_upsert_goal_raises_invalid_goal_name() -> None:
    service, _, _, _, _ = _make_service()

    with pytest.raises(InvalidGoalName):
        await service.upsert_goal(GoalInput(id=uuid4(), name="   ", target_usd=Decimal("100")))

    with pytest.raises(InvalidGoalName):
        await service.upsert_goal(GoalInput(id=uuid4(), name="x" * 51, target_usd=Decimal("100")))


async def test_upsert_goal_raises_invalid_goal_target() -> None:
    service, _, _, _, _ = _make_service()

    with pytest.raises(InvalidGoalTarget):
        await service.upsert_goal(GoalInput(id=uuid4(), name="Trip", target_usd=Decimal("0")))


async def test_upsert_goal_raises_invalid_goal_note() -> None:
    service, _, _, _, _ = _make_service()

    with pytest.raises(InvalidGoalNote):
        await service.upsert_goal(
            GoalInput(id=uuid4(), name="Trip", target_usd=Decimal("100"), note="x" * 501)
        )


async def test_upsert_goal_raises_goal_name_taken_case_insensitively() -> None:
    service, _, _, _, _ = _make_service()
    await service.upsert_goal(GoalInput(id=uuid4(), name="Car", target_usd=Decimal("5000")))

    with pytest.raises(GoalNameTaken):
        await service.upsert_goal(GoalInput(id=uuid4(), name="car", target_usd=Decimal("6000")))


async def test_upsert_goal_allows_reusing_deleted_goal_name() -> None:
    service, _, _, _, _ = _make_service()
    old_id = uuid4()
    await service.upsert_goal(GoalInput(id=old_id, name="Car", target_usd=Decimal("5000")))
    await service.delete_goal(old_id)

    created = await service.upsert_goal(
        GoalInput(id=uuid4(), name="car", target_usd=Decimal("6000"))
    )
    assert created.created is True


async def test_upsert_goal_raises_goal_deleted() -> None:
    service, _, _, _, _ = _make_service()
    goal_id = uuid4()
    await service.upsert_goal(GoalInput(id=goal_id, name="Car", target_usd=Decimal("5000")))
    await service.delete_goal(goal_id)

    with pytest.raises(GoalDeleted):
        await service.upsert_goal(GoalInput(id=goal_id, name="Car", target_usd=Decimal("5000")))


# Rule 2: archive_goal / unarchive_goal & list/get


async def test_archive_and_unarchive_goal_are_idempotent_without_noop_bump() -> None:
    service, goal_repo, _, _, clock = _make_service()
    goal_id = uuid4()
    await service.upsert_goal(GoalInput(id=goal_id, name="Home", target_usd=Decimal("10000")))

    clock.current += timedelta(minutes=5)
    archived = await service.archive_goal(goal_id)
    assert archived.goal.archived_at == clock.current
    updates = goal_repo.update_calls

    clock.current += timedelta(minutes=5)
    archived_again = await service.archive_goal(goal_id)
    assert archived_again.goal.updated_at == archived.goal.updated_at
    assert goal_repo.update_calls == updates

    clock.current += timedelta(minutes=5)
    unarchived = await service.unarchive_goal(goal_id)
    assert unarchived.goal.archived_at is None
    assert unarchived.goal.updated_at == clock.current
    updates = goal_repo.update_calls

    clock.current += timedelta(minutes=5)
    unarchived_again = await service.unarchive_goal(goal_id)
    assert unarchived_again.goal.updated_at == unarchived.goal.updated_at
    assert goal_repo.update_calls == updates


async def test_archive_goal_raises_goal_not_found_when_missing_or_deleted() -> None:
    service, _, _, _, _ = _make_service()
    goal_id = uuid4()
    await service.upsert_goal(GoalInput(id=goal_id, name="Home", target_usd=Decimal("10000")))
    await service.delete_goal(goal_id)

    with pytest.raises(GoalNotFound):
        await service.archive_goal(uuid4())
    with pytest.raises(GoalNotFound):
        await service.archive_goal(goal_id)
    with pytest.raises(GoalNotFound):
        await service.unarchive_goal(goal_id)


async def test_list_goals_filters_archived_orders_and_batches_progress() -> None:
    service, _, contrib_repo, _, _ = _make_service()
    g_archived = uuid4()
    g_active = uuid4()
    g_deleted = uuid4()
    await service.upsert_goal(GoalInput(id=g_archived, name="Alpha", target_usd=Decimal("100")))
    await service.upsert_goal(GoalInput(id=g_active, name="Zebra", target_usd=Decimal("100")))
    await service.upsert_goal(GoalInput(id=g_deleted, name="Deleted", target_usd=Decimal("100")))
    await service.archive_goal(g_archived)
    await service.delete_goal(g_deleted)
    contrib_repo.batch_sum_calls = 0

    active = await service.list_active_goals()
    assert [item.goal.id for item in active] == [g_active]

    all_goals = await service.list_all_goals()
    assert [item.goal.id for item in all_goals] == [g_active, g_archived]
    assert contrib_repo.batch_sum_calls == 2


# Rule 3: delete_goal


async def test_delete_goal_soft_deletes_idempotently_and_hides_goal() -> None:
    service, goal_repo, _, _, clock = _make_service()
    goal_id = uuid4()
    await service.upsert_goal(GoalInput(id=goal_id, name="Trip", target_usd=Decimal("500")))

    clock.current += timedelta(minutes=5)
    deleted = await service.delete_goal(goal_id)
    assert deleted.goal.deleted_at == clock.current
    updates = goal_repo.update_calls

    clock.current += timedelta(minutes=5)
    retry = await service.delete_goal(goal_id)
    assert retry.goal.deleted_at == deleted.goal.deleted_at
    assert goal_repo.update_calls == updates

    with pytest.raises(GoalNotFound):
        await service.get_goal(goal_id)


async def test_delete_goal_raises_goal_not_found() -> None:
    service, _, _, _, _ = _make_service()

    with pytest.raises(GoalNotFound):
        await service.delete_goal(uuid4())


# Rule 4: upsert_contribution


async def test_upsert_contribution_creates_and_keeps_rates_when_date_unchanged() -> None:
    service, _, _, rates, clock = _make_service()
    goal_id = uuid4()
    contrib_id = uuid4()
    await service.upsert_goal(GoalInput(id=goal_id, name="Fund", target_usd=Decimal("1000")))

    created = await service.upsert_contribution(
        ContributionInput(
            id=contrib_id,
            goal_id=goal_id,
            kind=ContributionKind.deposit,
            amount=Decimal("100.00"),
            currency=Currency.usd,
            contributed_on=date(2026, 11, 20),
            note="  Initial  ",
        )
    )
    assert created.created is True
    assert created.contribution.bcv_rate_value == Decimal("800.00000000")
    assert created.contribution.note == "Initial"
    calls_before = len(rates.bcv_calls)

    clock.current += timedelta(minutes=5)
    updated = await service.upsert_contribution(
        ContributionInput(
            id=contrib_id,
            goal_id=goal_id,
            kind=ContributionKind.deposit,
            amount=Decimal("150.00"),
            currency=Currency.usd,
            contributed_on=date(2026, 11, 20),
            note="Initial",
        )
    )
    assert updated.created is False
    assert updated.contribution.amount == Decimal("150.00")
    assert len(rates.bcv_calls) == calls_before


async def test_upsert_contribution_identical_retry_is_noop() -> None:
    service, _, contrib_repo, _, clock = _make_service()
    goal_id = uuid4()
    contrib_id = uuid4()
    await service.upsert_goal(GoalInput(id=goal_id, name="Fund", target_usd=Decimal("1000")))
    await service.upsert_contribution(
        ContributionInput(
            id=contrib_id,
            goal_id=goal_id,
            kind=ContributionKind.deposit,
            amount=Decimal("100.00"),
            currency=Currency.usd,
            contributed_on=date(2026, 11, 20),
            note="  Note  ",
        )
    )

    clock.current += timedelta(minutes=5)
    retry = await service.upsert_contribution(
        ContributionInput(
            id=contrib_id,
            goal_id=goal_id,
            kind=ContributionKind.deposit,
            amount=Decimal("100.000"),
            currency=Currency.usd,
            contributed_on=date(2026, 11, 20),
            note="Note",
        )
    )
    assert retry.created is False
    assert contrib_repo.update_calls == 0


async def test_upsert_contribution_refreshes_rates_when_contributed_on_changes() -> None:
    service, _, _, _, _ = _make_service()
    goal_id = uuid4()
    contrib_id = uuid4()
    await service.upsert_goal(GoalInput(id=goal_id, name="Fund", target_usd=Decimal("1000")))
    await service.upsert_contribution(
        ContributionInput(
            id=contrib_id,
            goal_id=goal_id,
            kind=ContributionKind.deposit,
            amount=Decimal("100.00"),
            currency=Currency.usd,
            contributed_on=date(2026, 11, 20),
        )
    )

    updated = await service.upsert_contribution(
        ContributionInput(
            id=contrib_id,
            goal_id=goal_id,
            kind=ContributionKind.deposit,
            amount=Decimal("100.00"),
            currency=Currency.usd,
            contributed_on=date(2026, 11, 23),
        )
    )
    assert updated.contribution.bcv_rate_value == Decimal("900.00000000")
    assert updated.contribution.usdt_rate_value == Decimal("950.00000000")


async def test_upsert_contribution_raises_invalid_contribution_amount() -> None:
    service, _, _, _, _ = _make_service()

    with pytest.raises(InvalidContributionAmount):
        await service.upsert_contribution(
            ContributionInput(
                id=uuid4(),
                goal_id=uuid4(),
                kind=ContributionKind.deposit,
                amount=Decimal("0"),
                currency=Currency.usd,
                contributed_on=date(2026, 11, 23),
            )
        )


async def test_upsert_contribution_raises_invalid_contribution_note() -> None:
    service, _, _, _, _ = _make_service()

    with pytest.raises(InvalidContributionNote):
        await service.upsert_contribution(
            ContributionInput(
                id=uuid4(),
                goal_id=uuid4(),
                kind=ContributionKind.deposit,
                amount=Decimal("10"),
                currency=Currency.usd,
                contributed_on=date(2026, 11, 23),
                note="x" * 501,
            )
        )


async def test_upsert_contribution_raises_contribution_deleted() -> None:
    service, _, _, _, _ = _make_service()
    goal_id = uuid4()
    contrib_id = uuid4()
    await service.upsert_goal(GoalInput(id=goal_id, name="Fund", target_usd=Decimal("1000")))
    await service.upsert_contribution(
        ContributionInput(
            id=contrib_id,
            goal_id=goal_id,
            kind=ContributionKind.deposit,
            amount=Decimal("10"),
            currency=Currency.usd,
            contributed_on=date(2026, 11, 23),
        )
    )
    await service.delete_contribution(goal_id, contrib_id)

    with pytest.raises(ContributionDeleted):
        await service.upsert_contribution(
            ContributionInput(
                id=contrib_id,
                goal_id=goal_id,
                kind=ContributionKind.deposit,
                amount=Decimal("10"),
                currency=Currency.usd,
                contributed_on=date(2026, 11, 23),
            )
        )


async def test_upsert_contribution_raises_goal_not_found_when_missing_or_deleted() -> None:
    service, _, _, _, _ = _make_service()
    deleted_goal_id = uuid4()
    await service.upsert_goal(GoalInput(id=deleted_goal_id, name="Gone", target_usd=Decimal("100")))
    await service.delete_goal(deleted_goal_id)

    with pytest.raises(GoalNotFound):
        await service.upsert_contribution(
            ContributionInput(
                id=uuid4(),
                goal_id=uuid4(),
                kind=ContributionKind.deposit,
                amount=Decimal("10"),
                currency=Currency.usd,
                contributed_on=date(2026, 11, 23),
            )
        )

    with pytest.raises(GoalNotFound):
        await service.upsert_contribution(
            ContributionInput(
                id=uuid4(),
                goal_id=deleted_goal_id,
                kind=ContributionKind.deposit,
                amount=Decimal("10"),
                currency=Currency.usd,
                contributed_on=date(2026, 11, 23),
            )
        )


async def test_upsert_contribution_blocks_create_on_archived_goal() -> None:
    service, _, _, _, _ = _make_service()
    archived_goal = uuid4()
    await service.upsert_goal(
        GoalInput(id=archived_goal, name="Archived", target_usd=Decimal("500"))
    )
    await service.archive_goal(archived_goal)

    with pytest.raises(GoalArchived):
        await service.upsert_contribution(
            ContributionInput(
                id=uuid4(),
                goal_id=archived_goal,
                kind=ContributionKind.deposit,
                amount=Decimal("10"),
                currency=Currency.usd,
                contributed_on=date(2026, 11, 23),
            )
        )


async def test_upsert_contribution_blocks_move_to_archived_goal() -> None:
    service, _, _, _, _ = _make_service()
    active_goal = uuid4()
    archived_goal = uuid4()
    movable_contrib = uuid4()
    await service.upsert_goal(GoalInput(id=active_goal, name="Active", target_usd=Decimal("500")))
    await service.upsert_goal(
        GoalInput(id=archived_goal, name="Archived", target_usd=Decimal("500"))
    )
    await service.upsert_contribution(
        ContributionInput(
            id=movable_contrib,
            goal_id=active_goal,
            kind=ContributionKind.deposit,
            amount=Decimal("25"),
            currency=Currency.usd,
            contributed_on=date(2026, 11, 20),
        )
    )
    await service.archive_goal(archived_goal)

    with pytest.raises(GoalArchived):
        await service.upsert_contribution(
            ContributionInput(
                id=movable_contrib,
                goal_id=archived_goal,
                kind=ContributionKind.deposit,
                amount=Decimal("25"),
                currency=Currency.usd,
                contributed_on=date(2026, 11, 20),
            )
        )


async def test_upsert_contribution_allows_editing_existing_on_archived_goal() -> None:
    service, _, _, _, _ = _make_service()
    archived_goal = uuid4()
    existing_contrib = uuid4()
    await service.upsert_goal(
        GoalInput(id=archived_goal, name="Archived", target_usd=Decimal("500"))
    )
    await service.upsert_contribution(
        ContributionInput(
            id=existing_contrib,
            goal_id=archived_goal,
            kind=ContributionKind.deposit,
            amount=Decimal("50"),
            currency=Currency.usd,
            contributed_on=date(2026, 11, 20),
        )
    )
    await service.archive_goal(archived_goal)

    edited = await service.upsert_contribution(
        ContributionInput(
            id=existing_contrib,
            goal_id=archived_goal,
            kind=ContributionKind.deposit,
            amount=Decimal("75"),
            currency=Currency.usd,
            contributed_on=date(2026, 11, 20),
        )
    )
    assert edited.contribution.amount == Decimal("75.00")


async def test_upsert_contribution_raises_rate_not_available_when_bcv_missing() -> None:
    _, usdt_only = _default_rates()
    service, _, _, _, _ = _make_service(bcv_rates={}, usdt_rates=usdt_only)
    goal_id = uuid4()
    await service.upsert_goal(GoalInput(id=goal_id, name="G1", target_usd=Decimal("100")))

    with pytest.raises(RateNotAvailableForDate):
        await service.upsert_contribution(
            ContributionInput(
                id=uuid4(),
                goal_id=goal_id,
                kind=ContributionKind.deposit,
                amount=Decimal("10"),
                currency=Currency.usd,
                contributed_on=date(2026, 11, 23),
            )
        )


async def test_upsert_contribution_raises_rate_not_available_when_usdt_missing() -> None:
    bcv_only, _ = _default_rates()
    service, _, _, _, _ = _make_service(bcv_rates=bcv_only, usdt_rates={})
    goal_id = uuid4()
    await service.upsert_goal(GoalInput(id=goal_id, name="G2", target_usd=Decimal("100")))

    with pytest.raises(RateNotAvailableForDate):
        await service.upsert_contribution(
            ContributionInput(
                id=uuid4(),
                goal_id=goal_id,
                kind=ContributionKind.deposit,
                amount=Decimal("10"),
                currency=Currency.usd,
                contributed_on=date(2026, 11, 23),
            )
        )


# Rules 5, 6, 7: negative balance, delete_contribution, list_contributions


async def test_delete_contribution_soft_deletes_idempotently() -> None:
    service, _, contrib_repo, _, clock = _make_service()
    goal_id = uuid4()
    contrib_id = uuid4()
    await service.upsert_goal(GoalInput(id=goal_id, name="Fund", target_usd=Decimal("100")))
    await service.upsert_contribution(
        ContributionInput(
            id=contrib_id,
            goal_id=goal_id,
            kind=ContributionKind.deposit,
            amount=Decimal("10"),
            currency=Currency.usd,
            contributed_on=date(2026, 11, 23),
        )
    )

    clock.current += timedelta(minutes=5)
    deleted = await service.delete_contribution(goal_id, contrib_id)
    assert deleted.deleted_at == clock.current
    updates = contrib_repo.update_calls

    clock.current += timedelta(minutes=5)
    retry = await service.delete_contribution(goal_id, contrib_id)
    assert retry.deleted_at == deleted.deleted_at
    assert contrib_repo.update_calls == updates


async def test_delete_contribution_raises_contribution_not_found() -> None:
    service, _, _, _, _ = _make_service()
    g1 = uuid4()
    g2 = uuid4()
    c1 = uuid4()
    await service.upsert_goal(GoalInput(id=g1, name="G1", target_usd=Decimal("100")))
    await service.upsert_goal(GoalInput(id=g2, name="G2", target_usd=Decimal("100")))
    await service.upsert_contribution(
        ContributionInput(
            id=c1,
            goal_id=g1,
            kind=ContributionKind.deposit,
            amount=Decimal("10"),
            currency=Currency.usd,
            contributed_on=date(2026, 11, 23),
        )
    )

    with pytest.raises(ContributionNotFound):
        await service.delete_contribution(g1, uuid4())
    with pytest.raises(ContributionNotFound):
        await service.delete_contribution(g2, c1)


async def test_list_contributions_orders_by_date_desc_created_at_desc_id_asc() -> None:
    goal_id = uuid4()
    id_low = UUID("00000000-0000-0000-0000-000000000001")
    id_high = UUID("00000000-0000-0000-0000-000000000002")
    t_old = datetime(2026, 11, 20, 10, 0, tzinfo=UTC)
    t_same = datetime(2026, 11, 23, 10, 0, tzinfo=UTC)
    t_new = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
    goal = Goal(
        id=goal_id,
        name="Fund",
        target_usd=Decimal("100"),
        target_date=None,
        note=None,
        archived_at=None,
        deleted_at=None,
        created_at=t_old,
        updated_at=t_old,
    )
    contribs = [
        GoalContribution(
            id=id_high,
            goal_id=goal_id,
            kind=ContributionKind.deposit,
            amount=Decimal("1"),
            currency=Currency.usd,
            contributed_on=date(2026, 11, 23),
            note=None,
            bcv_rate_value=Decimal("900"),
            bcv_rate_date=date(2026, 11, 23),
            usdt_rate_value=Decimal("950"),
            usdt_rate_date=date(2026, 11, 23),
            deleted_at=None,
            created_at=t_same,
            updated_at=t_same,
        ),
        GoalContribution(
            id=id_low,
            goal_id=goal_id,
            kind=ContributionKind.deposit,
            amount=Decimal("2"),
            currency=Currency.usd,
            contributed_on=date(2026, 11, 23),
            note=None,
            bcv_rate_value=Decimal("900"),
            bcv_rate_date=date(2026, 11, 23),
            usdt_rate_value=Decimal("950"),
            usdt_rate_date=date(2026, 11, 23),
            deleted_at=None,
            created_at=t_same,
            updated_at=t_same,
        ),
        GoalContribution(
            id=uuid4(),
            goal_id=goal_id,
            kind=ContributionKind.deposit,
            amount=Decimal("3"),
            currency=Currency.usd,
            contributed_on=date(2026, 11, 23),
            note=None,
            bcv_rate_value=Decimal("900"),
            bcv_rate_date=date(2026, 11, 23),
            usdt_rate_value=Decimal("950"),
            usdt_rate_date=date(2026, 11, 23),
            deleted_at=None,
            created_at=t_new,
            updated_at=t_new,
        ),
        GoalContribution(
            id=uuid4(),
            goal_id=goal_id,
            kind=ContributionKind.deposit,
            amount=Decimal("4"),
            currency=Currency.usd,
            contributed_on=date(2026, 11, 20),
            note=None,
            bcv_rate_value=Decimal("800"),
            bcv_rate_date=date(2026, 11, 20),
            usdt_rate_value=Decimal("850"),
            usdt_rate_date=date(2026, 11, 20),
            deleted_at=None,
            created_at=t_old,
            updated_at=t_old,
        ),
    ]
    service, _, _, _, _ = _make_service(goals=[goal], contributions=contribs)

    listed = await service.list_contributions(goal_id)
    assert [c.amount for c in listed] == [Decimal("3"), Decimal("2"), Decimal("1"), Decimal("4")]


# Rules 8, 9, 10: progress


async def test_progress_mixed_currencies_withdrawals_and_today_bs_equivalents() -> None:
    service, _, _, _, _ = _make_service()
    goal_id = uuid4()
    await service.upsert_goal(GoalInput(id=goal_id, name="Bike", target_usd=Decimal("100.00")))
    # +50 USD on Nov 20
    await service.upsert_contribution(
        ContributionInput(
            id=uuid4(),
            goal_id=goal_id,
            kind=ContributionKind.deposit,
            amount=Decimal("50.00"),
            currency=Currency.usd,
            contributed_on=date(2026, 11, 20),
        )
    )
    # +16000 VES on Nov 20 at BCV 800 = +20.00 USD
    await service.upsert_contribution(
        ContributionInput(
            id=uuid4(),
            goal_id=goal_id,
            kind=ContributionKind.deposit,
            amount=Decimal("16000.00"),
            currency=Currency.ves,
            contributed_on=date(2026, 11, 20),
        )
    )
    # -9000 VES on Nov 23 at BCV 900 = -10.00 USD
    await service.upsert_contribution(
        ContributionInput(
            id=uuid4(),
            goal_id=goal_id,
            kind=ContributionKind.withdrawal,
            amount=Decimal("9000.00"),
            currency=Currency.ves,
            contributed_on=date(2026, 11, 23),
        )
    )

    fetched = await service.get_goal(goal_id)
    prog = fetched.progress
    assert prog.saved_usd == Decimal("60.00")
    assert prog.remaining_usd == Decimal("40.00")
    assert prog.percent == Decimal("60.0")
    assert prog.reached is False
    # Today (Nov 23) rates: BCV=900, USDT=950
    assert prog.saved_ves_bcv == Decimal("54000.00")
    assert prog.saved_ves_usdt == Decimal("57000.00")
    assert prog.remaining_ves_bcv == Decimal("36000.00")
    assert prog.remaining_ves_usdt == Decimal("38000.00")
    assert prog.bcv_rate == RateSnapshot(
        value=Decimal("900.00000000"), value_date=date(2026, 11, 23)
    )
    assert prog.usdt_rate == RateSnapshot(
        value=Decimal("950.00000000"), value_date=date(2026, 11, 23)
    )


async def test_progress_allows_negative_balance() -> None:
    service, _, _, _, _ = _make_service()
    goal_id = uuid4()
    await service.upsert_goal(GoalInput(id=goal_id, name="Fund", target_usd=Decimal("100.00")))
    await service.upsert_contribution(
        ContributionInput(
            id=uuid4(),
            goal_id=goal_id,
            kind=ContributionKind.withdrawal,
            amount=Decimal("25.00"),
            currency=Currency.usd,
            contributed_on=date(2026, 11, 23),
        )
    )

    prog = (await service.get_goal(goal_id)).progress
    assert prog.saved_usd == Decimal("-25.00")
    assert prog.remaining_usd == Decimal("125.00")
    assert prog.percent == Decimal("-25.0")
    assert prog.reached is False


async def test_progress_percent_exceeds_100_and_remaining_clamps_to_zero() -> None:
    service, _, _, _, _ = _make_service()
    goal_id = uuid4()
    await service.upsert_goal(GoalInput(id=goal_id, name="Fund", target_usd=Decimal("100.00")))
    await service.upsert_contribution(
        ContributionInput(
            id=uuid4(),
            goal_id=goal_id,
            kind=ContributionKind.deposit,
            amount=Decimal("150.00"),
            currency=Currency.usd,
            contributed_on=date(2026, 11, 23),
        )
    )

    prog = (await service.get_goal(goal_id)).progress
    assert prog.saved_usd == Decimal("150.00")
    assert prog.remaining_usd == Decimal("0.00")
    assert prog.percent == Decimal("150.0")
    assert prog.reached is True


async def test_progress_sets_bs_fields_null_when_today_rate_missing() -> None:
    past = date(2026, 11, 20)
    bcv = {past: RateSnapshot(value=Decimal("800.00000000"), value_date=past)}
    # Empty USDT rates -> usdt_in_force(today) is None!
    service, _, _, _, _ = _make_service(bcv_rates=bcv, usdt_rates={})
    goal_id = uuid4()
    created = await service.upsert_goal(
        GoalInput(id=goal_id, name="Fund", target_usd=Decimal("100.00"))
    )

    prog = created.item.progress
    assert prog.saved_usd == Decimal("0.00")
    assert prog.remaining_usd == Decimal("100.00")
    assert prog.saved_ves_bcv is None
    assert prog.saved_ves_usdt is None
    assert prog.remaining_ves_bcv is None
    assert prog.remaining_ves_usdt is None
    assert prog.bcv_rate is None
    assert prog.usdt_rate is None
