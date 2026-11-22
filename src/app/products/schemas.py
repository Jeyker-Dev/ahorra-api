from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, Field, PlainSerializer, model_validator

from app.products.domain import Currency, PriceEntry, PriceSource, Product, Unit

DecimalStr = Annotated[
    Decimal,
    PlainSerializer(str, return_type=str, when_used="json"),
]


class ProductUpsertIn(BaseModel):
    """Request body for creating or updating a product."""

    name: str
    unit: Unit
    last_price: Decimal | None = None
    currency: Currency | None = None


class ProductListQuery(BaseModel):
    """Query parameters for listing products."""

    include_archived: bool = False


class ProductOut(BaseModel):
    """Public HTTP representation of a product."""

    id: UUID
    name: str
    unit: Unit
    last_price: DecimalStr | None
    last_price_currency: Currency | None
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, product: Product) -> Self:
        """Build an HTTP response model from a domain Product."""
        return cls(
            id=product.id,
            name=product.name,
            unit=product.unit,
            last_price=product.last_price,
            last_price_currency=product.last_price_currency,
            archived_at=product.archived_at,
            created_at=product.created_at,
            updated_at=product.updated_at,
        )


class ProductPriceHistoryQuery(BaseModel):
    """Query parameters for listing historical price entries of a product."""

    from_date: date = Field(alias="from")
    to_date: date = Field(alias="to")

    @model_validator(mode="after")
    def check_date_range(self) -> Self:
        """Reject requests where the start date is after the end date."""
        if self.from_date > self.to_date:
            raise ValueError("'from' must be less than or equal to 'to'.")
        return self


class PriceEntryOut(BaseModel):
    """Public HTTP representation of a product price history entry."""

    id: UUID
    product_id: UUID
    price: DecimalStr
    currency: Currency
    recorded_on: date
    source: PriceSource
    list_item_id: UUID | None
    bcv_rate_value: DecimalStr
    bcv_rate_date: date
    usdt_rate_value: DecimalStr
    usdt_rate_date: date
    created_at: datetime

    @classmethod
    def from_domain(cls, entry: PriceEntry) -> Self:
        """Build an HTTP response model from a domain PriceEntry."""
        return cls(
            id=entry.id,
            product_id=entry.product_id,
            price=entry.price,
            currency=entry.currency,
            recorded_on=entry.recorded_on,
            source=entry.source,
            list_item_id=entry.list_item_id,
            bcv_rate_value=entry.bcv_rate_value,
            bcv_rate_date=entry.bcv_rate_date,
            usdt_rate_value=entry.usdt_rate_value,
            usdt_rate_date=entry.usdt_rate_date,
            created_at=entry.created_at,
        )
