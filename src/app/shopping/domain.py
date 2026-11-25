from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from app.clock import Clock

MIN_LIST_NAME_LENGTH = 1
MAX_LIST_NAME_LENGTH = 80
DEFAULT_RECOMMENDATION_DAYS_AHEAD = 7
MIN_RECOMMENDATION_DAYS_AHEAD = 1
MAX_RECOMMENDATION_DAYS_AHEAD = 60
MIN_RECOMMENDATION_PURCHASE_DATES = 2
SHOPPING_EXPENSE_NAMESPACE = UUID("6f3e2c7d-9b5a-4e82-8f1c-3d4e5a6b7c8d")


class Currency(StrEnum):
    """Supported currencies for shopping items and totals."""

    usd = "usd"
    ves = "ves"


class Unit(StrEnum):
    """Supported measurement units for catalog products."""

    unit = "unit"
    kg = "kg"
    l = "l"  # noqa: E741


class ListStatus(StrEnum):
    """Lifecycle status of a shopping list."""

    open = "open"
    closed = "closed"


class ItemStatus(StrEnum):
    """Lifecycle status of a shopping list item."""

    pending = "pending"
    bought = "bought"


@dataclass(frozen=True, slots=True)
class ShoppingList:
    """Domain entity representing a shopping list."""

    id: UUID
    name: str
    status: ListStatus
    closed_at: datetime | None
    expense_id: UUID | None
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ShoppingListItem:
    """Domain entity representing an item on a shopping list."""

    id: UUID
    list_id: UUID
    product_id: UUID
    quantity: Decimal
    status: ItemStatus
    bought_price: Decimal | None
    bought_currency: Currency | None
    bought_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ProductSummary:
    """Minimal catalog product view exposed to shopping through ProductCatalog."""

    id: UUID
    name: str
    unit: Unit
    last_price: Decimal | None
    last_price_currency: Currency | None
    archived_at: datetime | None


@dataclass(frozen=True, slots=True)
class ShoppingListItemDetail:
    """Shopping list item enriched with current product metadata."""

    item: ShoppingListItem
    product_name: str
    product_unit: Unit
    product_last_price: Decimal | None
    product_last_price_currency: Currency | None


@dataclass(frozen=True, slots=True)
class ShoppingListWithItems:
    """Shopping list paired with its ordered items."""

    shopping_list: ShoppingList
    items: list[ShoppingListItemDetail]


@dataclass(frozen=True, slots=True)
class RateSnapshot:
    """Minimal exchange rate snapshot used in calculations and responses."""

    value: Decimal
    value_date: date


@dataclass(frozen=True, slots=True)
class EstimatedTotals:
    """Estimated totals for priced items plus unpriced item count."""

    total_ves: Decimal
    total_usd: Decimal
    usdt_to_sell: Decimal
    unpriced_items: int


@dataclass(frozen=True, slots=True)
class BoughtTotals:
    """Actual totals for bought items."""

    total_ves: Decimal
    total_usd: Decimal
    usdt_to_sell: Decimal


@dataclass(frozen=True, slots=True)
class ListTotals:
    """Estimated and bought totals for a shopping list with today's rates."""

    estimated: EstimatedTotals
    bought: BoughtTotals
    bcv_rate: RateSnapshot
    usdt_rate: RateSnapshot


@dataclass(frozen=True, slots=True)
class RecommendationItem:
    """Recommended product to buy based on historical purchase intervals."""

    product_id: UUID
    product_name: str
    unit: Unit
    last_price: Decimal | None
    last_price_currency: Currency | None
    avg_interval_days: Decimal
    last_purchased_on: date
    next_due: date
    suggested_quantity: Decimal


@dataclass(frozen=True, slots=True)
class RecommendationTotals:
    """Estimated totals for the recommended product set with today's rates."""

    total_ves: Decimal
    total_usd: Decimal
    usdt_to_sell: Decimal
    unpriced_items: int
    bcv_rate: RateSnapshot
    usdt_rate: RateSnapshot


@dataclass(frozen=True, slots=True)
class RecommendationsResult:
    """Recommended items ordered by due date together with their estimated totals."""

    items: list[RecommendationItem]
    totals: RecommendationTotals


@dataclass(frozen=True, slots=True)
class UpsertListResult:
    """Result of upserting a shopping list, indicating whether it was created."""

    shopping_list: ShoppingList
    created: bool


@dataclass(frozen=True, slots=True)
class UpsertItemInput:
    """Input payload for adding or updating an item on a shopping list."""

    list_id: UUID
    item_id: UUID
    product_id: UUID
    quantity: Decimal


@dataclass(frozen=True, slots=True)
class UpsertItemResult:
    """Result of upserting a list item, indicating whether it was created."""

    item: ShoppingListItemDetail
    created: bool


@dataclass(frozen=True, slots=True)
class BuyItemInput:
    """Input payload for buying or correcting a shopping list item."""

    list_id: UUID
    item_id: UUID
    price: Decimal | None = None
    currency: Currency | None = None


@dataclass(frozen=True, slots=True)
class CloseListInput:
    """Input payload for closing a shopping list and recording its expense."""

    list_id: UUID
    category_id: UUID
    spent_on: date | None = None


@dataclass(frozen=True, slots=True)
class RecordPurchaseInput:
    """Cross-domain payload passed to ProductCatalog when buying an item."""

    product_id: UUID
    list_item_id: UUID
    price: Decimal
    currency: Currency
    recorded_on: date
    bcv_rate: RateSnapshot
    usdt_rate: RateSnapshot


@dataclass(frozen=True, slots=True)
class PurchaseHistoryRef:
    """Minimal reference to a purchase entry returned by ProductCatalog."""

    product_id: UUID
    recorded_on: date
    list_item_id: UUID


@dataclass(frozen=True, slots=True)
class RecordExpenseInput:
    """Cross-domain payload passed to ExpenseRecorder when closing a list."""

    expense_id: UUID
    category_id: UUID
    amount: Decimal
    currency: Currency
    spent_on: date
    note: str


class RateLookup(Protocol):
    """Port for looking up BCV and USDT exchange rates in force on a given date."""

    async def bcv_in_force(self, on: date) -> RateSnapshot | None: ...

    async def usdt_in_force(self, on: date) -> RateSnapshot | None: ...


class ProductCatalog(Protocol):
    """Cross-domain port wrapping ProductsService operations used by shopping."""

    async def get_product(self, product_id: UUID) -> ProductSummary | None: ...

    async def list_all_products(self) -> list[ProductSummary]: ...

    async def record_purchase_price(self, payload: RecordPurchaseInput) -> ProductSummary: ...

    async def delete_purchase_price_and_restore(
        self,
        product_id: UUID,
        list_item_id: UUID,
    ) -> ProductSummary: ...

    async def list_purchase_refs_for_active_products(self) -> list[PurchaseHistoryRef]: ...


class ExpenseRecorder(Protocol):
    """Cross-domain port wrapping ExpenseService operations used when closing a list."""

    async def upsert_expense(self, payload: RecordExpenseInput) -> UUID: ...


class ShoppingListRepository(Protocol):
    """Persistence port for shopping lists."""

    async def get_by_id(self, list_id: UUID) -> ShoppingList | None: ...

    async def list_non_deleted(self, status: ListStatus | None = None) -> list[ShoppingList]: ...

    async def insert_if_absent(self, shopping_list: ShoppingList) -> tuple[ShoppingList, bool]: ...

    async def update(self, shopping_list: ShoppingList) -> ShoppingList: ...

    async def list_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[ShoppingList]: ...


class ShoppingListItemRepository(Protocol):
    """Persistence port for shopping list items."""

    async def get_by_id(self, item_id: UUID) -> ShoppingListItem | None: ...

    async def get_by_list_and_product(
        self,
        list_id: UUID,
        product_id: UUID,
    ) -> ShoppingListItem | None: ...

    async def list_for_list(self, list_id: UUID) -> list[ShoppingListItem]: ...

    async def insert_if_absent(self, item: ShoppingListItem) -> tuple[ShoppingListItem, bool]: ...

    async def update(self, item: ShoppingListItem) -> ShoppingListItem: ...

    async def delete_by_id(self, item_id: UUID) -> None: ...

    async def list_non_deleted_item_ids(self) -> set[UUID]: ...

    async def list_bought_quantities_by_product(self) -> dict[UUID, list[Decimal]]: ...

    async def list_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[ShoppingListItem]: ...


@dataclass(frozen=True, slots=True)
class ShoppingCollaborators:
    """Grouped collaborators injected into ShoppingService."""

    lists: ShoppingListRepository
    items: ShoppingListItemRepository
    products: ProductCatalog
    expenses: ExpenseRecorder
    rates: RateLookup
    clock: Clock
