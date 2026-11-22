from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from app.clock import Clock

MIN_PRODUCT_NAME_LENGTH = 1
MAX_PRODUCT_NAME_LENGTH = 80


class Unit(StrEnum):
    """Supported measurement units for products."""

    unit = "unit"
    kg = "kg"
    l = "l"  # noqa: E741


class Currency(StrEnum):
    """Supported currencies for product prices."""

    usd = "usd"
    ves = "ves"


class PriceSource(StrEnum):
    """Origin of a historical product price entry."""

    manual = "manual"
    purchase = "purchase"


@dataclass(frozen=True, slots=True)
class Product:
    """Domain entity representing a catalog product."""

    id: UUID
    name: str
    unit: Unit
    last_price: Decimal | None
    last_price_currency: Currency | None
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class PriceEntry:
    """Domain entity representing a single historical price entry for a product."""

    id: UUID
    product_id: UUID
    price: Decimal
    currency: Currency
    recorded_on: date
    source: PriceSource
    list_item_id: UUID | None
    bcv_rate_value: Decimal
    bcv_rate_date: date
    usdt_rate_value: Decimal
    usdt_rate_date: date
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ProductInput:
    """Input payload for creating or updating a product."""

    id: UUID
    name: str
    unit: Unit
    last_price: Decimal | None = None
    last_price_currency: Currency | None = None


@dataclass(frozen=True, slots=True)
class UpsertProductResult:
    """Result of upserting a product, indicating whether it was created."""

    product: Product
    created: bool


@dataclass(frozen=True, slots=True)
class RateSnapshot:
    """Minimal exchange rate snapshot stored on a price entry."""

    value: Decimal
    value_date: date


@dataclass(frozen=True, slots=True)
class PurchasePriceInput:
    """Input payload for recording or correcting a purchase price from shopping."""

    product_id: UUID
    list_item_id: UUID
    price: Decimal
    currency: Currency
    recorded_on: date
    bcv_rate: RateSnapshot
    usdt_rate: RateSnapshot


@dataclass(frozen=True, slots=True)
class PurchaseEntryRef:
    """Reference to a purchase price entry on an active product for recommendations."""

    product_id: UUID
    recorded_on: date
    list_item_id: UUID


class RateLookup(Protocol):
    """Port for looking up BCV and USDT exchange rates in force on a given date."""

    async def bcv_in_force(self, on: date) -> RateSnapshot | None: ...

    async def usdt_in_force(self, on: date) -> RateSnapshot | None: ...


class ProductRepository(Protocol):
    """Persistence port for products."""

    async def get_by_id(self, product_id: UUID) -> Product | None: ...

    async def get_by_lower_name(self, name: str) -> Product | None: ...

    async def list_active(self) -> list[Product]: ...

    async def list_all(self) -> list[Product]: ...

    async def insert_if_absent(self, product: Product) -> tuple[Product, bool]: ...

    async def update(self, product: Product) -> Product: ...


class ProductPriceRepository(Protocol):
    """Persistence port for historical product price entries."""

    async def add(self, entry: PriceEntry) -> PriceEntry: ...

    async def get_by_list_item_id(self, list_item_id: UUID) -> PriceEntry | None: ...

    async def insert_purchase_if_absent(self, entry: PriceEntry) -> tuple[PriceEntry, bool]: ...

    async def update_purchase(self, entry: PriceEntry) -> PriceEntry: ...

    async def delete_by_list_item_id(self, list_item_id: UUID) -> bool: ...

    async def latest_for_product(self, product_id: UUID) -> PriceEntry | None: ...

    async def list_in_range(
        self,
        product_id: UUID,
        from_date: date,
        to_date: date,
    ) -> list[PriceEntry]: ...

    async def list_purchase_refs_for_active_products(self) -> list[PurchaseEntryRef]: ...


@dataclass(frozen=True, slots=True)
class ProductCollaborators:
    """Grouped collaborators injected into ProductsService."""

    products: ProductRepository
    prices: ProductPriceRepository
    rates: RateLookup
    clock: Clock
