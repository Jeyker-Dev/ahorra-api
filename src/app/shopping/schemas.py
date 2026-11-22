from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, PlainSerializer

from app.shopping.domain import (
    DEFAULT_RECOMMENDATION_DAYS_AHEAD,
    BoughtTotals,
    Currency,
    EstimatedTotals,
    ItemStatus,
    ListStatus,
    ListTotals,
    RateSnapshot,
    RecommendationItem,
    RecommendationsResult,
    RecommendationTotals,
    ShoppingList,
    ShoppingListItemDetail,
    ShoppingListWithItems,
    Unit,
)

DecimalStr = Annotated[
    Decimal,
    PlainSerializer(str, return_type=str, when_used="json"),
]


class ShoppingListUpsertIn(BaseModel):
    """Request body for creating or renaming a shopping list."""

    name: str


class ShoppingListQuery(BaseModel):
    """Query parameters for listing non-deleted shopping lists."""

    status: ListStatus | None = None


class ShoppingListOut(BaseModel):
    """Public HTTP representation of a shopping list."""

    id: UUID
    name: str
    status: ListStatus
    closed_at: datetime | None
    expense_id: UUID | None
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, shopping_list: ShoppingList) -> Self:
        """Build an HTTP response model from a domain ShoppingList."""
        return cls(
            id=shopping_list.id,
            name=shopping_list.name,
            status=shopping_list.status,
            closed_at=shopping_list.closed_at,
            expense_id=shopping_list.expense_id,
            deleted_at=shopping_list.deleted_at,
            created_at=shopping_list.created_at,
            updated_at=shopping_list.updated_at,
        )


class ListItemUpsertIn(BaseModel):
    """Request body for adding or updating an item on a shopping list."""

    product_id: UUID
    quantity: Decimal


class BuyItemIn(BaseModel):
    """Optional request body for marking a shopping list item as bought."""

    price: Decimal | None = None
    currency: Currency | None = None


class CloseListIn(BaseModel):
    """Request body for closing a shopping list."""

    category_id: UUID
    spent_on: date | None = None


class ShoppingListItemOut(BaseModel):
    """Public HTTP representation of a shopping list item with product metadata."""

    id: UUID
    list_id: UUID
    product_id: UUID
    product_name: str
    product_unit: Unit
    product_last_price: DecimalStr | None
    product_last_price_currency: Currency | None
    quantity: DecimalStr
    status: ItemStatus
    bought_price: DecimalStr | None
    bought_currency: Currency | None
    bought_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_detail(cls, detail: ShoppingListItemDetail) -> Self:
        """Build an HTTP response model from a domain ShoppingListItemDetail."""
        item = detail.item
        return cls(
            id=item.id,
            list_id=item.list_id,
            product_id=item.product_id,
            product_name=detail.product_name,
            product_unit=detail.product_unit,
            product_last_price=detail.product_last_price,
            product_last_price_currency=detail.product_last_price_currency,
            quantity=item.quantity,
            status=item.status,
            bought_price=item.bought_price,
            bought_currency=item.bought_currency,
            bought_at=item.bought_at,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )


class ShoppingListDetailOut(BaseModel):
    """Public HTTP representation of a shopping list with its ordered items."""

    id: UUID
    name: str
    status: ListStatus
    closed_at: datetime | None
    expense_id: UUID | None
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime
    items: list[ShoppingListItemOut]

    @classmethod
    def from_domain(cls, detail: ShoppingListWithItems) -> Self:
        """Build an HTTP response model from a domain ShoppingListWithItems."""
        lst = detail.shopping_list
        return cls(
            id=lst.id,
            name=lst.name,
            status=lst.status,
            closed_at=lst.closed_at,
            expense_id=lst.expense_id,
            deleted_at=lst.deleted_at,
            created_at=lst.created_at,
            updated_at=lst.updated_at,
            items=[ShoppingListItemOut.from_detail(item) for item in detail.items],
        )


class RateSnapshotOut(BaseModel):
    """Public HTTP representation of an exchange rate snapshot used in totals."""

    value: DecimalStr
    value_date: date

    @classmethod
    def from_domain(cls, snapshot: RateSnapshot) -> Self:
        """Build an HTTP response model from a domain RateSnapshot."""
        return cls(value=snapshot.value, value_date=snapshot.value_date)


class EstimatedTotalsOut(BaseModel):
    """Public HTTP representation of estimated shopping list totals."""

    total_ves: DecimalStr
    total_usd: DecimalStr
    usdt_to_sell: DecimalStr
    unpriced_items: int

    @classmethod
    def from_domain(cls, totals: EstimatedTotals) -> Self:
        """Build an HTTP response model from a domain EstimatedTotals."""
        return cls(
            total_ves=totals.total_ves,
            total_usd=totals.total_usd,
            usdt_to_sell=totals.usdt_to_sell,
            unpriced_items=totals.unpriced_items,
        )


class BoughtTotalsOut(BaseModel):
    """Public HTTP representation of bought shopping list totals."""

    total_ves: DecimalStr
    total_usd: DecimalStr
    usdt_to_sell: DecimalStr

    @classmethod
    def from_domain(cls, totals: BoughtTotals) -> Self:
        """Build an HTTP response model from a domain BoughtTotals."""
        return cls(
            total_ves=totals.total_ves,
            total_usd=totals.total_usd,
            usdt_to_sell=totals.usdt_to_sell,
        )


class ListTotalsOut(BaseModel):
    """Public HTTP representation of estimated and bought totals for a shopping list."""

    estimated: EstimatedTotalsOut
    bought: BoughtTotalsOut
    bcv_rate: RateSnapshotOut
    usdt_rate: RateSnapshotOut

    @classmethod
    def from_domain(cls, totals: ListTotals) -> Self:
        """Build an HTTP response model from a domain ListTotals."""
        return cls(
            estimated=EstimatedTotalsOut.from_domain(totals.estimated),
            bought=BoughtTotalsOut.from_domain(totals.bought),
            bcv_rate=RateSnapshotOut.from_domain(totals.bcv_rate),
            usdt_rate=RateSnapshotOut.from_domain(totals.usdt_rate),
        )


class RecommendationsQuery(BaseModel):
    """Query parameters for shopping recommendations."""

    days_ahead: int = DEFAULT_RECOMMENDATION_DAYS_AHEAD


class RecommendationItemOut(BaseModel):
    """Public HTTP representation of one recommended product."""

    product_id: UUID
    product_name: str
    unit: Unit
    last_price: DecimalStr | None
    last_price_currency: Currency | None
    avg_interval_days: DecimalStr
    last_purchased_on: date
    next_due: date
    suggested_quantity: DecimalStr

    @classmethod
    def from_domain(cls, item: RecommendationItem) -> Self:
        """Build an HTTP response model from a domain RecommendationItem."""
        return cls(
            product_id=item.product_id,
            product_name=item.product_name,
            unit=item.unit,
            last_price=item.last_price,
            last_price_currency=item.last_price_currency,
            avg_interval_days=item.avg_interval_days,
            last_purchased_on=item.last_purchased_on,
            next_due=item.next_due,
            suggested_quantity=item.suggested_quantity,
        )


class RecommendationTotalsOut(BaseModel):
    """Public HTTP representation of estimated totals for recommended items."""

    total_ves: DecimalStr
    total_usd: DecimalStr
    usdt_to_sell: DecimalStr
    unpriced_items: int
    bcv_rate: RateSnapshotOut
    usdt_rate: RateSnapshotOut

    @classmethod
    def from_domain(cls, totals: RecommendationTotals) -> Self:
        """Build an HTTP response model from a domain RecommendationTotals."""
        return cls(
            total_ves=totals.total_ves,
            total_usd=totals.total_usd,
            usdt_to_sell=totals.usdt_to_sell,
            unpriced_items=totals.unpriced_items,
            bcv_rate=RateSnapshotOut.from_domain(totals.bcv_rate),
            usdt_rate=RateSnapshotOut.from_domain(totals.usdt_rate),
        )


class RecommendationsOut(BaseModel):
    """Public HTTP representation of shopping recommendations and their estimated totals."""

    items: list[RecommendationItemOut]
    totals: RecommendationTotalsOut

    @classmethod
    def from_domain(cls, result: RecommendationsResult) -> Self:
        """Build an HTTP response model from a domain RecommendationsResult."""
        return cls(
            items=[RecommendationItemOut.from_domain(item) for item in result.items],
            totals=RecommendationTotalsOut.from_domain(result.totals),
        )
