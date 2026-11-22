from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.shopping.domain import (
    BuyItemInput,
    CloseListInput,
    Currency,
    ItemStatus,
    ListStatus,
    ProductSummary,
    PurchaseHistoryRef,
    RateSnapshot,
    RecordExpenseInput,
    RecordPurchaseInput,
    ShoppingCollaborators,
    ShoppingList,
    ShoppingListItem,
    Unit,
    UpsertItemInput,
)
from app.shopping.errors import (
    DuplicateListItem,
    InvalidDaysAhead,
    InvalidItemPrice,
    InvalidItemQuantity,
    InvalidListName,
    ItemAlreadyBought,
    ListClosed,
    ListDeleted,
    ListItemNotFound,
    NothingBought,
    PriceRequired,
    ProductArchived,
    ProductNotFound,
    RateNotAvailableForDate,
    ShoppingListNotFound,
)
from app.shopping.service import ShoppingService


class _FakeClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def now(self) -> datetime:
        return self.current


class _InMemoryListRepository:
    def __init__(self, initial: list[ShoppingList] | None = None) -> None:
        self.items: dict[UUID, ShoppingList] = {lst.id: lst for lst in initial or []}
        self.update_calls = 0
        self.simulate_concurrent_existing = False

    async def get_by_id(self, list_id: UUID) -> ShoppingList | None:
        return self.items.get(list_id)

    async def list_non_deleted(self, status: ListStatus | None = None) -> list[ShoppingList]:
        matching = [
            lst
            for lst in self.items.values()
            if lst.deleted_at is None and (status is None or lst.status == status)
        ]
        matching.sort(key=lambda lst: (-lst.created_at.timestamp(), lst.id))
        return matching

    async def insert_if_absent(self, shopping_list: ShoppingList) -> tuple[ShoppingList, bool]:
        if self.simulate_concurrent_existing:
            self.simulate_concurrent_existing = False
            self.items[shopping_list.id] = shopping_list
            return shopping_list, False
        if shopping_list.id in self.items:
            return self.items[shopping_list.id], False
        self.items[shopping_list.id] = shopping_list
        return shopping_list, True

    async def update(self, shopping_list: ShoppingList) -> ShoppingList:
        self.update_calls += 1
        self.items[shopping_list.id] = shopping_list
        return shopping_list


class _InMemoryItemRepository:
    def __init__(
        self,
        lists_repo: _InMemoryListRepository,
        initial: list[ShoppingListItem] | None = None,
    ) -> None:
        self._lists_repo = lists_repo
        self.items: dict[UUID, ShoppingListItem] = {item.id: item for item in initial or []}
        self.update_calls = 0

    async def get_by_id(self, item_id: UUID) -> ShoppingListItem | None:
        return self.items.get(item_id)

    async def get_by_list_and_product(
        self,
        list_id: UUID,
        product_id: UUID,
    ) -> ShoppingListItem | None:
        for item in self.items.values():
            if item.list_id == list_id and item.product_id == product_id:
                return item
        return None

    async def list_for_list(self, list_id: UUID) -> list[ShoppingListItem]:
        matching = [item for item in self.items.values() if item.list_id == list_id]
        matching.sort(key=lambda item: (item.created_at, item.id))
        return matching

    async def insert_if_absent(self, item: ShoppingListItem) -> tuple[ShoppingListItem, bool]:
        if item.id in self.items:
            return self.items[item.id], False
        existing = await self.get_by_list_and_product(item.list_id, item.product_id)
        if existing is not None:
            raise DuplicateListItem("Duplicate product on list.")
        self.items[item.id] = item
        return item, True

    async def update(self, item: ShoppingListItem) -> ShoppingListItem:
        self.update_calls += 1
        self.items[item.id] = item
        return item

    async def delete_by_id(self, item_id: UUID) -> None:
        self.items.pop(item_id, None)

    async def list_non_deleted_item_ids(self) -> set[UUID]:
        return {
            item.id
            for item in self.items.values()
            if self._lists_repo.items[item.list_id].deleted_at is None
        }

    async def list_bought_quantities_by_product(self) -> dict[UUID, list[Decimal]]:
        grouped: dict[UUID, list[Decimal]] = {}
        for item in self.items.values():
            if (
                self._lists_repo.items[item.list_id].deleted_at is None
                and item.status == ItemStatus.bought
            ):
                grouped.setdefault(item.product_id, []).append(item.quantity)
        return grouped


class _FakeProductCatalog:
    def __init__(
        self,
        products: list[ProductSummary] | None = None,
        purchase_refs: list[PurchaseHistoryRef] | None = None,
    ) -> None:
        self.products: dict[UUID, ProductSummary] = {p.id: p for p in products or []}
        self.purchase_entries: dict[UUID, RecordPurchaseInput] = {}
        self.extra_refs: list[PurchaseHistoryRef] = purchase_refs or []
        self.history_stack: dict[UUID, list[tuple[Decimal, Currency]]] = {
            p.id: [(p.last_price, p.last_price_currency)]
            for p in self.products.values()
            if p.last_price is not None and p.last_price_currency is not None
        }

    async def get_product(self, product_id: UUID) -> ProductSummary | None:
        return self.products.get(product_id)

    async def list_all_products(self) -> list[ProductSummary]:
        return list(self.products.values())

    async def record_purchase_price(self, payload: RecordPurchaseInput) -> ProductSummary:
        self.purchase_entries[payload.list_item_id] = payload
        product = self.products[payload.product_id]
        updated = replace(
            product,
            last_price=payload.price,
            last_price_currency=payload.currency,
        )
        self.products[payload.product_id] = updated
        return updated

    async def delete_purchase_price_and_restore(
        self,
        product_id: UUID,
        list_item_id: UUID,
    ) -> ProductSummary:
        self.purchase_entries.pop(list_item_id, None)
        product = self.products[product_id]
        stack = self.history_stack.get(product_id, [])
        if stack:
            prev_price, prev_currency = stack[-1]
        else:
            prev_price, prev_currency = None, None
        updated = replace(
            product,
            last_price=prev_price,
            last_price_currency=prev_currency,
        )
        self.products[product_id] = updated
        return updated

    async def list_purchase_refs_for_active_products(self) -> list[PurchaseHistoryRef]:
        from_recorded = [
            PurchaseHistoryRef(
                product_id=entry.product_id,
                recorded_on=entry.recorded_on,
                list_item_id=entry.list_item_id,
            )
            for entry in self.purchase_entries.values()
            if self.products[entry.product_id].archived_at is None
        ]
        from_extra = [
            ref for ref in self.extra_refs if self.products[ref.product_id].archived_at is None
        ]
        return from_recorded + from_extra


class _FakeExpenseError(Exception):
    pass


class _FakeExpenseRecorder:
    def __init__(self) -> None:
        self.recorded: dict[UUID, RecordExpenseInput] = {}
        self.error_to_raise: Exception | None = None

    async def upsert_expense(self, payload: RecordExpenseInput) -> UUID:
        if self.error_to_raise is not None:
            raise self.error_to_raise
        self.recorded[payload.expense_id] = payload
        return payload.expense_id


class _FakeRateLookup:
    def __init__(
        self,
        bcv_rates: dict[date, RateSnapshot] | None = None,
        usdt_rates: dict[date, RateSnapshot] | None = None,
    ) -> None:
        self.bcv_rates = bcv_rates or {}
        self.usdt_rates = usdt_rates or {}

    async def bcv_in_force(self, on: date) -> RateSnapshot | None:
        eligible = [d for d in self.bcv_rates if d <= on]
        if not eligible:
            return None
        return self.bcv_rates[max(eligible)]

    async def usdt_in_force(self, on: date) -> RateSnapshot | None:
        eligible = [d for d in self.usdt_rates if d <= on]
        if not eligible:
            return None
        return self.usdt_rates[max(eligible)]


def _default_rates() -> tuple[dict[date, RateSnapshot], dict[date, RateSnapshot]]:
    today = date(2026, 11, 22)
    return (
        {today: RateSnapshot(value=Decimal("900.00000000"), value_date=today)},
        {today: RateSnapshot(value=Decimal("950.00000000"), value_date=today)},
    )


@dataclass(frozen=True, slots=True)
class _ServiceSeed:
    now: datetime | None = None
    lists: list[ShoppingList] | None = None
    items: list[ShoppingListItem] | None = None
    products: list[ProductSummary] | None = None
    purchase_refs: list[PurchaseHistoryRef] | None = None
    bcv_rates: dict[date, RateSnapshot] | None = None
    usdt_rates: dict[date, RateSnapshot] | None = None


def _make_service(
    *,
    now: datetime | None = None,
    lists: list[ShoppingList] | None = None,
    products: list[ProductSummary] | None = None,
    bcv_rates: dict[date, RateSnapshot] | None = None,
    usdt_rates: dict[date, RateSnapshot] | None = None,
) -> tuple[
    ShoppingService,
    _InMemoryListRepository,
    _InMemoryItemRepository,
    _FakeProductCatalog,
    _FakeExpenseRecorder,
    _FakeClock,
]:
    return _build_service(
        _ServiceSeed(
            now=now,
            lists=lists,
            products=products,
            bcv_rates=bcv_rates,
            usdt_rates=usdt_rates,
        )
    )


def _build_service(
    seed: _ServiceSeed,
) -> tuple[
    ShoppingService,
    _InMemoryListRepository,
    _InMemoryItemRepository,
    _FakeProductCatalog,
    _FakeExpenseRecorder,
    _FakeClock,
]:
    default_bcv, default_usdt = _default_rates()
    clock = _FakeClock(seed.now or datetime(2026, 11, 22, 14, 0, tzinfo=UTC))
    list_repo = _InMemoryListRepository(seed.lists)
    item_repo = _InMemoryItemRepository(list_repo, seed.items)
    product_catalog = _FakeProductCatalog(
        products=seed.products,
        purchase_refs=seed.purchase_refs,
    )
    expense_recorder = _FakeExpenseRecorder()
    rates = _FakeRateLookup(
        bcv_rates=default_bcv if seed.bcv_rates is None else seed.bcv_rates,
        usdt_rates=default_usdt if seed.usdt_rates is None else seed.usdt_rates,
    )
    service = ShoppingService(
        ShoppingCollaborators(
            lists=list_repo,
            items=item_repo,
            products=product_catalog,
            expenses=expense_recorder,
            rates=rates,
            clock=clock,
        )
    )
    return service, list_repo, item_repo, product_catalog, expense_recorder, clock


def _product(
    *,
    name: str = "Rice",
    unit: Unit = Unit.kg,
    last_price: Decimal | None = Decimal("2.50"),
    last_price_currency: Currency | None = Currency.usd,
    archived_at: datetime | None = None,
) -> ProductSummary:
    return ProductSummary(
        id=uuid4(),
        name=name,
        unit=unit,
        last_price=last_price,
        last_price_currency=last_price_currency,
        archived_at=archived_at,
    )


# Rule 4: upsert_list & delete_list


async def test_upsert_list_creates_normalizes_and_is_idempotent() -> None:
    service, list_repo, _, _, _, clock = _make_service()
    list_id = uuid4()

    created = await service.upsert_list(list_id, "  Weekly Market  ")
    assert created.created is True
    assert created.shopping_list.name == "Weekly Market"
    assert created.shopping_list.status == ListStatus.open
    assert created.shopping_list.created_at == clock.current

    clock.current += timedelta(minutes=5)
    retry = await service.upsert_list(list_id, "Weekly Market")
    assert retry.created is False
    assert retry.shopping_list.updated_at == created.shopping_list.updated_at
    assert list_repo.update_calls == 0

    renamed = await service.upsert_list(list_id, "Weekend Market")
    assert renamed.created is False
    assert renamed.shopping_list.name == "Weekend Market"
    assert renamed.shopping_list.updated_at == clock.current


async def test_upsert_list_raises_invalid_list_name() -> None:
    service, _, _, _, _, _ = _make_service()

    with pytest.raises(InvalidListName):
        await service.upsert_list(uuid4(), "   ")

    with pytest.raises(InvalidListName):
        await service.upsert_list(uuid4(), "x" * 81)


async def test_upsert_list_raises_list_closed() -> None:
    now = datetime(2026, 11, 22, 14, 0, tzinfo=UTC)
    closed_list = ShoppingList(
        id=uuid4(),
        name="Market",
        status=ListStatus.closed,
        closed_at=now,
        expense_id=uuid4(),
        deleted_at=None,
        created_at=now,
        updated_at=now,
    )
    service, _, _, _, _, _ = _make_service(lists=[closed_list])

    with pytest.raises(ListClosed):
        await service.upsert_list(closed_list.id, "Renamed")


async def test_upsert_list_raises_list_deleted() -> None:
    now = datetime(2026, 11, 22, 14, 0, tzinfo=UTC)
    deleted_list = ShoppingList(
        id=uuid4(),
        name="Market",
        status=ListStatus.open,
        closed_at=None,
        expense_id=None,
        deleted_at=now,
        created_at=now,
        updated_at=now,
    )
    service, _, _, _, _, _ = _make_service(lists=[deleted_list])

    with pytest.raises(ListDeleted):
        await service.upsert_list(deleted_list.id, "Renamed")


async def test_delete_list_soft_deletes_idempotently() -> None:
    service, list_repo, _, _, _, clock = _make_service()
    list_id = uuid4()
    await service.upsert_list(list_id, "Market")

    clock.current += timedelta(minutes=5)
    deleted = await service.delete_list(list_id)
    assert deleted.deleted_at == clock.current
    updates_after_delete = list_repo.update_calls

    clock.current += timedelta(minutes=5)
    retry = await service.delete_list(list_id)
    assert retry.deleted_at == deleted.deleted_at
    assert list_repo.update_calls == updates_after_delete


async def test_delete_list_raises_shopping_list_not_found() -> None:
    service, _, _, _, _, _ = _make_service()

    with pytest.raises(ShoppingListNotFound):
        await service.delete_list(uuid4())


async def test_list_lists_excludes_deleted_and_filters_by_status() -> None:
    t1 = datetime(2026, 11, 22, 10, 0, tzinfo=UTC)
    t2 = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
    open_old = ShoppingList(
        id=uuid4(),
        name="Old",
        status=ListStatus.open,
        closed_at=None,
        expense_id=None,
        deleted_at=None,
        created_at=t1,
        updated_at=t1,
    )
    closed_new = ShoppingList(
        id=uuid4(),
        name="Closed",
        status=ListStatus.closed,
        closed_at=t2,
        expense_id=uuid4(),
        deleted_at=None,
        created_at=t2,
        updated_at=t2,
    )
    deleted = ShoppingList(
        id=uuid4(),
        name="Deleted",
        status=ListStatus.open,
        closed_at=None,
        expense_id=None,
        deleted_at=t2,
        created_at=t2,
        updated_at=t2,
    )
    service, _, _, _, _, _ = _make_service(lists=[open_old, closed_new, deleted])

    assert [lst.id for lst in await service.list_lists()] == [closed_new.id, open_old.id]
    assert [lst.id for lst in await service.list_lists(ListStatus.open)] == [open_old.id]
    assert [lst.id for lst in await service.list_lists(ListStatus.closed)] == [closed_new.id]


async def test_get_list_returns_items_ordered_by_created_at_asc() -> None:
    prod = _product()
    prod2 = _product(name="Pasta")
    service, _, _, _, _, clock = _make_service(products=[prod, prod2])
    list_id = uuid4()
    await service.upsert_list(list_id, "Market")
    item1_id = uuid4()
    item2_id = uuid4()
    await service.upsert_item(
        UpsertItemInput(
            list_id=list_id, item_id=item1_id, product_id=prod.id, quantity=Decimal("1")
        )
    )
    clock.current += timedelta(minutes=1)
    await service.upsert_item(
        UpsertItemInput(
            list_id=list_id,
            item_id=item2_id,
            product_id=prod2.id,
            quantity=Decimal("2"),
        )
    )

    detail = await service.get_list(list_id)
    assert [entry.item.id for entry in detail.items] == [item1_id, item2_id]
    assert detail.items[0].product_name == prod.name


async def test_get_list_raises_not_found_for_deleted_list() -> None:
    service, _, _, _, _, _ = _make_service()
    list_id = uuid4()
    await service.upsert_list(list_id, "Market")
    await service.delete_list(list_id)

    with pytest.raises(ShoppingListNotFound):
        await service.get_list(list_id)


# Rule 5: upsert_item


async def test_upsert_item_creates_and_updates_quantity() -> None:
    prod = _product()
    service, _, _, _, _, clock = _make_service(products=[prod])
    list_id = uuid4()
    item_id = uuid4()
    await service.upsert_list(list_id, "Market")

    created = await service.upsert_item(
        UpsertItemInput(
            list_id=list_id,
            item_id=item_id,
            product_id=prod.id,
            quantity=Decimal("1.2345"),
        )
    )
    assert created.created is True
    assert created.item.item.quantity == Decimal("1.234")
    assert created.item.item.status == ItemStatus.pending

    clock.current += timedelta(minutes=1)
    updated = await service.upsert_item(
        UpsertItemInput(
            list_id=list_id,
            item_id=item_id,
            product_id=prod.id,
            quantity=Decimal("2.000"),
        )
    )
    assert updated.created is False
    assert updated.item.item.quantity == Decimal("2.000")


async def test_upsert_item_allows_quantity_change_on_bought_item() -> None:
    prod = _product()
    service, _, _, _, _, _ = _make_service(products=[prod])
    list_id = uuid4()
    item_id = uuid4()
    await service.upsert_list(list_id, "Market")
    await service.upsert_item(
        UpsertItemInput(list_id=list_id, item_id=item_id, product_id=prod.id, quantity=Decimal("1"))
    )
    await service.buy_item(BuyItemInput(list_id=list_id, item_id=item_id))

    updated = await service.upsert_item(
        UpsertItemInput(list_id=list_id, item_id=item_id, product_id=prod.id, quantity=Decimal("3"))
    )
    assert updated.item.item.quantity == Decimal("3.000")
    assert updated.item.item.status == ItemStatus.bought


async def test_upsert_item_raises_item_already_bought_on_product_change() -> None:
    prod1 = _product(name="Rice")
    prod2 = _product(name="Beans")
    service, _, _, _, _, _ = _make_service(products=[prod1, prod2])
    list_id = uuid4()
    item_id = uuid4()
    await service.upsert_list(list_id, "Market")
    await service.upsert_item(
        UpsertItemInput(
            list_id=list_id, item_id=item_id, product_id=prod1.id, quantity=Decimal("1")
        )
    )
    await service.buy_item(BuyItemInput(list_id=list_id, item_id=item_id))

    with pytest.raises(ItemAlreadyBought):
        await service.upsert_item(
            UpsertItemInput(
                list_id=list_id, item_id=item_id, product_id=prod2.id, quantity=Decimal("1")
            )
        )


async def test_upsert_item_raises_product_archived() -> None:
    archived = _product(archived_at=datetime(2026, 11, 20, 12, 0, tzinfo=UTC))
    service, _, _, _, _, _ = _make_service(products=[archived])
    list_id = uuid4()
    await service.upsert_list(list_id, "Market")

    with pytest.raises(ProductArchived):
        await service.upsert_item(
            UpsertItemInput(
                list_id=list_id, item_id=uuid4(), product_id=archived.id, quantity=Decimal("1")
            )
        )


async def test_upsert_item_raises_duplicate_list_item() -> None:
    prod = _product()
    service, _, _, _, _, _ = _make_service(products=[prod])
    list_id = uuid4()
    await service.upsert_list(list_id, "Market")
    await service.upsert_item(
        UpsertItemInput(list_id=list_id, item_id=uuid4(), product_id=prod.id, quantity=Decimal("1"))
    )

    with pytest.raises(DuplicateListItem):
        await service.upsert_item(
            UpsertItemInput(
                list_id=list_id, item_id=uuid4(), product_id=prod.id, quantity=Decimal("2")
            )
        )


async def test_upsert_item_raises_invalid_item_quantity() -> None:
    prod = _product()
    service, _, _, _, _, _ = _make_service(products=[prod])
    list_id = uuid4()
    await service.upsert_list(list_id, "Market")

    with pytest.raises(InvalidItemQuantity):
        await service.upsert_item(
            UpsertItemInput(
                list_id=list_id, item_id=uuid4(), product_id=prod.id, quantity=Decimal("0")
            )
        )


async def test_upsert_item_raises_product_not_found() -> None:
    service, _, _, _, _, _ = _make_service()
    list_id = uuid4()
    await service.upsert_list(list_id, "Market")

    with pytest.raises(ProductNotFound):
        await service.upsert_item(
            UpsertItemInput(
                list_id=list_id, item_id=uuid4(), product_id=uuid4(), quantity=Decimal("1")
            )
        )


# Rule 6: delete_item


async def test_delete_item_removes_pending_item() -> None:
    prod = _product()
    service, _, item_repo, _, _, _ = _make_service(products=[prod])
    list_id = uuid4()
    item_id = uuid4()
    await service.upsert_list(list_id, "Market")
    await service.upsert_item(
        UpsertItemInput(list_id=list_id, item_id=item_id, product_id=prod.id, quantity=Decimal("1"))
    )

    await service.delete_item(list_id, item_id)
    assert item_id not in item_repo.items


async def test_delete_item_raises_item_already_bought() -> None:
    prod = _product()
    service, _, _, _, _, _ = _make_service(products=[prod])
    list_id = uuid4()
    item_id = uuid4()
    await service.upsert_list(list_id, "Market")
    await service.upsert_item(
        UpsertItemInput(list_id=list_id, item_id=item_id, product_id=prod.id, quantity=Decimal("1"))
    )
    await service.buy_item(BuyItemInput(list_id=list_id, item_id=item_id))

    with pytest.raises(ItemAlreadyBought):
        await service.delete_item(list_id, item_id)


async def test_delete_item_raises_list_item_not_found_for_other_list() -> None:
    prod = _product()
    service, _, _, _, _, _ = _make_service(products=[prod])
    list1 = uuid4()
    list2 = uuid4()
    item_id = uuid4()
    await service.upsert_list(list1, "List 1")
    await service.upsert_list(list2, "List 2")
    await service.upsert_item(
        UpsertItemInput(list_id=list1, item_id=item_id, product_id=prod.id, quantity=Decimal("1"))
    )

    with pytest.raises(ListItemNotFound):
        await service.delete_item(list2, item_id)


# Rules 7 & 8: buy_item


async def test_buy_item_defaults_to_product_last_price() -> None:
    prod = _product(last_price=Decimal("3.20"), last_price_currency=Currency.usd)
    service, _, _, catalog, _, clock = _make_service(products=[prod])
    list_id = uuid4()
    item_id = uuid4()
    await service.upsert_list(list_id, "Market")
    await service.upsert_item(
        UpsertItemInput(list_id=list_id, item_id=item_id, product_id=prod.id, quantity=Decimal("2"))
    )

    bought = await service.buy_item(BuyItemInput(list_id=list_id, item_id=item_id))

    assert bought.item.status == ItemStatus.bought
    assert bought.item.bought_price == Decimal("3.20")
    assert bought.item.bought_currency == Currency.usd
    assert bought.item.bought_at == clock.current
    assert catalog.purchase_entries[item_id].recorded_on == date(2026, 11, 22)


async def test_buy_item_correction_updates_same_entry_and_preserves_bought_at() -> None:
    prod = _product(last_price=Decimal("3.20"), last_price_currency=Currency.usd)
    service, _, _, catalog, _, clock = _make_service(products=[prod])
    list_id = uuid4()
    item_id = uuid4()
    await service.upsert_list(list_id, "Market")
    await service.upsert_item(
        UpsertItemInput(list_id=list_id, item_id=item_id, product_id=prod.id, quantity=Decimal("1"))
    )
    first_buy_time = clock.current
    await service.buy_item(BuyItemInput(list_id=list_id, item_id=item_id))

    clock.current += timedelta(days=1)
    corrected = await service.buy_item(
        BuyItemInput(
            list_id=list_id,
            item_id=item_id,
            price=Decimal("3200.00"),
            currency=Currency.ves,
        )
    )

    assert corrected.item.bought_price == Decimal("3200.00")
    assert corrected.item.bought_currency == Currency.ves
    assert corrected.item.bought_at == first_buy_time
    assert len(catalog.purchase_entries) == 1
    assert catalog.purchase_entries[item_id].recorded_on == date(2026, 11, 22)
    assert catalog.products[prod.id].last_price == Decimal("3200.00")


async def test_buy_item_raises_price_required_when_product_has_no_last_price() -> None:
    prod = _product(last_price=None, last_price_currency=None)
    service, _, _, _, _, _ = _make_service(products=[prod])
    list_id = uuid4()
    item_id = uuid4()
    await service.upsert_list(list_id, "Market")
    await service.upsert_item(
        UpsertItemInput(list_id=list_id, item_id=item_id, product_id=prod.id, quantity=Decimal("1"))
    )

    with pytest.raises(PriceRequired):
        await service.buy_item(BuyItemInput(list_id=list_id, item_id=item_id))


async def test_buy_item_raises_invalid_item_price() -> None:
    prod = _product()
    service, _, _, _, _, _ = _make_service(products=[prod])
    list_id = uuid4()
    item_id = uuid4()
    await service.upsert_list(list_id, "Market")
    await service.upsert_item(
        UpsertItemInput(list_id=list_id, item_id=item_id, product_id=prod.id, quantity=Decimal("1"))
    )

    with pytest.raises(InvalidItemPrice):
        await service.buy_item(
            BuyItemInput(list_id=list_id, item_id=item_id, price=Decimal("5.00"), currency=None)
        )

    with pytest.raises(InvalidItemPrice):
        await service.buy_item(
            BuyItemInput(
                list_id=list_id, item_id=item_id, price=Decimal("0"), currency=Currency.usd
            )
        )


async def test_buy_item_raises_rate_not_available_when_missing() -> None:
    prod = _product()
    bcv, _ = _default_rates()
    service, _, _, _, _, _ = _make_service(products=[prod], bcv_rates=bcv, usdt_rates={})
    list_id = uuid4()
    item_id = uuid4()
    await service.upsert_list(list_id, "Market")
    await service.upsert_item(
        UpsertItemInput(list_id=list_id, item_id=item_id, product_id=prod.id, quantity=Decimal("1"))
    )

    with pytest.raises(RateNotAvailableForDate):
        await service.buy_item(BuyItemInput(list_id=list_id, item_id=item_id))


# Rule 9: unbuy_item


async def test_unbuy_item_restores_previous_price_and_is_idempotent() -> None:
    prod = _product(last_price=Decimal("2.00"), last_price_currency=Currency.usd)
    service, _, _, catalog, _, _ = _make_service(products=[prod])
    list_id = uuid4()
    item_id = uuid4()
    await service.upsert_list(list_id, "Market")
    await service.upsert_item(
        UpsertItemInput(list_id=list_id, item_id=item_id, product_id=prod.id, quantity=Decimal("1"))
    )
    await service.buy_item(
        BuyItemInput(list_id=list_id, item_id=item_id, price=Decimal("2.50"), currency=Currency.usd)
    )

    unbought = await service.unbuy_item(list_id, item_id)
    assert unbought.item.status == ItemStatus.pending
    assert unbought.item.bought_price is None
    assert unbought.item.bought_currency is None
    assert unbought.item.bought_at is None
    assert catalog.products[prod.id].last_price == Decimal("2.00")

    retry = await service.unbuy_item(list_id, item_id)
    assert retry.item.status == ItemStatus.pending


async def test_unbuy_item_restores_null_when_no_prior_price() -> None:
    prod = _product(last_price=None, last_price_currency=None)
    service, _, _, catalog, _, _ = _make_service(products=[prod])
    list_id = uuid4()
    item_id = uuid4()
    await service.upsert_list(list_id, "Market")
    await service.upsert_item(
        UpsertItemInput(list_id=list_id, item_id=item_id, product_id=prod.id, quantity=Decimal("1"))
    )
    await service.buy_item(
        BuyItemInput(list_id=list_id, item_id=item_id, price=Decimal("3.00"), currency=Currency.usd)
    )

    unbought = await service.unbuy_item(list_id, item_id)
    assert unbought.item.status == ItemStatus.pending
    assert catalog.products[prod.id].last_price is None
    assert catalog.products[prod.id].last_price_currency is None


async def test_unbuy_item_raises_list_closed() -> None:
    now = datetime(2026, 11, 22, 14, 0, tzinfo=UTC)
    closed_list = ShoppingList(
        id=uuid4(),
        name="Market",
        status=ListStatus.closed,
        closed_at=now,
        expense_id=uuid4(),
        deleted_at=None,
        created_at=now,
        updated_at=now,
    )
    service, _, _, _, _, _ = _make_service(lists=[closed_list])

    with pytest.raises(ListClosed):
        await service.unbuy_item(closed_list.id, uuid4())


# Rule 10: list_totals


async def test_list_totals_with_mixed_currencies_unpriced_and_rounding() -> None:
    today = date(2026, 11, 22)
    bcv = {today: RateSnapshot(value=Decimal("100.00000000"), value_date=today)}
    usdt = {today: RateSnapshot(value=Decimal("120.00000000"), value_date=today)}
    p_usd = _product(name="Coffee", last_price=Decimal("2.50"), last_price_currency=Currency.usd)
    p_ves = _product(name="Bread", last_price=Decimal("150.00"), last_price_currency=Currency.ves)
    p_unpriced = _product(name="Mystery", last_price=None, last_price_currency=None)
    service, _, _, _, _, _ = _make_service(
        products=[p_usd, p_ves, p_unpriced],
        bcv_rates=bcv,
        usdt_rates=usdt,
    )
    list_id = uuid4()
    item_usd = uuid4()
    item_ves = uuid4()
    await service.upsert_list(list_id, "Market")
    await service.upsert_item(
        UpsertItemInput(
            list_id=list_id, item_id=item_usd, product_id=p_usd.id, quantity=Decimal("1.500")
        )
    )
    await service.upsert_item(
        UpsertItemInput(
            list_id=list_id, item_id=item_ves, product_id=p_ves.id, quantity=Decimal("2.000")
        )
    )
    await service.upsert_item(
        UpsertItemInput(
            list_id=list_id, item_id=uuid4(), product_id=p_unpriced.id, quantity=Decimal("1")
        )
    )
    # Buy only p_usd at 2.00 USD (2.00 * 1.5 * 100 = 300.00 VES)
    await service.buy_item(
        BuyItemInput(
            list_id=list_id, item_id=item_usd, price=Decimal("2.00"), currency=Currency.usd
        )
    )

    totals = await service.list_totals(list_id)

    # Estimated: p_usd updated to 2.00 * 1.5 * 100 = 300 + p_ves 150 * 2 = 300 -> 600.00 VES
    assert totals.estimated.unpriced_items == 1
    assert totals.estimated.total_ves == Decimal("600.00")
    assert totals.estimated.total_usd == Decimal("6.00")
    assert totals.estimated.usdt_to_sell == Decimal("5.00")

    assert totals.bought.total_ves == Decimal("300.00")
    assert totals.bought.total_usd == Decimal("3.00")
    assert totals.bought.usdt_to_sell == Decimal("2.50")


async def test_list_totals_raises_rate_not_available_on_empty_list() -> None:
    service, _, _, _, _, _ = _make_service(bcv_rates={}, usdt_rates={})
    list_id = uuid4()
    await service.upsert_list(list_id, "Empty")

    with pytest.raises(RateNotAvailableForDate):
        await service.list_totals(list_id)


# Rule 11: close_list


async def test_close_list_single_currency_records_plain_sum_and_ignores_pending() -> None:
    p1 = _product(name="Rice", last_price=Decimal("2.00"), last_price_currency=Currency.usd)
    p2 = _product(name="Oil", last_price=Decimal("4.00"), last_price_currency=Currency.usd)
    p3 = _product(name="Pending", last_price=Decimal("10.00"), last_price_currency=Currency.usd)
    service, _, _, _, expenses, clock = _make_service(products=[p1, p2, p3])
    list_id = uuid4()
    i1 = uuid4()
    i2 = uuid4()
    await service.upsert_list(list_id, "Groceries")
    await service.upsert_item(
        UpsertItemInput(list_id=list_id, item_id=i1, product_id=p1.id, quantity=Decimal("1.500"))
    )
    await service.upsert_item(
        UpsertItemInput(list_id=list_id, item_id=i2, product_id=p2.id, quantity=Decimal("1.000"))
    )
    await service.upsert_item(
        UpsertItemInput(
            list_id=list_id, item_id=uuid4(), product_id=p3.id, quantity=Decimal("1.000")
        )
    )
    await service.buy_item(BuyItemInput(list_id=list_id, item_id=i1))
    await service.buy_item(BuyItemInput(list_id=list_id, item_id=i2))

    cat_id = uuid4()
    closed = await service.close_list(CloseListInput(list_id=list_id, category_id=cat_id))

    assert closed.status == ListStatus.closed
    assert closed.closed_at == clock.current
    assert closed.expense_id is not None
    exp = expenses.recorded[closed.expense_id]
    assert exp.amount == Decimal("7.00")
    assert exp.currency == Currency.usd
    assert exp.spent_on == date(2026, 11, 22)
    assert exp.note == "Shopping list: Groceries"


async def test_close_list_mixed_currencies_converts_to_ves_on_spent_on() -> None:
    spent_on = date(2026, 11, 20)
    today = date(2026, 11, 22)
    bcv = {
        spent_on: RateSnapshot(value=Decimal("800.00000000"), value_date=spent_on),
        today: RateSnapshot(value=Decimal("900.00000000"), value_date=today),
    }
    usdt = {today: RateSnapshot(value=Decimal("950.00000000"), value_date=today)}
    p_usd = _product(name="Coffee")
    p_ves = _product(name="Bread")
    service, _, _, _, expenses, _ = _make_service(
        products=[p_usd, p_ves],
        bcv_rates=bcv,
        usdt_rates=usdt,
    )
    list_id = uuid4()
    i1 = uuid4()
    i2 = uuid4()
    await service.upsert_list(list_id, "Mixed")
    await service.upsert_item(
        UpsertItemInput(list_id=list_id, item_id=i1, product_id=p_usd.id, quantity=Decimal("1"))
    )
    await service.upsert_item(
        UpsertItemInput(list_id=list_id, item_id=i2, product_id=p_ves.id, quantity=Decimal("2"))
    )
    await service.buy_item(
        BuyItemInput(list_id=list_id, item_id=i1, price=Decimal("2.00"), currency=Currency.usd)
    )
    await service.buy_item(
        BuyItemInput(list_id=list_id, item_id=i2, price=Decimal("100.00"), currency=Currency.ves)
    )

    closed = await service.close_list(
        CloseListInput(list_id=list_id, category_id=uuid4(), spent_on=spent_on)
    )
    assert closed.expense_id is not None
    exp = expenses.recorded[closed.expense_id]
    assert exp.currency == Currency.ves
    assert exp.amount == Decimal("1800.00")


async def test_close_list_is_idempotent_on_already_closed_list() -> None:
    prod = _product()
    service, _, _, _, expenses, clock = _make_service(products=[prod])
    list_id = uuid4()
    item_id = uuid4()
    await service.upsert_list(list_id, "Market")
    await service.upsert_item(
        UpsertItemInput(list_id=list_id, item_id=item_id, product_id=prod.id, quantity=Decimal("1"))
    )
    await service.buy_item(BuyItemInput(list_id=list_id, item_id=item_id))

    first = await service.close_list(CloseListInput(list_id=list_id, category_id=uuid4()))
    clock.current += timedelta(hours=1)
    retry = await service.close_list(
        CloseListInput(list_id=list_id, category_id=uuid4(), spent_on=date(2026, 11, 1))
    )

    assert retry == first
    assert len(expenses.recorded) == 1


async def test_close_list_propagates_expense_error_and_leaves_list_open() -> None:
    prod = _product()
    service, list_repo, _, _, expenses, _ = _make_service(products=[prod])
    list_id = uuid4()
    item_id = uuid4()
    await service.upsert_list(list_id, "Market")
    await service.upsert_item(
        UpsertItemInput(list_id=list_id, item_id=item_id, product_id=prod.id, quantity=Decimal("1"))
    )
    await service.buy_item(BuyItemInput(list_id=list_id, item_id=item_id))
    expenses.error_to_raise = _FakeExpenseError("category archived")

    with pytest.raises(_FakeExpenseError):
        await service.close_list(CloseListInput(list_id=list_id, category_id=uuid4()))

    assert list_repo.items[list_id].status == ListStatus.open
    assert list_repo.items[list_id].expense_id is None


async def test_close_list_raises_nothing_bought() -> None:
    prod = _product()
    service, _, _, _, _, _ = _make_service(products=[prod])
    list_id = uuid4()
    await service.upsert_list(list_id, "Market")
    await service.upsert_item(
        UpsertItemInput(list_id=list_id, item_id=uuid4(), product_id=prod.id, quantity=Decimal("1"))
    )

    with pytest.raises(NothingBought):
        await service.close_list(CloseListInput(list_id=list_id, category_id=uuid4()))


async def test_close_list_raises_list_deleted() -> None:
    service, _, _, _, _, _ = _make_service()
    list_id = uuid4()
    await service.upsert_list(list_id, "Market")
    await service.delete_list(list_id)

    with pytest.raises(ListDeleted):
        await service.close_list(CloseListInput(list_id=list_id, category_id=uuid4()))


# Rule 13: recommendations


async def test_recommendations_computes_intervals_threshold_quantity_and_totals() -> None:
    now = datetime(2026, 11, 22, 14, 0, tzinfo=UTC)
    p_due = _product(name="Milk", last_price=Decimal("2.00"), last_price_currency=Currency.usd)
    p_not_due = _product(
        name="Coffee", last_price=Decimal("8.00"), last_price_currency=Currency.usd
    )
    p_archived = _product(
        name="Old",
        last_price=Decimal("1.00"),
        last_price_currency=Currency.usd,
        archived_at=now,
    )
    active_list = ShoppingList(
        id=uuid4(),
        name="Active",
        status=ListStatus.closed,
        closed_at=now,
        expense_id=uuid4(),
        deleted_at=None,
        created_at=now,
        updated_at=now,
    )
    deleted_list = ShoppingList(
        id=uuid4(),
        name="Deleted",
        status=ListStatus.open,
        closed_at=None,
        expense_id=None,
        deleted_at=now,
        created_at=now,
        updated_at=now,
    )
    # p_due: Nov 1, Nov 11 (deduped), Nov 18 -> avg 8.5 -> round 8 -> next_due Nov 26 (<= Nov 29)
    item_d1 = ShoppingListItem(
        id=uuid4(),
        list_id=active_list.id,
        product_id=p_due.id,
        quantity=Decimal("2.000"),
        status=ItemStatus.bought,
        bought_price=Decimal("2.00"),
        bought_currency=Currency.usd,
        bought_at=now,
        created_at=now,
        updated_at=now,
    )
    item_d2 = replace(item_d1, id=uuid4(), quantity=Decimal("3.000"))
    item_d3 = replace(item_d1, id=uuid4(), quantity=Decimal("4.000"))
    item_deleted = replace(item_d1, id=uuid4(), list_id=deleted_list.id, quantity=Decimal("99.000"))
    # p_not_due bought Nov 1, Nov 21 -> gap 20 -> next_due Dec 11 (> Nov 29)
    item_nd1 = replace(item_d1, id=uuid4(), product_id=p_not_due.id, quantity=Decimal("1.000"))
    item_nd2 = replace(item_d1, id=uuid4(), product_id=p_not_due.id, quantity=Decimal("1.000"))
    # p_archived
    item_arch1 = replace(item_d1, id=uuid4(), product_id=p_archived.id, quantity=Decimal("1.000"))
    item_arch2 = replace(item_d1, id=uuid4(), product_id=p_archived.id, quantity=Decimal("1.000"))

    refs = [
        PurchaseHistoryRef(p_due.id, date(2026, 11, 1), item_d1.id),
        PurchaseHistoryRef(p_due.id, date(2026, 11, 11), item_d2.id),
        PurchaseHistoryRef(p_due.id, date(2026, 11, 11), item_deleted.id),
        PurchaseHistoryRef(p_due.id, date(2026, 11, 18), item_d3.id),
        PurchaseHistoryRef(p_not_due.id, date(2026, 11, 1), item_nd1.id),
        PurchaseHistoryRef(p_not_due.id, date(2026, 11, 21), item_nd2.id),
        PurchaseHistoryRef(p_archived.id, date(2026, 11, 1), item_arch1.id),
        PurchaseHistoryRef(p_archived.id, date(2026, 11, 10), item_arch2.id),
    ]
    service, _, _, _, _, _ = _build_service(
        _ServiceSeed(
            now=now,
            lists=[active_list, deleted_list],
            items=[
                item_d1,
                item_d2,
                item_d3,
                item_deleted,
                item_nd1,
                item_nd2,
                item_arch1,
                item_arch2,
            ],
            products=[p_due, p_not_due, p_archived],
            purchase_refs=refs,
        )
    )

    result = await service.recommendations(days_ahead=7)

    assert len(result.items) == 1
    rec = result.items[0]
    assert rec.product_id == p_due.id
    assert rec.avg_interval_days == Decimal("8.5")
    assert rec.last_purchased_on == date(2026, 11, 18)
    assert rec.next_due == date(2026, 11, 26)
    assert rec.suggested_quantity == Decimal("3.000")
    assert result.totals.total_usd == Decimal("6.00")
    assert result.totals.total_ves == Decimal("5400.00")


async def test_recommendations_raises_invalid_days_ahead() -> None:
    service, _, _, _, _, _ = _make_service()

    with pytest.raises(InvalidDaysAhead):
        await service.recommendations(days_ahead=0)

    with pytest.raises(InvalidDaysAhead):
        await service.recommendations(days_ahead=61)


async def test_recommendations_raises_rate_not_available() -> None:
    service, _, _, _, _, _ = _make_service(bcv_rates={}, usdt_rates={})

    with pytest.raises(RateNotAvailableForDate):
        await service.recommendations()
