from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.income.domain import (
    Currency,
    IncomeCollaborators,
    IncomeOccurrence,
    IncomeRule,
    IncomeRuleInput,
    OccurrenceStatus,
    OccurrenceWithRule,
    RateSnapshot,
)
from app.income.errors import (
    IncomeRuleEnded,
    IncomeRuleNotFound,
    InvalidIncomeAmount,
    InvalidIncomeDays,
    InvalidIncomeName,
    InvalidIncomePeriod,
    OccurrenceNotFound,
    OccurrenceNotReceived,
    RateNotAvailableForDate,
)
from app.income.service import IncomeService


class _FakeClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def now(self) -> datetime:
        return self.current


class _InMemoryRuleRepository:
    def __init__(self, initial: list[IncomeRule] | None = None) -> None:
        self.items: dict[UUID, IncomeRule] = {rule.id: rule for rule in initial or []}
        self.insert_calls = 0
        self.update_calls = 0
        self.simulate_concurrent_existing = False

    async def get_by_id(self, rule_id: UUID) -> IncomeRule | None:
        return self.items.get(rule_id)

    async def list_all(self) -> list[IncomeRule]:
        return sorted(
            self.items.values(),
            key=lambda rule: (rule.ended_at is not None, rule.name, rule.id),
        )

    async def list_active(self, today: date) -> list[IncomeRule]:
        return [
            rule
            for rule in self.items.values()
            if rule.ended_at is None and (rule.end_date is None or rule.end_date >= today)
        ]

    async def insert_if_absent(self, rule: IncomeRule) -> tuple[IncomeRule, bool]:
        self.insert_calls += 1
        if self.simulate_concurrent_existing:
            self.simulate_concurrent_existing = False
            self.items[rule.id] = rule
            return rule, False
        if rule.id in self.items:
            return self.items[rule.id], False
        self.items[rule.id] = rule
        return rule, True

    async def update(self, rule: IncomeRule) -> IncomeRule:
        self.update_calls += 1
        self.items[rule.id] = rule
        return rule


class _InMemoryOccurrenceRepository:
    def __init__(
        self,
        rules_repo: _InMemoryRuleRepository,
        initial: list[IncomeOccurrence] | None = None,
    ) -> None:
        self._rules_repo = rules_repo
        self.items: dict[UUID, IncomeOccurrence] = {occ.id: occ for occ in initial or []}
        self.update_calls = 0
        self.delete_calls = 0

    async def get_by_id(self, occurrence_id: UUID) -> IncomeOccurrence | None:
        return self.items.get(occurrence_id)

    async def list_dates_for_rule(self, rule_id: UUID) -> set[date]:
        return {occ.expected_date for occ in self.items.values() if occ.rule_id == rule_id}

    async def add_many_ignore_conflicts(
        self,
        occurrences: Sequence[IncomeOccurrence],
    ) -> int:
        existing_pairs = {(occ.rule_id, occ.expected_date) for occ in self.items.values()}
        inserted = 0
        for occ in occurrences:
            key = (occ.rule_id, occ.expected_date)
            if key in existing_pairs:
                continue
            existing_pairs.add(key)
            self.items[occ.id] = occ
            inserted += 1
        return inserted

    async def delete_future_expected(self, rule_id: UUID, from_date: date) -> None:
        self.delete_calls += 1
        to_delete = [
            occ_id
            for occ_id, occ in self.items.items()
            if occ.rule_id == rule_id
            and occ.status == OccurrenceStatus.expected
            and occ.expected_date >= from_date
        ]
        for occ_id in to_delete:
            del self.items[occ_id]

    async def update(self, occurrence: IncomeOccurrence) -> IncomeOccurrence:
        self.update_calls += 1
        self.items[occurrence.id] = occurrence
        return occurrence

    async def list_in_range(
        self,
        from_date: date,
        to_date: date,
        status: OccurrenceStatus | None = None,
    ) -> list[OccurrenceWithRule]:
        matched = [
            OccurrenceWithRule(
                occurrence=occ,
                rule_name=self._rules_repo.items[occ.rule_id].name,
            )
            for occ in self.items.values()
            if from_date <= occ.expected_date <= to_date
            and (status is None or occ.status == status)
        ]
        return sorted(
            matched,
            key=lambda item: (item.occurrence.expected_date, item.rule_name, item.occurrence.id),
        )


class _FakeRateLookup:
    def __init__(self, rates_by_date: dict[date, RateSnapshot] | None = None) -> None:
        self.rates_by_date = rates_by_date or {}

    async def bcv_in_force(self, on: date) -> RateSnapshot | None:
        eligible = [d for d in self.rates_by_date if d <= on]
        if not eligible:
            return None
        return self.rates_by_date[max(eligible)]


def _make_service(
    *,
    now: datetime = datetime(2026, 11, 22, 14, 0, tzinfo=UTC),
    rules: list[IncomeRule] | None = None,
    occurrences: list[IncomeOccurrence] | None = None,
    rates: dict[date, RateSnapshot] | None = None,
    horizon_months: int = 3,
) -> tuple[
    IncomeService,
    _InMemoryRuleRepository,
    _InMemoryOccurrenceRepository,
    _FakeClock,
]:
    clock = _FakeClock(now)
    rules_repo = _InMemoryRuleRepository(rules)
    occurrences_repo = _InMemoryOccurrenceRepository(rules_repo, occurrences)
    rate_lookup = _FakeRateLookup(rates)
    service = IncomeService(
        collaborators=IncomeCollaborators(
            rules=rules_repo,
            occurrences=occurrences_repo,
            rates=rate_lookup,
            clock=clock,
        ),
        horizon_months=horizon_months,
    )
    return service, rules_repo, occurrences_repo, clock


async def test_upsert_rule_creates_new_rule_and_generates_occurrences_to_horizon() -> None:
    # 2026-11-22 14:00 UTC -> 2026-11-22 Caracas; horizon +3 months -> 2027-02-22.
    service, _, occ_repo, _ = _make_service()
    rule_id = uuid4()

    result = await service.upsert_rule(
        IncomeRuleInput(
            id=rule_id,
            name="  Salary  ",
            amount=Decimal("1250.555"),
            currency=Currency.usd,
            days=[30, 15, 30],
            start_date=date(2026, 11, 10),
        )
    )

    assert result.created is True
    assert result.rule.id == rule_id
    assert result.rule.name == "Salary"
    assert result.rule.amount == Decimal("1250.56")
    assert result.rule.days == (15, 30)
    assert result.rule.ended_at is None

    dates = sorted(occ.expected_date for occ in occ_repo.items.values())
    assert dates == [
        date(2026, 11, 15),
        date(2026, 11, 30),
        date(2026, 12, 15),
        date(2026, 12, 30),
        date(2027, 1, 15),
        date(2027, 1, 30),
        date(2027, 2, 15),
    ]
    assert all(occ.expected_amount == Decimal("1250.56") for occ in occ_repo.items.values())


async def test_upsert_rule_with_end_date_clamps_generation_to_end_date() -> None:
    service, _, occ_repo, _ = _make_service()
    result = await service.upsert_rule(
        IncomeRuleInput(
            id=uuid4(),
            name="Bonus",
            amount=Decimal("200"),
            currency=Currency.usd,
            days=(15,),
            start_date=date(2026, 11, 1),
            end_date=date(2026, 12, 20),
        )
    )

    assert result.created is True
    dates = sorted(occ.expected_date for occ in occ_repo.items.values())
    assert dates == [date(2026, 11, 15), date(2026, 12, 15)]


async def test_upsert_rule_identical_normalized_fields_is_noop() -> None:
    service, rules_repo, occ_repo, clock = _make_service()
    rule_id = uuid4()
    first = await service.upsert_rule(
        IncomeRuleInput(
            id=rule_id,
            name="Salary",
            amount=Decimal("500.00"),
            currency=Currency.usd,
            days=(15, 30),
            start_date=date(2026, 11, 1),
        )
    )
    initial_occ_ids = set(occ_repo.items)
    clock.current = datetime(2026, 11, 22, 18, 0, tzinfo=UTC)

    second = await service.upsert_rule(
        IncomeRuleInput(
            id=rule_id,
            name="  Salary ",
            amount=Decimal("500"),
            currency=Currency.usd,
            days=[30, 15, 30],
            start_date=date(2026, 11, 1),
        )
    )

    assert second.created is False
    assert second.rule == first.rule
    assert rules_repo.update_calls == 0
    assert occ_repo.delete_calls == 0
    assert set(occ_repo.items) == initial_occ_ids


async def test_upsert_rule_concurrent_insert_conflict_returns_existing_not_created() -> None:
    service, rules_repo, _, _ = _make_service()
    rules_repo.simulate_concurrent_existing = True

    result = await service.upsert_rule(
        IncomeRuleInput(
            id=uuid4(),
            name="Salary",
            amount=Decimal("500.00"),
            currency=Currency.usd,
            days=(15,),
            start_date=date(2026, 11, 1),
        )
    )

    assert result.created is False
    assert rules_repo.update_calls == 0


async def test_upsert_rule_update_regenerates_only_future_expected_and_preserves_received() -> None:
    rule_id = uuid4()
    created_at = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    existing_rule = IncomeRule(
        id=rule_id,
        name="Salary",
        amount=Decimal("500.00"),
        currency=Currency.usd,
        days=(15, 30),
        start_date=date(2026, 11, 1),
        end_date=None,
        ended_at=None,
        created_at=created_at,
        updated_at=created_at,
    )
    past_expected = IncomeOccurrence(
        id=uuid4(),
        rule_id=rule_id,
        expected_date=date(2026, 11, 15),
        expected_amount=Decimal("500.00"),
        currency=Currency.usd,
        status=OccurrenceStatus.expected,
        received_date=None,
        received_amount=None,
        rate_value=None,
        rate_date=None,
        received_at=None,
        updated_at=created_at,
    )
    future_received = IncomeOccurrence(
        id=uuid4(),
        rule_id=rule_id,
        expected_date=date(2026, 11, 30),
        expected_amount=Decimal("500.00"),
        currency=Currency.usd,
        status=OccurrenceStatus.received,
        received_date=date(2026, 11, 22),
        received_amount=Decimal("500.00"),
        rate_value=Decimal("920.00000000"),
        rate_date=date(2026, 11, 22),
        received_at=created_at,
        updated_at=created_at,
    )
    future_expected = IncomeOccurrence(
        id=uuid4(),
        rule_id=rule_id,
        expected_date=date(2026, 12, 15),
        expected_amount=Decimal("500.00"),
        currency=Currency.usd,
        status=OccurrenceStatus.expected,
        received_date=None,
        received_amount=None,
        rate_value=None,
        rate_date=None,
        received_at=None,
        updated_at=created_at,
    )
    service, _, occ_repo, _ = _make_service(
        now=datetime(2026, 11, 22, 14, 0, tzinfo=UTC),
        rules=[existing_rule],
        occurrences=[past_expected, future_received, future_expected],
    )

    result = await service.upsert_rule(
        IncomeRuleInput(
            id=rule_id,
            name="Salary New",
            amount=Decimal("600.00"),
            currency=Currency.usd,
            days=(10, 30),
            start_date=date(2026, 11, 1),
        )
    )

    assert result.created is False
    assert result.rule.name == "Salary New"
    assert result.rule.amount == Decimal("600.00")
    assert result.rule.created_at == created_at
    assert result.rule.updated_at == datetime(2026, 11, 22, 14, 0, tzinfo=UTC)

    assert occ_repo.items[past_expected.id] == past_expected
    assert occ_repo.items[future_received.id] == future_received
    assert future_expected.id not in occ_repo.items

    dates = sorted(occ.expected_date for occ in occ_repo.items.values())
    assert dates == [
        date(2026, 11, 15),
        date(2026, 11, 30),
        date(2026, 12, 10),
        date(2026, 12, 30),
        date(2027, 1, 10),
        date(2027, 1, 30),
        date(2027, 2, 10),
    ]
    dec_10 = next(occ for occ in occ_repo.items.values() if occ.expected_date == date(2026, 12, 10))
    assert dec_10.expected_amount == Decimal("600.00")


async def test_upsert_rule_raises_when_rule_already_ended() -> None:
    now = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    ended_rule = IncomeRule(
        id=uuid4(),
        name="Old Job",
        amount=Decimal("500.00"),
        currency=Currency.usd,
        days=(15,),
        start_date=date(2026, 10, 1),
        end_date=None,
        ended_at=now,
        created_at=now,
        updated_at=now,
    )
    service, _, _, _ = _make_service(rules=[ended_rule])

    with pytest.raises(IncomeRuleEnded):
        await service.upsert_rule(
            IncomeRuleInput(
                id=ended_rule.id,
                name="Updated Job",
                amount=Decimal("700.00"),
                currency=Currency.usd,
                days=(15,),
                start_date=date(2026, 10, 1),
            )
        )


async def test_end_rule_sets_ended_at_keeps_end_date_and_deletes_future_expected() -> None:
    created_at = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
    rule_id = uuid4()
    rule = IncomeRule(
        id=rule_id,
        name="Freelance",
        amount=Decimal("300.00"),
        currency=Currency.usd,
        days=(10, 25),
        start_date=date(2026, 11, 1),
        end_date=date(2027, 6, 1),
        ended_at=None,
        created_at=created_at,
        updated_at=created_at,
    )
    past_expected = IncomeOccurrence(
        id=uuid4(),
        rule_id=rule_id,
        expected_date=date(2026, 11, 10),
        expected_amount=Decimal("300.00"),
        currency=Currency.usd,
        status=OccurrenceStatus.expected,
        received_date=None,
        received_amount=None,
        rate_value=None,
        rate_date=None,
        received_at=None,
        updated_at=created_at,
    )
    future_expected = IncomeOccurrence(
        id=uuid4(),
        rule_id=rule_id,
        expected_date=date(2026, 11, 25),
        expected_amount=Decimal("300.00"),
        currency=Currency.usd,
        status=OccurrenceStatus.expected,
        received_date=None,
        received_amount=None,
        rate_value=None,
        rate_date=None,
        received_at=None,
        updated_at=created_at,
    )
    future_received = replace(
        future_expected,
        id=uuid4(),
        expected_date=date(2026, 12, 10),
        status=OccurrenceStatus.received,
        received_date=date(2026, 11, 20),
        received_amount=Decimal("300.00"),
        rate_value=Decimal("920.00000000"),
        rate_date=date(2026, 11, 20),
        received_at=created_at,
    )
    end_time = datetime(2026, 11, 22, 15, 0, tzinfo=UTC)
    service, rules_repo, occ_repo, clock = _make_service(
        now=end_time,
        rules=[rule],
        occurrences=[past_expected, future_expected, future_received],
    )

    ended = await service.end_rule(rule_id)

    assert ended.ended_at == end_time
    assert ended.updated_at == end_time
    assert ended.end_date == date(2027, 6, 1)
    assert past_expected.id in occ_repo.items
    assert future_expected.id not in occ_repo.items
    assert future_received.id in occ_repo.items

    # Ending again returns unchanged without touching timestamps or DB
    clock.current = datetime(2026, 11, 25, 15, 0, tzinfo=UTC)
    ended_again = await service.end_rule(rule_id)
    assert ended_again == ended
    assert rules_repo.update_calls == 1


async def test_end_rule_before_start_date_removes_all_future_expected() -> None:
    rule_id = uuid4()
    created_at = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
    rule = IncomeRule(
        id=rule_id,
        name="Future Job",
        amount=Decimal("400.00"),
        currency=Currency.usd,
        days=(15,),
        start_date=date(2026, 12, 1),
        end_date=None,
        ended_at=None,
        created_at=created_at,
        updated_at=created_at,
    )
    occ = IncomeOccurrence(
        id=uuid4(),
        rule_id=rule_id,
        expected_date=date(2026, 12, 15),
        expected_amount=Decimal("400.00"),
        currency=Currency.usd,
        status=OccurrenceStatus.expected,
        received_date=None,
        received_amount=None,
        rate_value=None,
        rate_date=None,
        received_at=None,
        updated_at=created_at,
    )
    service, _, occ_repo, _ = _make_service(
        now=datetime(2026, 11, 22, 15, 0, tzinfo=UTC),
        rules=[rule],
        occurrences=[occ],
    )

    await service.end_rule(rule_id)
    assert occ_repo.items == {}


async def test_end_rule_missing_raises_not_found() -> None:
    service, _, _, _ = _make_service()
    with pytest.raises(IncomeRuleNotFound):
        await service.end_rule(uuid4())


async def test_generate_occurrences_extends_active_rules_idempotently() -> None:
    created_at = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    active_rule = IncomeRule(
        id=uuid4(),
        name="Salary",
        amount=Decimal("500.00"),
        currency=Currency.usd,
        days=(15,),
        start_date=date(2026, 11, 1),
        end_date=None,
        ended_at=None,
        created_at=created_at,
        updated_at=created_at,
    )
    ended_rule = replace(active_rule, id=uuid4(), name="Ended", ended_at=created_at)
    expired_rule = replace(
        active_rule,
        id=uuid4(),
        name="Expired",
        end_date=date(2026, 11, 20),
    )
    existing_occ = IncomeOccurrence(
        id=uuid4(),
        rule_id=active_rule.id,
        expected_date=date(2026, 11, 15),
        expected_amount=Decimal("500.00"),
        currency=Currency.usd,
        status=OccurrenceStatus.received,
        received_date=date(2026, 11, 15),
        received_amount=Decimal("500.00"),
        rate_value=Decimal("915.00000000"),
        rate_date=date(2026, 11, 15),
        received_at=created_at,
        updated_at=created_at,
    )
    service, _, occ_repo, _ = _make_service(
        now=datetime(2026, 11, 22, 12, 0, tzinfo=UTC),
        rules=[active_rule, ended_rule, expired_rule],
        occurrences=[existing_occ],
    )

    first_count = await service.generate_occurrences()
    second_count = await service.generate_occurrences()

    assert first_count == 3  # Dec 15, Jan 15, Feb 15
    assert second_count == 0
    assert occ_repo.items[existing_occ.id] == existing_occ
    assert sorted(occ.expected_date for occ in occ_repo.items.values()) == [
        date(2026, 11, 15),
        date(2026, 12, 15),
        date(2027, 1, 15),
        date(2027, 2, 15),
    ]


async def test_receive_with_defaults_uses_expected_values_and_bcv_rate() -> None:
    created_at = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
    occ = IncomeOccurrence(
        id=uuid4(),
        rule_id=uuid4(),
        expected_date=date(2026, 11, 15),
        expected_amount=Decimal("500.00"),
        currency=Currency.ves,
        status=OccurrenceStatus.expected,
        received_date=None,
        received_amount=None,
        rate_value=None,
        rate_date=None,
        received_at=None,
        updated_at=created_at,
    )
    now = datetime(2026, 11, 22, 14, 0, tzinfo=UTC)
    service, _, _, _ = _make_service(
        now=now,
        occurrences=[occ],
        rates={
            date(2026, 11, 14): RateSnapshot(
                value=Decimal("915.50000000"),
                value_date=date(2026, 11, 14),
            ),
        },
    )

    received = await service.receive(occ.id)

    assert received.status == OccurrenceStatus.received
    assert received.received_amount == Decimal("500.00")
    assert received.received_date == date(2026, 11, 15)
    assert received.rate_value == Decimal("915.50000000")
    assert received.rate_date == date(2026, 11, 14)
    assert received.received_at == now
    assert received.updated_at == now


async def test_receive_with_explicit_values_and_correction_overwrites_fields() -> None:
    created_at = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
    occ = IncomeOccurrence(
        id=uuid4(),
        rule_id=uuid4(),
        expected_date=date(2026, 11, 15),
        expected_amount=Decimal("500.00"),
        currency=Currency.usd,
        status=OccurrenceStatus.expected,
        received_date=None,
        received_amount=None,
        rate_value=None,
        rate_date=None,
        received_at=None,
        updated_at=created_at,
    )
    service, _, _, clock = _make_service(
        now=datetime(2026, 11, 16, 14, 0, tzinfo=UTC),
        occurrences=[occ],
        rates={
            date(2026, 11, 16): RateSnapshot(
                value=Decimal("916.00000000"),
                value_date=date(2026, 11, 16),
            ),
            date(2026, 11, 18): RateSnapshot(
                value=Decimal("918.25000000"),
                value_date=date(2026, 11, 18),
            ),
        },
    )

    first = await service.receive(
        occ.id,
        received_amount=Decimal("495.125"),
        received_date=date(2026, 11, 16),
    )
    assert first.received_amount == Decimal("495.12")
    assert first.rate_value == Decimal("916.00000000")

    clock.current = datetime(2026, 11, 18, 16, 0, tzinfo=UTC)
    corrected = await service.receive(
        occ.id,
        received_amount=Decimal("510.00"),
        received_date=date(2026, 11, 18),
    )
    assert corrected.received_amount == Decimal("510.00")
    assert corrected.received_date == date(2026, 11, 18)
    assert corrected.rate_value == Decimal("918.25000000")
    assert corrected.rate_date == date(2026, 11, 18)
    assert corrected.received_at == datetime(2026, 11, 18, 16, 0, tzinfo=UTC)
    assert corrected.updated_at == datetime(2026, 11, 18, 16, 0, tzinfo=UTC)


async def test_receive_raises_when_missing_or_no_bcv_rate() -> None:
    occ = IncomeOccurrence(
        id=uuid4(),
        rule_id=uuid4(),
        expected_date=date(2026, 11, 15),
        expected_amount=Decimal("500.00"),
        currency=Currency.usd,
        status=OccurrenceStatus.expected,
        received_date=None,
        received_amount=None,
        rate_value=None,
        rate_date=None,
        received_at=None,
        updated_at=datetime(2026, 11, 1, 12, 0, tzinfo=UTC),
    )
    service, _, _, _ = _make_service(occurrences=[occ], rates={})

    with pytest.raises(OccurrenceNotFound):
        await service.receive(uuid4())

    with pytest.raises(RateNotAvailableForDate):
        await service.receive(occ.id)


async def test_unreceive_resets_received_occurrence_and_rejects_expected() -> None:
    created_at = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
    received_occ = IncomeOccurrence(
        id=uuid4(),
        rule_id=uuid4(),
        expected_date=date(2026, 11, 15),
        expected_amount=Decimal("500.00"),
        currency=Currency.usd,
        status=OccurrenceStatus.received,
        received_date=date(2026, 11, 15),
        received_amount=Decimal("500.00"),
        rate_value=Decimal("915.00000000"),
        rate_date=date(2026, 11, 15),
        received_at=created_at,
        updated_at=created_at,
    )
    now = datetime(2026, 11, 22, 16, 0, tzinfo=UTC)
    service, _, _, _ = _make_service(now=now, occurrences=[received_occ])

    reverted = await service.unreceive(received_occ.id)

    assert reverted.status == OccurrenceStatus.expected
    assert reverted.received_date is None
    assert reverted.received_amount is None
    assert reverted.rate_value is None
    assert reverted.rate_date is None
    assert reverted.received_at is None
    assert reverted.updated_at == now

    with pytest.raises(OccurrenceNotReceived):
        await service.unreceive(received_occ.id)

    with pytest.raises(OccurrenceNotFound):
        await service.unreceive(uuid4())


async def test_validation_errors_on_upsert_receive_and_list() -> None:
    service, _, _, _ = _make_service()

    with pytest.raises(InvalidIncomeName):
        await service.upsert_rule(
            IncomeRuleInput(
                id=uuid4(),
                name="   ",
                amount=Decimal("100"),
                currency=Currency.usd,
                days=(15,),
                start_date=date(2026, 11, 1),
            )
        )

    with pytest.raises(InvalidIncomeName):
        await service.upsert_rule(
            IncomeRuleInput(
                id=uuid4(),
                name="a" * 101,
                amount=Decimal("100"),
                currency=Currency.usd,
                days=(15,),
                start_date=date(2026, 11, 1),
            )
        )

    with pytest.raises(InvalidIncomeAmount):
        await service.upsert_rule(
            IncomeRuleInput(
                id=uuid4(),
                name="Salary",
                amount=Decimal("0"),
                currency=Currency.usd,
                days=(15,),
                start_date=date(2026, 11, 1),
            )
        )

    with pytest.raises(InvalidIncomeDays):
        await service.upsert_rule(
            IncomeRuleInput(
                id=uuid4(),
                name="Salary",
                amount=Decimal("100"),
                currency=Currency.usd,
                days=(),
                start_date=date(2026, 11, 1),
            )
        )

    with pytest.raises(InvalidIncomePeriod):
        await service.upsert_rule(
            IncomeRuleInput(
                id=uuid4(),
                name="Salary",
                amount=Decimal("100"),
                currency=Currency.usd,
                days=(15,),
                start_date=date(2026, 11, 10),
                end_date=date(2026, 11, 9),
            )
        )

    with pytest.raises(InvalidIncomeAmount):
        await service.receive(uuid4(), received_amount=Decimal("-5"))

    with pytest.raises(InvalidIncomePeriod):
        await service.list_occurrences(date(2026, 11, 20), date(2026, 11, 10))


async def test_generate_occurrences_does_not_backfill_past_dates_after_rule_days_change() -> None:
    created_at = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    rule_id = uuid4()
    rule = IncomeRule(
        id=rule_id,
        name="Salary",
        amount=Decimal("500.00"),
        currency=Currency.usd,
        days=(15,),
        start_date=date(2026, 9, 1),
        end_date=None,
        ended_at=None,
        created_at=created_at,
        updated_at=created_at,
    )
    past_occs = [
        IncomeOccurrence(
            id=uuid4(),
            rule_id=rule_id,
            expected_date=day,
            expected_amount=Decimal("500.00"),
            currency=Currency.usd,
            status=OccurrenceStatus.expected,
            received_date=None,
            received_amount=None,
            rate_value=None,
            rate_date=None,
            received_at=None,
            updated_at=created_at,
        )
        for day in (date(2026, 9, 15), date(2026, 10, 15), date(2026, 11, 15))
    ]
    service, _, occ_repo, _ = _make_service(
        now=datetime(2026, 11, 22, 14, 0, tzinfo=UTC),
        rules=[rule],
        occurrences=past_occs,
    )

    await service.upsert_rule(
        IncomeRuleInput(
            id=rule_id,
            name="Salary",
            amount=Decimal("500.00"),
            currency=Currency.usd,
            days=(16,),
            start_date=date(2026, 9, 1),
        )
    )
    created_on_daily_run = await service.generate_occurrences()

    assert created_on_daily_run == 0
    for past_occ in past_occs:
        assert occ_repo.items[past_occ.id] == past_occ
    all_dates = sorted(occ.expected_date for occ in occ_repo.items.values())
    assert all_dates == [
        date(2026, 9, 15),
        date(2026, 10, 15),
        date(2026, 11, 15),
        date(2026, 12, 16),
        date(2027, 1, 16),
        date(2027, 2, 16),
    ]
