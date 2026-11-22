from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.products.domain import (
    Currency,
    PriceEntry,
    PriceSource,
    Product,
    ProductCollaborators,
    ProductInput,
    PurchaseEntryRef,
    PurchasePriceInput,
    RateSnapshot,
    Unit,
)
from app.products.errors import (
    InvalidPricePeriod,
    InvalidProductName,
    InvalidProductPrice,
    ProductNameTaken,
    ProductNotFound,
    RateNotAvailableForDate,
)
from app.products.service import ProductsService


class _FakeClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def now(self) -> datetime:
        return self.current


class _InMemoryProductRepository:
    def __init__(self, initial: list[Product] | None = None) -> None:
        self.items: dict[UUID, Product] = {p.id: p for p in initial or []}
        self.insert_calls = 0
        self.update_calls = 0
        self.simulate_concurrent_existing = False

    async def get_by_id(self, product_id: UUID) -> Product | None:
        return self.items.get(product_id)

    async def get_by_lower_name(self, name: str) -> Product | None:
        target = name.lower()
        for product in self.items.values():
            if product.name.lower() == target:
                return product
        return None

    async def list_active(self) -> list[Product]:
        return sorted(
            (p for p in self.items.values() if p.archived_at is None),
            key=lambda p: (p.name, p.id),
        )

    async def list_all(self) -> list[Product]:
        return sorted(
            self.items.values(),
            key=lambda p: (p.archived_at is not None, p.name, p.id),
        )

    async def insert_if_absent(self, product: Product) -> tuple[Product, bool]:
        self.insert_calls += 1
        if self.simulate_concurrent_existing:
            self.simulate_concurrent_existing = False
            self.items[product.id] = product
            return product, False
        if product.id in self.items:
            return self.items[product.id], False
        existing_name = await self.get_by_lower_name(product.name)
        if existing_name is not None:
            raise ProductNameTaken(f"Product name {product.name!r} is already taken.")
        self.items[product.id] = product
        return product, True

    async def update(self, product: Product) -> Product:
        self.update_calls += 1
        existing_name = await self.get_by_lower_name(product.name)
        if existing_name is not None and existing_name.id != product.id:
            raise ProductNameTaken(f"Product name {product.name!r} is already taken.")
        self.items[product.id] = product
        return product


class _InMemoryProductPriceRepository:
    def __init__(
        self,
        products_repo: _InMemoryProductRepository,
        initial: list[PriceEntry] | None = None,
    ) -> None:
        self._products_repo = products_repo
        self.entries: dict[UUID, PriceEntry] = {entry.id: entry for entry in initial or []}

    async def add(self, entry: PriceEntry) -> PriceEntry:
        self.entries[entry.id] = entry
        return entry

    async def get_by_list_item_id(self, list_item_id: UUID) -> PriceEntry | None:
        for entry in self.entries.values():
            if entry.list_item_id == list_item_id:
                return entry
        return None

    async def insert_purchase_if_absent(self, entry: PriceEntry) -> tuple[PriceEntry, bool]:
        if entry.list_item_id is not None:
            existing = await self.get_by_list_item_id(entry.list_item_id)
            if existing is not None:
                return existing, False
        self.entries[entry.id] = entry
        return entry, True

    async def update_purchase(self, entry: PriceEntry) -> PriceEntry:
        self.entries[entry.id] = entry
        return entry

    async def delete_by_list_item_id(self, list_item_id: UUID) -> bool:
        existing = await self.get_by_list_item_id(list_item_id)
        if existing is None:
            return False
        del self.entries[existing.id]
        return True

    async def latest_for_product(self, product_id: UUID) -> PriceEntry | None:
        matching = [e for e in self.entries.values() if e.product_id == product_id]
        if not matching:
            return None
        matching.sort(
            key=lambda e: (-e.recorded_on.toordinal(), -e.created_at.timestamp(), e.id),
        )
        return matching[0]

    async def list_in_range(
        self,
        product_id: UUID,
        from_date: date,
        to_date: date,
    ) -> list[PriceEntry]:
        matching = [
            e
            for e in self.entries.values()
            if e.product_id == product_id and from_date <= e.recorded_on <= to_date
        ]
        matching.sort(
            key=lambda e: (-e.recorded_on.toordinal(), -e.created_at.timestamp(), e.id),
        )
        return matching

    async def list_purchase_refs_for_active_products(self) -> list[PurchaseEntryRef]:
        return [
            PurchaseEntryRef(
                product_id=e.product_id,
                recorded_on=e.recorded_on,
                list_item_id=e.list_item_id,
            )
            for e in self.entries.values()
            if e.source == PriceSource.purchase
            and e.list_item_id is not None
            and self._products_repo.items[e.product_id].archived_at is None
        ]


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


def _make_service(
    *,
    now: datetime | None = None,
    products: list[Product] | None = None,
    entries: list[PriceEntry] | None = None,
    bcv_rates: dict[date, RateSnapshot] | None = None,
    usdt_rates: dict[date, RateSnapshot] | None = None,
) -> tuple[
    ProductsService,
    _InMemoryProductRepository,
    _InMemoryProductPriceRepository,
    _FakeClock,
]:
    default_bcv, default_usdt = _default_rates()
    clock = _FakeClock(now or datetime(2026, 11, 22, 14, 0, tzinfo=UTC))
    prod_repo = _InMemoryProductRepository(products)
    price_repo = _InMemoryProductPriceRepository(prod_repo, entries)
    rates = _FakeRateLookup(
        bcv_rates=default_bcv if bcv_rates is None else bcv_rates,
        usdt_rates=default_usdt if usdt_rates is None else usdt_rates,
    )
    service = ProductsService(
        ProductCollaborators(
            products=prod_repo,
            prices=price_repo,
            rates=rates,
            clock=clock,
        )
    )
    return service, prod_repo, price_repo, clock


async def test_upsert_product_creates_without_price() -> None:
    service, _, price_repo, clock = _make_service(bcv_rates={}, usdt_rates={})
    product_id = uuid4()

    result = await service.upsert_product(
        ProductInput(id=product_id, name="  Olive Oil  ", unit=Unit.l)
    )

    assert result.created is True
    assert result.product.id == product_id
    assert result.product.name == "Olive Oil"
    assert result.product.unit == Unit.l
    assert result.product.last_price is None
    assert result.product.last_price_currency is None
    assert result.product.created_at == clock.current
    assert result.product.updated_at == clock.current
    assert price_repo.entries == {}


async def test_upsert_product_creates_with_price_and_appends_manual_entry() -> None:
    service, _, price_repo, _ = _make_service()
    product_id = uuid4()

    result = await service.upsert_product(
        ProductInput(
            id=product_id,
            name="Rice",
            unit=Unit.kg,
            last_price=Decimal("2.345"),
            last_price_currency=Currency.usd,
        )
    )

    assert result.created is True
    assert result.product.last_price == Decimal("2.34")
    assert result.product.last_price_currency == Currency.usd
    assert len(price_repo.entries) == 1
    entry = next(iter(price_repo.entries.values()))
    assert entry.product_id == product_id
    assert entry.price == Decimal("2.34")
    assert entry.currency == Currency.usd
    assert entry.source == PriceSource.manual
    assert entry.list_item_id is None
    assert entry.recorded_on == date(2026, 11, 22)
    assert entry.bcv_rate_value == Decimal("900.00000000")
    assert entry.usdt_rate_value == Decimal("950.00000000")


async def test_upsert_product_identical_retry_adds_no_entry_and_no_bump() -> None:
    service, prod_repo, price_repo, clock = _make_service()
    product_id = uuid4()
    await service.upsert_product(
        ProductInput(
            id=product_id,
            name="Coffee",
            unit=Unit.kg,
            last_price=Decimal("8.50"),
            last_price_currency=Currency.usd,
        )
    )
    initial_updates = prod_repo.update_calls

    clock.current += timedelta(minutes=10)
    retry = await service.upsert_product(
        ProductInput(
            id=product_id,
            name="  Coffee ",
            unit=Unit.kg,
            last_price=Decimal("8.500"),
            last_price_currency=Currency.usd,
        )
    )

    assert retry.created is False
    assert retry.product.updated_at == datetime(2026, 11, 22, 14, 0, tzinfo=UTC)
    assert prod_repo.update_calls == initial_updates
    assert len(price_repo.entries) == 1


async def test_upsert_product_price_change_appends_one_manual_entry() -> None:
    service, _, price_repo, clock = _make_service()
    product_id = uuid4()
    await service.upsert_product(
        ProductInput(
            id=product_id,
            name="Milk",
            unit=Unit.l,
            last_price=Decimal("1.50"),
            last_price_currency=Currency.usd,
        )
    )

    clock.current += timedelta(minutes=5)
    updated = await service.upsert_product(
        ProductInput(
            id=product_id,
            name="Milk",
            unit=Unit.l,
            last_price=Decimal("1.75"),
            last_price_currency=Currency.usd,
        )
    )

    assert updated.created is False
    assert updated.product.last_price == Decimal("1.75")
    assert updated.product.updated_at == clock.current
    assert len(price_repo.entries) == 2


async def test_upsert_product_omitted_price_leaves_stored_price_untouched() -> None:
    service, _, price_repo, clock = _make_service()
    product_id = uuid4()
    await service.upsert_product(
        ProductInput(
            id=product_id,
            name="Pasta",
            unit=Unit.kg,
            last_price=Decimal("2.10"),
            last_price_currency=Currency.usd,
        )
    )

    clock.current += timedelta(minutes=5)
    renamed = await service.upsert_product(
        ProductInput(
            id=product_id,
            name="Whole Pasta",
            unit=Unit.kg,
            last_price=None,
            last_price_currency=None,
        )
    )

    assert renamed.product.name == "Whole Pasta"
    assert renamed.product.last_price == Decimal("2.10")
    assert renamed.product.last_price_currency == Currency.usd
    assert len(price_repo.entries) == 1


async def test_upsert_product_allows_case_only_rename_on_same_id() -> None:
    service, _, _, _ = _make_service()
    product_id = uuid4()
    await service.upsert_product(ProductInput(id=product_id, name="sugar", unit=Unit.kg))

    renamed = await service.upsert_product(ProductInput(id=product_id, name="Sugar", unit=Unit.kg))

    assert renamed.created is False
    assert renamed.product.name == "Sugar"


async def test_upsert_product_handles_concurrent_insert_idempotently() -> None:
    service, prod_repo, _, _ = _make_service()
    prod_repo.simulate_concurrent_existing = True
    product_id = uuid4()

    result = await service.upsert_product(ProductInput(id=product_id, name="Salt", unit=Unit.kg))

    assert result.created is False
    assert result.product.id == product_id


async def test_upsert_product_raises_invalid_product_name() -> None:
    service, _, _, _ = _make_service()

    with pytest.raises(InvalidProductName):
        await service.upsert_product(ProductInput(id=uuid4(), name="   ", unit=Unit.unit))

    with pytest.raises(InvalidProductName):
        await service.upsert_product(ProductInput(id=uuid4(), name="x" * 81, unit=Unit.unit))


async def test_upsert_product_raises_invalid_product_price_when_non_positive() -> None:
    service, _, _, _ = _make_service()

    with pytest.raises(InvalidProductPrice):
        await service.upsert_product(
            ProductInput(
                id=uuid4(),
                name="Bread",
                unit=Unit.unit,
                last_price=Decimal("0"),
                last_price_currency=Currency.usd,
            )
        )


async def test_upsert_product_raises_invalid_product_price_when_pair_incomplete() -> None:
    service, _, _, _ = _make_service()

    with pytest.raises(InvalidProductPrice):
        await service.upsert_product(
            ProductInput(
                id=uuid4(),
                name="Bread",
                unit=Unit.unit,
                last_price=Decimal("2.00"),
                last_price_currency=None,
            )
        )


async def test_upsert_product_raises_product_name_taken() -> None:
    service, _, _, _ = _make_service()
    await service.upsert_product(ProductInput(id=uuid4(), name="Flour", unit=Unit.kg))

    with pytest.raises(ProductNameTaken):
        await service.upsert_product(ProductInput(id=uuid4(), name="flour", unit=Unit.kg))


async def test_upsert_product_raises_rate_not_available_when_bcv_missing() -> None:
    _, usdt = _default_rates()
    service, _, _, _ = _make_service(bcv_rates={}, usdt_rates=usdt)

    with pytest.raises(RateNotAvailableForDate):
        await service.upsert_product(
            ProductInput(
                id=uuid4(),
                name="Eggs",
                unit=Unit.unit,
                last_price=Decimal("3.00"),
                last_price_currency=Currency.usd,
            )
        )


async def test_upsert_product_raises_rate_not_available_when_usdt_missing() -> None:
    bcv, _ = _default_rates()
    service, _, _, _ = _make_service(bcv_rates=bcv, usdt_rates={})

    with pytest.raises(RateNotAvailableForDate):
        await service.upsert_product(
            ProductInput(
                id=uuid4(),
                name="Eggs",
                unit=Unit.unit,
                last_price=Decimal("3.00"),
                last_price_currency=Currency.usd,
            )
        )


async def test_get_product_returns_existing_product() -> None:
    service, _, _, _ = _make_service()
    product_id = uuid4()
    await service.upsert_product(ProductInput(id=product_id, name="Tea", unit=Unit.unit))

    found = await service.get_product(product_id)

    assert found.id == product_id
    assert found.name == "Tea"


async def test_get_product_raises_product_not_found() -> None:
    service, _, _, _ = _make_service()

    with pytest.raises(ProductNotFound):
        await service.get_product(uuid4())


async def test_list_active_products_excludes_archived() -> None:
    service, _, _, _ = _make_service()
    active_id = uuid4()
    archived_id = uuid4()
    await service.upsert_product(ProductInput(id=active_id, name="Apples", unit=Unit.kg))
    await service.upsert_product(ProductInput(id=archived_id, name="Bananas", unit=Unit.kg))
    await service.archive_product(archived_id)

    active = await service.list_active_products()

    assert [p.id for p in active] == [active_id]


async def test_list_all_products_orders_active_first_then_archived() -> None:
    service, _, _, _ = _make_service()
    archived_id = uuid4()
    active_id = uuid4()
    await service.upsert_product(ProductInput(id=archived_id, name="Apples", unit=Unit.kg))
    await service.upsert_product(ProductInput(id=active_id, name="Zucchini", unit=Unit.kg))
    await service.archive_product(archived_id)

    all_products = await service.list_all_products()

    assert [p.id for p in all_products] == [active_id, archived_id]


async def test_archive_product_sets_archived_at_and_is_idempotent() -> None:
    service, prod_repo, _, clock = _make_service()
    product_id = uuid4()
    await service.upsert_product(ProductInput(id=product_id, name="Butter", unit=Unit.unit))

    clock.current += timedelta(minutes=5)
    archived = await service.archive_product(product_id)
    assert archived.archived_at == clock.current
    assert archived.updated_at == clock.current
    updates_after_archive = prod_repo.update_calls

    clock.current += timedelta(minutes=5)
    retry = await service.archive_product(product_id)
    assert retry.updated_at == archived.updated_at
    assert prod_repo.update_calls == updates_after_archive


async def test_archive_product_raises_product_not_found() -> None:
    service, _, _, _ = _make_service()

    with pytest.raises(ProductNotFound):
        await service.archive_product(uuid4())


async def test_unarchive_product_clears_archived_at_and_is_idempotent() -> None:
    service, prod_repo, _, clock = _make_service()
    product_id = uuid4()
    await service.upsert_product(ProductInput(id=product_id, name="Cheese", unit=Unit.kg))
    await service.archive_product(product_id)

    clock.current += timedelta(minutes=5)
    unarchived = await service.unarchive_product(product_id)
    assert unarchived.archived_at is None
    assert unarchived.updated_at == clock.current
    updates_after_unarchive = prod_repo.update_calls

    clock.current += timedelta(minutes=5)
    retry = await service.unarchive_product(product_id)
    assert retry.updated_at == unarchived.updated_at
    assert prod_repo.update_calls == updates_after_unarchive


async def test_unarchive_product_raises_product_not_found() -> None:
    service, _, _, _ = _make_service()

    with pytest.raises(ProductNotFound):
        await service.unarchive_product(uuid4())


async def test_price_history_orders_by_recorded_on_desc_created_at_desc_id_asc() -> None:
    product_id = uuid4()
    id_low = UUID("00000000-0000-0000-0000-000000000001")
    id_high = UUID("00000000-0000-0000-0000-000000000002")
    ts_old = datetime(2026, 11, 20, 10, 0, tzinfo=UTC)
    ts_same = datetime(2026, 11, 22, 10, 0, tzinfo=UTC)
    ts_new = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
    entries = [
        PriceEntry(
            id=id_high,
            product_id=product_id,
            price=Decimal("1.00"),
            currency=Currency.usd,
            recorded_on=date(2026, 11, 22),
            source=PriceSource.manual,
            list_item_id=None,
            bcv_rate_value=Decimal("900.00000000"),
            bcv_rate_date=date(2026, 11, 22),
            usdt_rate_value=Decimal("950.00000000"),
            usdt_rate_date=date(2026, 11, 22),
            created_at=ts_same,
        ),
        PriceEntry(
            id=id_low,
            product_id=product_id,
            price=Decimal("2.00"),
            currency=Currency.usd,
            recorded_on=date(2026, 11, 22),
            source=PriceSource.manual,
            list_item_id=None,
            bcv_rate_value=Decimal("900.00000000"),
            bcv_rate_date=date(2026, 11, 22),
            usdt_rate_value=Decimal("950.00000000"),
            usdt_rate_date=date(2026, 11, 22),
            created_at=ts_same,
        ),
        PriceEntry(
            id=uuid4(),
            product_id=product_id,
            price=Decimal("3.00"),
            currency=Currency.usd,
            recorded_on=date(2026, 11, 22),
            source=PriceSource.manual,
            list_item_id=None,
            bcv_rate_value=Decimal("900.00000000"),
            bcv_rate_date=date(2026, 11, 22),
            usdt_rate_value=Decimal("950.00000000"),
            usdt_rate_date=date(2026, 11, 22),
            created_at=ts_new,
        ),
        PriceEntry(
            id=uuid4(),
            product_id=product_id,
            price=Decimal("4.00"),
            currency=Currency.usd,
            recorded_on=date(2026, 11, 20),
            source=PriceSource.manual,
            list_item_id=None,
            bcv_rate_value=Decimal("900.00000000"),
            bcv_rate_date=date(2026, 11, 20),
            usdt_rate_value=Decimal("950.00000000"),
            usdt_rate_date=date(2026, 11, 20),
            created_at=ts_old,
        ),
    ]
    product = Product(
        id=product_id,
        name="Beans",
        unit=Unit.kg,
        last_price=Decimal("3.00"),
        last_price_currency=Currency.usd,
        archived_at=None,
        created_at=ts_old,
        updated_at=ts_new,
    )
    service, _, _, _ = _make_service(products=[product], entries=entries)

    history = await service.price_history(product_id, date(2026, 11, 20), date(2026, 11, 22))

    assert [e.price for e in history] == [
        Decimal("3.00"),
        Decimal("2.00"),
        Decimal("1.00"),
        Decimal("4.00"),
    ]


async def test_price_history_raises_invalid_price_period() -> None:
    service, _, _, _ = _make_service()

    with pytest.raises(InvalidPricePeriod):
        await service.price_history(uuid4(), date(2026, 11, 22), date(2026, 11, 20))


async def test_price_history_raises_product_not_found() -> None:
    service, _, _, _ = _make_service()

    with pytest.raises(ProductNotFound):
        await service.price_history(uuid4(), date(2026, 11, 20), date(2026, 11, 22))


async def test_record_purchase_price_creates_and_corrects_same_entry() -> None:
    service, _, price_repo, clock = _make_service()
    product_id = uuid4()
    item_id = uuid4()
    await service.upsert_product(ProductInput(id=product_id, name="Chicken", unit=Unit.kg))
    bcv = RateSnapshot(value=Decimal("900.00000000"), value_date=date(2026, 11, 22))
    usdt = RateSnapshot(value=Decimal("950.00000000"), value_date=date(2026, 11, 22))

    clock.current += timedelta(minutes=1)
    updated = await service.record_purchase_price(
        PurchasePriceInput(
            product_id=product_id,
            list_item_id=item_id,
            price=Decimal("4.50"),
            currency=Currency.usd,
            recorded_on=date(2026, 11, 22),
            bcv_rate=bcv,
            usdt_rate=usdt,
        )
    )
    assert updated.last_price == Decimal("4.50")
    assert len(price_repo.entries) == 1
    first_entry_id = next(iter(price_repo.entries))

    clock.current += timedelta(minutes=2)
    corrected = await service.record_purchase_price(
        PurchasePriceInput(
            product_id=product_id,
            list_item_id=item_id,
            price=Decimal("4500.00"),
            currency=Currency.ves,
            recorded_on=date(2026, 11, 22),
            bcv_rate=bcv,
            usdt_rate=usdt,
        )
    )
    assert corrected.last_price == Decimal("4500.00")
    assert corrected.last_price_currency == Currency.ves
    assert len(price_repo.entries) == 1
    assert next(iter(price_repo.entries)) == first_entry_id


async def test_delete_purchase_price_restores_previous_last_price() -> None:
    service, _, price_repo, clock = _make_service()
    product_id = uuid4()
    item_id = uuid4()
    bcv = RateSnapshot(value=Decimal("900.00000000"), value_date=date(2026, 11, 22))
    usdt = RateSnapshot(value=Decimal("950.00000000"), value_date=date(2026, 11, 22))

    await service.upsert_product(
        ProductInput(
            id=product_id,
            name="Yogurt",
            unit=Unit.unit,
            last_price=Decimal("2.00"),
            last_price_currency=Currency.usd,
        )
    )
    clock.current += timedelta(minutes=5)
    await service.record_purchase_price(
        PurchasePriceInput(
            product_id=product_id,
            list_item_id=item_id,
            price=Decimal("2.50"),
            currency=Currency.usd,
            recorded_on=date(2026, 11, 22),
            bcv_rate=bcv,
            usdt_rate=usdt,
        )
    )

    clock.current += timedelta(minutes=5)
    restored = await service.delete_purchase_price_and_restore(product_id, item_id)
    assert restored.last_price == Decimal("2.00")
    assert restored.last_price_currency == Currency.usd
    assert len(price_repo.entries) == 1


async def test_delete_purchase_price_restores_null_when_no_remaining_entry() -> None:
    service, _, price_repo, clock = _make_service()
    product_id = uuid4()
    item_id = uuid4()
    bcv = RateSnapshot(value=Decimal("900.00000000"), value_date=date(2026, 11, 22))
    usdt = RateSnapshot(value=Decimal("950.00000000"), value_date=date(2026, 11, 22))

    await service.upsert_product(ProductInput(id=product_id, name="Water", unit=Unit.l))
    clock.current += timedelta(minutes=5)
    await service.record_purchase_price(
        PurchasePriceInput(
            product_id=product_id,
            list_item_id=item_id,
            price=Decimal("1.00"),
            currency=Currency.usd,
            recorded_on=date(2026, 11, 22),
            bcv_rate=bcv,
            usdt_rate=usdt,
        )
    )
    clock.current += timedelta(minutes=5)
    cleared = await service.delete_purchase_price_and_restore(product_id, item_id)
    assert cleared.last_price is None
    assert cleared.last_price_currency is None
    assert price_repo.entries == {}


async def test_record_purchase_price_correcting_older_purchase_keeps_newer_last_price() -> None:
    service, prod_repo, _, clock = _make_service(now=datetime(2026, 11, 15, 14, 0, tzinfo=UTC))
    product_id = uuid4()
    older_item_id = uuid4()
    newer_item_id = uuid4()
    bcv_old = RateSnapshot(value=Decimal("880.00000000"), value_date=date(2026, 11, 15))
    usdt_old = RateSnapshot(value=Decimal("930.00000000"), value_date=date(2026, 11, 15))
    bcv_new = RateSnapshot(value=Decimal("900.00000000"), value_date=date(2026, 11, 22))
    usdt_new = RateSnapshot(value=Decimal("950.00000000"), value_date=date(2026, 11, 22))

    await service.upsert_product(ProductInput(id=product_id, name="Pasta", unit=Unit.kg))
    await service.record_purchase_price(
        PurchasePriceInput(
            product_id=product_id,
            list_item_id=older_item_id,
            price=Decimal("10.00"),
            currency=Currency.usd,
            recorded_on=date(2026, 11, 15),
            bcv_rate=bcv_old,
            usdt_rate=usdt_old,
        )
    )

    clock.current = datetime(2026, 11, 22, 14, 0, tzinfo=UTC)
    after_newer = await service.record_purchase_price(
        PurchasePriceInput(
            product_id=product_id,
            list_item_id=newer_item_id,
            price=Decimal("12.00"),
            currency=Currency.usd,
            recorded_on=date(2026, 11, 22),
            bcv_rate=bcv_new,
            usdt_rate=usdt_new,
        )
    )
    assert after_newer.last_price == Decimal("12.00")
    updates_before_correction = prod_repo.update_calls

    clock.current = datetime(2026, 11, 22, 15, 0, tzinfo=UTC)
    after_correction = await service.record_purchase_price(
        PurchasePriceInput(
            product_id=product_id,
            list_item_id=older_item_id,
            price=Decimal("11.00"),
            currency=Currency.usd,
            recorded_on=date(2026, 11, 15),
            bcv_rate=bcv_old,
            usdt_rate=usdt_old,
        )
    )

    assert after_correction.last_price == Decimal("12.00")
    assert after_correction.last_price_currency == Currency.usd
    assert after_correction.updated_at == after_newer.updated_at
    assert prod_repo.update_calls == updates_before_correction


async def test_record_purchase_price_correcting_most_recent_purchase_updates_last_price() -> None:
    service, _, _, clock = _make_service(now=datetime(2026, 11, 15, 14, 0, tzinfo=UTC))
    product_id = uuid4()
    older_item_id = uuid4()
    newer_item_id = uuid4()
    bcv_old = RateSnapshot(value=Decimal("880.00000000"), value_date=date(2026, 11, 15))
    usdt_old = RateSnapshot(value=Decimal("930.00000000"), value_date=date(2026, 11, 15))
    bcv_new = RateSnapshot(value=Decimal("900.00000000"), value_date=date(2026, 11, 22))
    usdt_new = RateSnapshot(value=Decimal("950.00000000"), value_date=date(2026, 11, 22))

    await service.upsert_product(ProductInput(id=product_id, name="Oil", unit=Unit.l))
    await service.record_purchase_price(
        PurchasePriceInput(
            product_id=product_id,
            list_item_id=older_item_id,
            price=Decimal("10.00"),
            currency=Currency.usd,
            recorded_on=date(2026, 11, 15),
            bcv_rate=bcv_old,
            usdt_rate=usdt_old,
        )
    )

    clock.current = datetime(2026, 11, 22, 14, 0, tzinfo=UTC)
    await service.record_purchase_price(
        PurchasePriceInput(
            product_id=product_id,
            list_item_id=newer_item_id,
            price=Decimal("12.00"),
            currency=Currency.usd,
            recorded_on=date(2026, 11, 22),
            bcv_rate=bcv_new,
            usdt_rate=usdt_new,
        )
    )

    clock.current = datetime(2026, 11, 22, 15, 0, tzinfo=UTC)
    corrected = await service.record_purchase_price(
        PurchasePriceInput(
            product_id=product_id,
            list_item_id=newer_item_id,
            price=Decimal("13.50"),
            currency=Currency.usd,
            recorded_on=date(2026, 11, 22),
            bcv_rate=bcv_new,
            usdt_rate=usdt_new,
        )
    )

    assert corrected.last_price == Decimal("13.50")
    assert corrected.last_price_currency == Currency.usd
    assert corrected.updated_at == clock.current
