from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.expenses.domain import (
    Currency,
    Expense,
    ExpenseCategory,
    ExpenseCollaborators,
    ExpenseInput,
    ExpenseSummaryItem,
    ExpenseWithCategory,
    RateSnapshot,
)
from app.expenses.errors import (
    CategoryArchived,
    CategoryNameTaken,
    CategoryNotFound,
    ExpenseDeleted,
    ExpenseNotFound,
    InvalidCategoryName,
    InvalidExpenseAmount,
    InvalidExpenseNote,
    InvalidExpensePeriod,
    RateNotAvailableForDate,
)
from app.expenses.service import ExpenseService


class _FakeClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def now(self) -> datetime:
        return self.current


class _InMemoryCategoryRepository:
    def __init__(self, initial: list[ExpenseCategory] | None = None) -> None:
        self.items: dict[UUID, ExpenseCategory] = {cat.id: cat for cat in initial or []}
        self.insert_calls = 0
        self.update_calls = 0
        self.simulate_concurrent_existing = False

    async def get_by_id(self, category_id: UUID) -> ExpenseCategory | None:
        return self.items.get(category_id)

    async def get_by_lower_name(self, name: str) -> ExpenseCategory | None:
        target = name.lower()
        for cat in self.items.values():
            if cat.name.lower() == target:
                return cat
        return None

    async def list_active(self) -> list[ExpenseCategory]:
        return sorted(
            (cat for cat in self.items.values() if cat.archived_at is None),
            key=lambda cat: (cat.name, cat.id),
        )

    async def list_all(self) -> list[ExpenseCategory]:
        return sorted(
            self.items.values(),
            key=lambda cat: (cat.archived_at is not None, cat.name, cat.id),
        )

    async def insert_if_absent(
        self,
        category: ExpenseCategory,
    ) -> tuple[ExpenseCategory, bool]:
        self.insert_calls += 1
        if self.simulate_concurrent_existing:
            self.simulate_concurrent_existing = False
            self.items[category.id] = category
            return category, False
        if category.id in self.items:
            return self.items[category.id], False
        existing_name = await self.get_by_lower_name(category.name)
        if existing_name is not None:
            raise CategoryNameTaken(f"Category name {category.name!r} is already taken.")
        self.items[category.id] = category
        return category, True

    async def update(self, category: ExpenseCategory) -> ExpenseCategory:
        self.update_calls += 1
        existing_name = await self.get_by_lower_name(category.name)
        if existing_name is not None and existing_name.id != category.id:
            raise CategoryNameTaken(f"Category name {category.name!r} is already taken.")
        self.items[category.id] = category
        return category

    async def list_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[ExpenseCategory]:
        return sorted(
            (
                c
                for c in self.items.values()
                if c.updated_at <= until and (since is None or c.updated_at > since)
            ),
            key=lambda c: (c.updated_at, c.id),
        )


class _InMemoryExpenseRepository:
    def __init__(
        self,
        categories_repo: _InMemoryCategoryRepository,
        initial: list[Expense] | None = None,
    ) -> None:
        self._categories_repo = categories_repo
        self.items: dict[UUID, Expense] = {exp.id: exp for exp in initial or []}
        self.insert_calls = 0
        self.update_calls = 0
        self.simulate_concurrent_existing = False

    async def get_by_id(self, expense_id: UUID) -> Expense | None:
        return self.items.get(expense_id)

    async def insert_if_absent(self, expense: Expense) -> tuple[Expense, bool]:
        self.insert_calls += 1
        if self.simulate_concurrent_existing:
            self.simulate_concurrent_existing = False
            self.items[expense.id] = expense
            return expense, False
        if expense.id in self.items:
            return self.items[expense.id], False
        self.items[expense.id] = expense
        return expense, True

    async def update(self, expense: Expense) -> Expense:
        self.update_calls += 1
        self.items[expense.id] = expense
        return expense

    async def list_in_range(
        self,
        from_date: date,
        to_date: date,
        category_id: UUID | None = None,
    ) -> list[ExpenseWithCategory]:
        matching = [
            exp
            for exp in self.items.values()
            if exp.deleted_at is None
            and from_date <= exp.spent_on <= to_date
            and (category_id is None or exp.category_id == category_id)
        ]
        matching.sort(
            key=lambda exp: (-exp.spent_on.toordinal(), -exp.created_at.timestamp(), exp.id)
        )
        return [
            ExpenseWithCategory(
                expense=exp,
                category_name=self._categories_repo.items[exp.category_id].name,
            )
            for exp in matching
        ]

    async def list_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[Expense]:
        return sorted(
            (
                e
                for e in self.items.values()
                if e.updated_at <= until and (since is None or e.updated_at > since)
            ),
            key=lambda e: (e.updated_at, e.id),
        )


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


def _make_service(
    *,
    now: datetime | None = None,
    categories: list[ExpenseCategory] | None = None,
    expenses: list[Expense] | None = None,
    bcv_rates: dict[date, RateSnapshot] | None = None,
    usdt_rates: dict[date, RateSnapshot] | None = None,
) -> tuple[
    ExpenseService,
    _InMemoryCategoryRepository,
    _InMemoryExpenseRepository,
    _FakeRateLookup,
    _FakeClock,
]:
    clock = _FakeClock(now or datetime(2026, 11, 22, 14, 0, tzinfo=UTC))
    cat_repo = _InMemoryCategoryRepository(categories)
    exp_repo = _InMemoryExpenseRepository(cat_repo, expenses)
    rates = _FakeRateLookup(bcv_rates=bcv_rates, usdt_rates=usdt_rates)
    service = ExpenseService(
        ExpenseCollaborators(
            categories=cat_repo,
            expenses=exp_repo,
            rates=rates,
            clock=clock,
        )
    )
    return service, cat_repo, exp_repo, rates, clock


async def test_upsert_category_creates_normalizes_and_is_idempotent() -> None:
    service, cat_repo, _, _, clock = _make_service()
    cat_id = uuid4()

    created = await service.upsert_category(cat_id, "  Groceries  ")
    assert created.created is True
    assert created.category.id == cat_id
    assert created.category.name == "Groceries"
    assert created.category.archived_at is None
    assert created.category.created_at == clock.current
    assert created.category.updated_at == clock.current

    clock.current = datetime(2026, 11, 22, 14, 5, tzinfo=UTC)
    retry = await service.upsert_category(cat_id, "Groceries ")
    assert retry.created is False
    assert retry.category == created.category
    assert cat_repo.update_calls == 0

    renamed = await service.upsert_category(cat_id, "groceries")
    assert renamed.created is False
    assert renamed.category.name == "groceries"
    assert renamed.category.updated_at == clock.current
    assert cat_repo.update_calls == 1


async def test_upsert_category_handles_concurrent_insert_and_name_conflicts() -> None:
    service, cat_repo, _, _, _ = _make_service()
    cat_id = uuid4()
    cat_repo.simulate_concurrent_existing = True

    res = await service.upsert_category(cat_id, "Food")
    assert res.created is False

    with pytest.raises(CategoryNameTaken):
        await service.upsert_category(uuid4(), "food")

    other_id = uuid4()
    await service.upsert_category(other_id, "Transport")
    with pytest.raises(CategoryNameTaken):
        await service.upsert_category(other_id, "FOOD")


@pytest.mark.parametrize("bad_name", ["", "   ", "x" * 51])
async def test_upsert_category_rejects_invalid_names(bad_name: str) -> None:
    service, _, _, _, _ = _make_service()
    with pytest.raises(InvalidCategoryName):
        await service.upsert_category(uuid4(), bad_name)


async def test_archive_unarchive_and_rename_archived_category() -> None:
    service, cat_repo, _, _, clock = _make_service()
    cat_id = uuid4()
    await service.upsert_category(cat_id, "Utilities")

    clock.current = datetime(2026, 11, 22, 15, 0, tzinfo=UTC)
    archived = await service.archive_category(cat_id)
    assert archived.archived_at == clock.current
    assert archived.updated_at == clock.current

    clock.current = datetime(2026, 11, 22, 15, 5, tzinfo=UTC)
    archived_again = await service.archive_category(cat_id)
    assert archived_again == archived
    assert cat_repo.update_calls == 1

    renamed = await service.upsert_category(cat_id, "Home Utilities")
    assert renamed.created is False
    assert renamed.category.name == "Home Utilities"
    assert renamed.category.archived_at == archived.archived_at

    clock.current = datetime(2026, 11, 22, 15, 10, tzinfo=UTC)
    unarchived = await service.unarchive_category(cat_id)
    assert unarchived.archived_at is None
    assert unarchived.updated_at == clock.current

    clock.current = datetime(2026, 11, 22, 15, 15, tzinfo=UTC)
    unarchived_again = await service.unarchive_category(cat_id)
    assert unarchived_again == unarchived
    assert cat_repo.update_calls == 3

    with pytest.raises(CategoryNotFound):
        await service.archive_category(uuid4())
    with pytest.raises(CategoryNotFound):
        await service.unarchive_category(uuid4())


async def test_list_categories_orders_active_first_then_archived_by_name_and_id() -> None:
    now = datetime(2026, 11, 22, 14, 0, tzinfo=UTC)
    cat_z = ExpenseCategory(uuid4(), "Zebra", None, now, now)
    cat_a_archived = ExpenseCategory(uuid4(), "Alpha", now, now, now)
    cat_b = ExpenseCategory(uuid4(), "Beta", None, now, now)
    service, _, _, _, _ = _make_service(categories=[cat_z, cat_a_archived, cat_b])

    assert await service.list_active_categories() == [cat_b, cat_z]
    assert await service.list_all_categories() == [cat_b, cat_z, cat_a_archived]


async def test_upsert_expense_creates_stores_rates_and_normalizes_retry() -> None:
    now = datetime(2026, 11, 22, 14, 0, tzinfo=UTC)
    cat = ExpenseCategory(uuid4(), "Food", None, now, now)
    spent_day = date(2026, 11, 20)
    bcv_rate = RateSnapshot(Decimal("917.77510000"), date(2026, 11, 20))
    usdt_rate = RateSnapshot(Decimal("952.50000000"), date(2026, 11, 20))
    service, _, exp_repo, rates, clock = _make_service(
        now=now,
        categories=[cat],
        bcv_rates={spent_day: bcv_rate},
        usdt_rates={spent_day: usdt_rate},
    )
    exp_id = uuid4()

    created = await service.upsert_expense(
        ExpenseInput(
            id=exp_id,
            category_id=cat.id,
            amount=Decimal("12.345"),
            currency=Currency.usd,
            spent_on=spent_day,
            note="  Lunch  ",
        )
    )
    assert created.created is True
    assert created.item.category_name == "Food"
    assert created.item.expense.amount == Decimal("12.34")
    assert created.item.expense.note == "Lunch"
    assert created.item.expense.bcv_rate_value == bcv_rate.value
    assert created.item.expense.bcv_rate_date == bcv_rate.value_date
    assert created.item.expense.usdt_rate_value == usdt_rate.value
    assert created.item.expense.usdt_rate_date == usdt_rate.value_date
    assert created.item.expense.deleted_at is None
    assert len(rates.bcv_calls) == 1
    assert len(rates.usdt_calls) == 1

    # Identical after normalization -> unchanged, even without new rate lookups
    clock.current = datetime(2026, 11, 22, 14, 5, tzinfo=UTC)
    retry = await service.upsert_expense(
        ExpenseInput(
            id=exp_id,
            category_id=cat.id,
            amount=Decimal("12.34"),
            currency=Currency.usd,
            spent_on=spent_day,
            note="Lunch",
        )
    )
    assert retry.created is False
    assert retry.item == created.item
    assert exp_repo.update_calls == 0
    assert len(rates.bcv_calls) == 1

    # Concurrent insert race -> treated as existing row
    concurrent_id = uuid4()
    exp_repo.simulate_concurrent_existing = True
    concurrent = await service.upsert_expense(
        ExpenseInput(
            id=concurrent_id,
            category_id=cat.id,
            amount=Decimal("5.00"),
            currency=Currency.usd,
            spent_on=spent_day,
            note=None,
        )
    )
    assert concurrent.created is False


async def test_upsert_expense_keeps_rates_when_spent_on_unchanged_and_refreshes_on_change() -> None:
    now = datetime(2026, 11, 22, 14, 0, tzinfo=UTC)
    cat = ExpenseCategory(uuid4(), "Food", None, now, now)
    day1 = date(2026, 11, 19)
    day2 = date(2026, 11, 21)
    service, _, _, rates, clock = _make_service(
        now=now,
        categories=[cat],
        bcv_rates={
            day1: RateSnapshot(Decimal("910.00000000"), day1),
            day2: RateSnapshot(Decimal("920.00000000"), day2),
        },
        usdt_rates={
            day1: RateSnapshot(Decimal("940.00000000"), day1),
            day2: RateSnapshot(Decimal("960.00000000"), day2),
        },
    )
    exp_id = uuid4()
    await service.upsert_expense(
        ExpenseInput(exp_id, cat.id, Decimal("10.00"), Currency.usd, day1, "   ")
    )
    assert len(rates.bcv_calls) == 1
    assert len(rates.usdt_calls) == 1

    # Change amount & note only -> rates preserved, no extra rate lookups
    clock.current = datetime(2026, 11, 22, 14, 10, tzinfo=UTC)
    edited = await service.upsert_expense(
        ExpenseInput(exp_id, cat.id, Decimal("25.00"), Currency.usd, day1, "Dinner")
    )
    assert edited.item.expense.note == "Dinner"
    assert edited.item.expense.bcv_rate_value == Decimal("910.00000000")
    assert edited.item.expense.usdt_rate_value == Decimal("940.00000000")
    assert len(rates.bcv_calls) == 1
    assert len(rates.usdt_calls) == 1

    # Change spent_on -> refreshes both rates
    clock.current = datetime(2026, 11, 22, 14, 20, tzinfo=UTC)
    moved_date = await service.upsert_expense(
        ExpenseInput(exp_id, cat.id, Decimal("25.00"), Currency.usd, day2, None)
    )
    assert moved_date.item.expense.bcv_rate_value == Decimal("920.00000000")
    assert moved_date.item.expense.bcv_rate_date == day2
    assert moved_date.item.expense.usdt_rate_value == Decimal("960.00000000")
    assert moved_date.item.expense.usdt_rate_date == day2
    assert len(rates.bcv_calls) == 2
    assert len(rates.usdt_calls) == 2


async def test_upsert_expense_missing_bcv_or_usdt_rate_raises_409() -> None:
    now = datetime(2026, 11, 22, 14, 0, tzinfo=UTC)
    cat = ExpenseCategory(uuid4(), "Food", None, now, now)
    day = date(2026, 11, 20)

    only_usdt, _, _, _, _ = _make_service(
        now=now,
        categories=[cat],
        usdt_rates={day: RateSnapshot(Decimal("950.00000000"), day)},
    )
    with pytest.raises(RateNotAvailableForDate):
        await only_usdt.upsert_expense(
            ExpenseInput(uuid4(), cat.id, Decimal("10.00"), Currency.usd, day)
        )

    only_bcv, _, _, _, _ = _make_service(
        now=now,
        categories=[cat],
        bcv_rates={day: RateSnapshot(Decimal("917.00000000"), day)},
    )
    with pytest.raises(RateNotAvailableForDate):
        await only_bcv.upsert_expense(
            ExpenseInput(uuid4(), cat.id, Decimal("10.00"), Currency.usd, day)
        )


async def test_upsert_expense_archived_category_rules() -> None:
    now = datetime(2026, 11, 22, 14, 0, tzinfo=UTC)
    day = date(2026, 11, 20)
    active_cat = ExpenseCategory(uuid4(), "Food", None, now, now)
    archived_cat = ExpenseCategory(uuid4(), "Old", now, now, now)
    service, cat_repo, _, _, _ = _make_service(
        now=now,
        categories=[active_cat, archived_cat],
        bcv_rates={day: RateSnapshot(Decimal("917.00000000"), day)},
        usdt_rates={day: RateSnapshot(Decimal("950.00000000"), day)},
    )

    # Create in archived category -> 409
    with pytest.raises(CategoryArchived):
        await service.upsert_expense(
            ExpenseInput(uuid4(), archived_cat.id, Decimal("10.00"), Currency.usd, day)
        )

    # Create in active category, then archive it
    exp_id = uuid4()
    await service.upsert_expense(
        ExpenseInput(exp_id, active_cat.id, Decimal("10.00"), Currency.usd, day)
    )
    await service.archive_category(active_cat.id)

    # Identical retry in archived category -> unchanged
    retry = await service.upsert_expense(
        ExpenseInput(exp_id, active_cat.id, Decimal("10.00"), Currency.usd, day)
    )
    assert retry.created is False

    # Edit fields while staying in the same archived category -> allowed
    edited = await service.upsert_expense(
        ExpenseInput(exp_id, active_cat.id, Decimal("15.00"), Currency.usd, day, "Updated")
    )
    assert edited.item.expense.amount == Decimal("15.00")

    # Moving to another archived category -> 409
    other_archived = ExpenseCategory(uuid4(), "Other Old", now, now, now)
    cat_repo.items[other_archived.id] = other_archived
    with pytest.raises(CategoryArchived):
        await service.upsert_expense(
            ExpenseInput(exp_id, other_archived.id, Decimal("15.00"), Currency.usd, day)
        )


async def test_upsert_expense_validation_and_deleted_and_missing_category() -> None:
    now = datetime(2026, 11, 22, 14, 0, tzinfo=UTC)
    day = date(2026, 11, 20)
    cat = ExpenseCategory(uuid4(), "Food", None, now, now)
    deleted_exp = Expense(
        id=uuid4(),
        category_id=cat.id,
        amount=Decimal("10.00"),
        currency=Currency.usd,
        spent_on=day,
        note=None,
        bcv_rate_value=Decimal("917.00000000"),
        bcv_rate_date=day,
        usdt_rate_value=Decimal("950.00000000"),
        usdt_rate_date=day,
        deleted_at=now,
        created_at=now,
        updated_at=now,
    )
    service, _, _, _, _ = _make_service(
        now=now,
        categories=[cat],
        expenses=[deleted_exp],
        bcv_rates={day: RateSnapshot(Decimal("917.00000000"), day)},
        usdt_rates={day: RateSnapshot(Decimal("950.00000000"), day)},
    )

    for bad_amount in (Decimal("0"), Decimal("-5.00"), Decimal("NaN"), Decimal("Infinity")):
        with pytest.raises(InvalidExpenseAmount):
            await service.upsert_expense(
                ExpenseInput(uuid4(), cat.id, bad_amount, Currency.usd, day)
            )

    with pytest.raises(InvalidExpenseNote):
        await service.upsert_expense(
            ExpenseInput(uuid4(), cat.id, Decimal("10.00"), Currency.usd, day, "a" * 501)
        )

    with pytest.raises(ExpenseDeleted):
        await service.upsert_expense(
            ExpenseInput(deleted_exp.id, cat.id, Decimal("10.00"), Currency.usd, day)
        )

    with pytest.raises(CategoryNotFound):
        await service.upsert_expense(
            ExpenseInput(uuid4(), uuid4(), Decimal("10.00"), Currency.usd, day)
        )


async def test_delete_get_list_and_summary_math_with_mixed_currencies_and_rounding() -> None:
    now = datetime(2026, 11, 22, 14, 0, tzinfo=UTC)
    cat_food = ExpenseCategory(uuid4(), "Food", None, now, now)
    cat_transit = ExpenseCategory(uuid4(), "Transit", None, now, now)
    cat_empty = ExpenseCategory(uuid4(), "Empty", None, now, now)
    service, _, exp_repo, _, clock = _make_service(
        now=now,
        categories=[cat_food, cat_transit, cat_empty],
        bcv_rates={date(2026, 11, 1): RateSnapshot(Decimal("100.00000000"), date(2026, 11, 1))},
        usdt_rates={date(2026, 11, 1): RateSnapshot(Decimal("110.00000000"), date(2026, 11, 1))},
    )

    # Food: 10.00 USD + 1000/300 + 1000/300 = 16.6666... -> 16.67
    # (if rounded per row: 10 + 3.33 + 3.33 = 16.66)
    e1 = Expense(
        id=uuid4(),
        category_id=cat_food.id,
        amount=Decimal("10.00"),
        currency=Currency.usd,
        spent_on=date(2026, 11, 10),
        note=None,
        bcv_rate_value=Decimal("300.00000000"),
        bcv_rate_date=date(2026, 11, 10),
        usdt_rate_value=Decimal("320.00000000"),
        usdt_rate_date=date(2026, 11, 10),
        deleted_at=None,
        created_at=now,
        updated_at=now,
    )
    e2 = replace(
        e1,
        id=uuid4(),
        amount=Decimal("1000.00"),
        currency=Currency.ves,
        spent_on=date(2026, 11, 12),
    )
    e3 = replace(
        e1,
        id=uuid4(),
        amount=Decimal("1000.00"),
        currency=Currency.ves,
        spent_on=date(2026, 11, 15),
    )
    # Transit: 20.00 USD; plus 1 deleted expense that must be excluded
    e_transit = replace(e1, id=uuid4(), category_id=cat_transit.id, amount=Decimal("20.00"))
    e_deleted = replace(
        e1,
        id=uuid4(),
        category_id=cat_transit.id,
        amount=Decimal("500.00"),
        deleted_at=now,
    )
    # Out of range expense
    e_out = replace(e1, id=uuid4(), category_id=cat_empty.id, spent_on=date(2026, 10, 31))
    for item in (e1, e2, e3, e_transit, e_deleted, e_out):
        exp_repo.items[item.id] = item

    # Delete e3 -> idempotent
    clock.current = datetime(2026, 11, 22, 16, 0, tzinfo=UTC)
    e_to_delete = replace(e1, id=uuid4())
    exp_repo.items[e_to_delete.id] = e_to_delete
    deleted = await service.delete_expense(e_to_delete.id)
    assert deleted.expense.deleted_at == clock.current
    assert deleted.expense.updated_at == clock.current

    clock.current = datetime(2026, 11, 22, 16, 5, tzinfo=UTC)
    deleted_again = await service.delete_expense(e_to_delete.id)
    assert deleted_again == deleted

    with pytest.raises(ExpenseNotFound):
        await service.get_expense(e_to_delete.id)
    with pytest.raises(ExpenseNotFound):
        await service.delete_expense(uuid4())

    listed = await service.list_expenses(date(2026, 11, 1), date(2026, 11, 30))
    assert [item.expense.id for item in listed] == [e3.id, e2.id, *sorted([e1.id, e_transit.id])]

    filtered = await service.list_expenses(
        date(2026, 11, 1),
        date(2026, 11, 30),
        category_id=cat_transit.id,
    )
    assert [item.expense.id for item in filtered] == [e_transit.id]

    summary = await service.summarize(date(2026, 11, 1), date(2026, 11, 30))
    assert summary == [
        ExpenseSummaryItem(cat_transit.id, "Transit", 1, Decimal("20.00")),
        ExpenseSummaryItem(cat_food.id, "Food", 3, Decimal("16.67")),
    ]

    with pytest.raises(InvalidExpensePeriod):
        await service.list_expenses(date(2026, 11, 20), date(2026, 11, 10))
    with pytest.raises(InvalidExpensePeriod):
        await service.summarize(date(2026, 11, 20), date(2026, 11, 10))
