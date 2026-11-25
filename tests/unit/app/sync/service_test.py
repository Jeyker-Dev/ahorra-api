from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Protocol
from uuid import UUID, uuid4

import pytest

from app.sync.domain import (
    SYNC_SAFETY_LAG,
    SyncCollaborators,
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
from app.sync.errors import InvalidSyncCursor
from app.sync.schemas import SyncChangesOut
from app.sync.service import SyncService


class _FakeClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def now(self) -> datetime:
        return self.current


class _HasTimestamp(Protocol):
    @property
    def id(self) -> UUID: ...

    @property
    def updated_at(self) -> datetime: ...


def _filter_window[T: _HasTimestamp](
    rows: list[T],
    since: datetime | None,
    until: datetime,
) -> list[T]:
    filtered = [
        row for row in rows if row.updated_at <= until and (since is None or row.updated_at > since)
    ]
    return sorted(filtered, key=lambda r: (r.updated_at, r.id))


class _FakeIncomeSource:
    def __init__(
        self,
        rules: list[SyncIncomeRule] | None = None,
        occurrences: list[SyncIncomeOccurrence] | None = None,
    ) -> None:
        self.rules = rules or []
        self.occurrences = occurrences or []

    async def list_rules_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[SyncIncomeRule]:
        return _filter_window(self.rules, since, until)

    async def list_occurrences_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[SyncIncomeOccurrence]:
        return _filter_window(self.occurrences, since, until)


class _FakeExpensesSource:
    def __init__(
        self,
        categories: list[SyncExpenseCategory] | None = None,
        expenses: list[SyncExpense] | None = None,
    ) -> None:
        self.categories = categories or []
        self.expenses = expenses or []

    async def list_categories_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[SyncExpenseCategory]:
        return _filter_window(self.categories, since, until)

    async def list_expenses_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[SyncExpense]:
        return _filter_window(self.expenses, since, until)


class _FakeProductsSource:
    def __init__(
        self,
        products: list[SyncProduct] | None = None,
        prices: list[SyncProductPrice] | None = None,
    ) -> None:
        self.products = products or []
        self.prices = prices or []

    async def list_products_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[SyncProduct]:
        return _filter_window(self.products, since, until)

    async def list_prices_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[SyncProductPrice]:
        return _filter_window(self.prices, since, until)


class _FakeShoppingSource:
    def __init__(
        self,
        lists: list[SyncShoppingList] | None = None,
        items: list[SyncShoppingListItem] | None = None,
    ) -> None:
        self.lists = lists or []
        self.items = items or []

    async def list_lists_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[SyncShoppingList]:
        return _filter_window(self.lists, since, until)

    async def list_items_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[SyncShoppingListItem]:
        return _filter_window(self.items, since, until)


class _FakeGoalsSource:
    def __init__(
        self,
        goals: list[SyncGoal] | None = None,
        contributions: list[SyncGoalContribution] | None = None,
    ) -> None:
        self.goals = goals or []
        self.contributions = contributions or []

    async def list_goals_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[SyncGoal]:
        return _filter_window(self.goals, since, until)

    async def list_contributions_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[SyncGoalContribution]:
        return _filter_window(self.contributions, since, until)


class _FakeTombstoneRepository:
    def __init__(self, tombstones: list[Tombstone] | None = None) -> None:
        self.tombstones = tombstones or []

    async def list_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[Tombstone]:
        filtered = [
            t
            for t in self.tombstones
            if t.deleted_at <= until and (since is None or t.deleted_at > since)
        ]
        return sorted(filtered, key=lambda t: (t.deleted_at, t.entity, t.id))


def _make_product(
    *,
    product_id: UUID | None = None,
    updated_at: datetime,
    archived_at: datetime | None = None,
) -> SyncProduct:
    return SyncProduct(
        id=product_id or uuid4(),
        name="Coffee",
        unit="kg",
        last_price=Decimal("10.00"),
        last_price_currency="usd",
        archived_at=archived_at,
        created_at=updated_at,
        updated_at=updated_at,
    )


def _make_list_item(
    *,
    item_id: UUID | None = None,
    updated_at: datetime,
) -> SyncShoppingListItem:
    return SyncShoppingListItem(
        id=item_id or uuid4(),
        list_id=uuid4(),
        product_id=uuid4(),
        quantity=Decimal("2.000"),
        status="pending",
        bought_price=None,
        bought_currency=None,
        bought_at=None,
        created_at=updated_at,
        updated_at=updated_at,
    )


@dataclass
class _FakeSyncDataset:
    income: _FakeIncomeSource = field(default_factory=_FakeIncomeSource)
    expenses: _FakeExpensesSource = field(default_factory=_FakeExpensesSource)
    products: _FakeProductsSource = field(default_factory=_FakeProductsSource)
    shopping: _FakeShoppingSource = field(default_factory=_FakeShoppingSource)
    goals: _FakeGoalsSource = field(default_factory=_FakeGoalsSource)
    tombstones: list[Tombstone] = field(default_factory=list)


def _make_service(
    *,
    now: datetime,
    dataset: _FakeSyncDataset | None = None,
) -> SyncService:
    data = dataset or _FakeSyncDataset()
    return SyncService(
        collaborators=SyncCollaborators(
            income=data.income,
            expenses=data.expenses,
            products=data.products,
            shopping=data.shopping,
            goals=data.goals,
            tombstones=_FakeTombstoneRepository(data.tombstones),
            clock=_FakeClock(now),
        )
    )


async def test_first_sync_returns_snapshot_up_to_until_with_safety_lag() -> None:
    now = datetime(2026, 11, 25, 14, 0, 30, tzinfo=UTC)
    expected_until = now - SYNC_SAFETY_LAG
    assert SYNC_SAFETY_LAG.total_seconds() == 30

    rule = SyncIncomeRule(
        id=uuid4(),
        name="Salary",
        amount=Decimal("1000.00"),
        currency="usd",
        days=(15, 30),
        start_date=date(2026, 11, 1),
        end_date=None,
        ended_at=expected_until,
        created_at=expected_until - timedelta(hours=1),
        updated_at=expected_until,
    )
    occurrence = SyncIncomeOccurrence(
        id=uuid4(),
        rule_id=rule.id,
        expected_date=date(2026, 11, 15),
        expected_amount=Decimal("500.00"),
        currency="usd",
        status="expected",
        received_date=None,
        received_amount=None,
        rate_value=None,
        rate_date=None,
        received_at=None,
        updated_at=expected_until - timedelta(seconds=10),
    )
    category = SyncExpenseCategory(
        id=uuid4(),
        name="Food",
        archived_at=expected_until - timedelta(minutes=1),
        created_at=expected_until - timedelta(hours=2),
        updated_at=expected_until - timedelta(minutes=1),
    )
    expense = SyncExpense(
        id=uuid4(),
        category_id=category.id,
        amount=Decimal("25.00"),
        currency="usd",
        spent_on=date(2026, 11, 25),
        note=None,
        bcv_rate_value=Decimal("900.00000000"),
        bcv_rate_date=date(2026, 11, 25),
        usdt_rate_value=Decimal("950.00000000"),
        usdt_rate_date=date(2026, 11, 25),
        deleted_at=expected_until - timedelta(seconds=5),
        created_at=expected_until - timedelta(minutes=5),
        updated_at=expected_until - timedelta(seconds=5),
    )
    included_product = _make_product(updated_at=expected_until, archived_at=expected_until)
    too_recent_product = _make_product(updated_at=expected_until + timedelta(seconds=1))
    price = SyncProductPrice(
        id=uuid4(),
        product_id=included_product.id,
        price=Decimal("10.00"),
        currency="usd",
        recorded_on=date(2026, 11, 25),
        source="manual",
        list_item_id=None,
        bcv_rate_value=Decimal("900.00000000"),
        bcv_rate_date=date(2026, 11, 25),
        usdt_rate_value=Decimal("950.00000000"),
        usdt_rate_date=date(2026, 11, 25),
        created_at=expected_until - timedelta(minutes=2),
        updated_at=expected_until - timedelta(minutes=1),
    )
    shopping_list = SyncShoppingList(
        id=uuid4(),
        name="Market",
        status="open",
        closed_at=None,
        expense_id=None,
        deleted_at=expected_until,
        created_at=expected_until - timedelta(minutes=3),
        updated_at=expected_until,
    )
    list_item = _make_list_item(updated_at=expected_until - timedelta(seconds=2))
    goal = SyncGoal(
        id=uuid4(),
        name="Reserve",
        target_usd=Decimal("500.00"),
        target_date=None,
        note=None,
        archived_at=None,
        deleted_at=expected_until,
        created_at=expected_until - timedelta(hours=1),
        updated_at=expected_until,
    )
    contribution = SyncGoalContribution(
        id=uuid4(),
        goal_id=goal.id,
        kind="deposit",
        amount=Decimal("50.00"),
        currency="usd",
        contributed_on=date(2026, 11, 25),
        note=None,
        bcv_rate_value=Decimal("900.00000000"),
        bcv_rate_date=date(2026, 11, 25),
        usdt_rate_value=Decimal("950.00000000"),
        usdt_rate_date=date(2026, 11, 25),
        deleted_at=None,
        created_at=expected_until - timedelta(minutes=1),
        updated_at=expected_until,
    )
    tombstone_included = Tombstone(
        entity=SyncEntity.income_occurrences,
        id=uuid4(),
        deleted_at=expected_until,
    )
    tombstone_too_recent = Tombstone(
        entity=SyncEntity.income_occurrences,
        id=uuid4(),
        deleted_at=expected_until + timedelta(microseconds=1),
    )

    service = _make_service(
        now=now,
        dataset=_FakeSyncDataset(
            income=_FakeIncomeSource(rules=[rule], occurrences=[occurrence]),
            expenses=_FakeExpensesSource(categories=[category], expenses=[expense]),
            products=_FakeProductsSource(
                products=[included_product, too_recent_product],
                prices=[price],
            ),
            shopping=_FakeShoppingSource(lists=[shopping_list], items=[list_item]),
            goals=_FakeGoalsSource(goals=[goal], contributions=[contribution]),
            tombstones=[tombstone_included, tombstone_too_recent],
        ),
    )

    result = await service.changes()

    assert result.next_cursor == expected_until
    assert result.income_rules == [rule]
    assert result.income_occurrences == [occurrence]
    assert result.expense_categories == [category]
    assert result.expenses == [expense]
    assert result.products == [included_product]
    assert result.product_prices == [price]
    assert result.shopping_lists == [shopping_list]
    assert result.shopping_list_items == [list_item]
    assert result.goals == [goal]
    assert result.goal_contributions == [contribution]
    assert result.tombstones == [tombstone_included]


async def test_window_bounds_since_exclusive_until_inclusive() -> None:
    since = datetime(2026, 11, 25, 13, 0, 0, tzinfo=UTC)
    now = datetime(2026, 11, 25, 14, 0, 30, tzinfo=UTC)
    until = now - SYNC_SAFETY_LAG

    at_since = _make_product(updated_at=since)
    inside = _make_product(updated_at=since + timedelta(microseconds=1))
    at_until = _make_product(updated_at=until)
    after_until = _make_product(updated_at=until + timedelta(microseconds=1))

    t_at_since = Tombstone(entity=SyncEntity.shopping_list_items, id=uuid4(), deleted_at=since)
    t_inside = Tombstone(
        entity=SyncEntity.shopping_list_items,
        id=uuid4(),
        deleted_at=since + timedelta(seconds=1),
    )
    t_at_until = Tombstone(entity=SyncEntity.shopping_list_items, id=uuid4(), deleted_at=until)
    t_after_until = Tombstone(
        entity=SyncEntity.shopping_list_items,
        id=uuid4(),
        deleted_at=until + timedelta(seconds=1),
    )

    service = _make_service(
        now=now,
        dataset=_FakeSyncDataset(
            products=_FakeProductsSource(products=[at_since, inside, at_until, after_until]),
            tombstones=[t_at_since, t_inside, t_at_until, t_after_until],
        ),
    )

    result = await service.changes(since="2026-11-25T13:00:00Z")

    assert result.products == [inside, at_until]
    assert result.tombstones == [t_inside, t_at_until]
    assert result.next_cursor == until


async def test_cursor_accepts_plus_zero_offset_and_since_equal_to_until() -> None:
    now = datetime(2026, 11, 25, 14, 0, 30, tzinfo=UTC)
    until = now - SYNC_SAFETY_LAG
    product = _make_product(updated_at=until)
    service = _make_service(
        now=now,
        dataset=_FakeSyncDataset(products=_FakeProductsSource(products=[product])),
    )

    result = await service.changes(since="2026-11-25T14:00:00+00:00")

    assert result.products == []
    assert result.next_cursor == until


@pytest.mark.parametrize(
    "bad_cursor",
    [
        "",
        "   ",
        "not-a-timestamp",
        "2026-11-25",
        "2026-11-25Z",
        "2026-11-25 14:00:00Z",
        " 2026-11-25T14:00:00Z",
        "2026-11-25T14:00:00",
        "2026-11-25T14:00:00-04:00",
        "2026-11-25T14:00:00+02:00",
        "2026-11-25T14:00:00-00:00",
        "2026-11-25T14:00:01Z",
        "2026-11-25T14:00:15Z",
        "2026-11-25T15:00:00Z",
    ],
)
async def test_invalid_cursor_raises_invalid_sync_cursor(bad_cursor: str) -> None:
    now = datetime(2026, 11, 25, 14, 0, 30, tzinfo=UTC)
    service = _make_service(now=now)

    with pytest.raises(InvalidSyncCursor):
        await service.changes(since=bad_cursor)


async def test_tombstone_wins_when_deleted_at_is_later_than_row_updated_at() -> None:
    now = datetime(2026, 11, 25, 14, 0, 30, tzinfo=UTC)
    item_id = uuid4()
    row = _make_list_item(
        item_id=item_id,
        updated_at=datetime(2026, 11, 25, 13, 50, 0, tzinfo=UTC),
    )
    tombstone = Tombstone(
        entity=SyncEntity.shopping_list_items,
        id=item_id,
        deleted_at=datetime(2026, 11, 25, 13, 55, 0, tzinfo=UTC),
    )
    service = _make_service(
        now=now,
        dataset=_FakeSyncDataset(
            shopping=_FakeShoppingSource(items=[row]),
            tombstones=[tombstone],
        ),
    )

    result = await service.changes()

    assert result.shopping_list_items == []
    assert result.tombstones == [tombstone]


async def test_row_wins_when_updated_at_is_later_than_or_equal_to_deleted_at() -> None:
    now = datetime(2026, 11, 25, 14, 0, 30, tzinfo=UTC)
    later_id = uuid4()
    equal_id = uuid4()
    row_later = _make_list_item(
        item_id=later_id,
        updated_at=datetime(2026, 11, 25, 13, 55, 0, tzinfo=UTC),
    )
    tombstone_earlier = Tombstone(
        entity=SyncEntity.shopping_list_items,
        id=later_id,
        deleted_at=datetime(2026, 11, 25, 13, 50, 0, tzinfo=UTC),
    )
    equal_ts = datetime(2026, 11, 25, 13, 52, 0, tzinfo=UTC)
    row_equal = _make_list_item(item_id=equal_id, updated_at=equal_ts)
    tombstone_equal = Tombstone(
        entity=SyncEntity.shopping_list_items,
        id=equal_id,
        deleted_at=equal_ts,
    )
    service = _make_service(
        now=now,
        dataset=_FakeSyncDataset(
            shopping=_FakeShoppingSource(items=[row_later, row_equal]),
            tombstones=[tombstone_earlier, tombstone_equal],
        ),
    )

    result = await service.changes()

    assert result.shopping_list_items == [row_equal, row_later]
    assert result.tombstones == []


async def test_empty_sync_schema_preserves_all_keys_as_empty_arrays_and_decimal_strings() -> None:
    now = datetime(2026, 11, 25, 14, 0, 30, tzinfo=UTC)
    product = _make_product(updated_at=now - SYNC_SAFETY_LAG)
    service = _make_service(
        now=now,
        dataset=_FakeSyncDataset(products=_FakeProductsSource(products=[product])),
    )

    result = await service.changes()
    payload = SyncChangesOut.from_domain(result).model_dump(mode="json")

    assert set(payload) == {
        "next_cursor",
        "income_rules",
        "income_occurrences",
        "expense_categories",
        "expenses",
        "products",
        "product_prices",
        "shopping_lists",
        "shopping_list_items",
        "goals",
        "goal_contributions",
        "tombstones",
    }
    assert payload["next_cursor"] == "2026-11-25T14:00:00Z"
    assert payload["products"][0]["last_price"] == "10.00"
    assert payload["tombstones"] == []
