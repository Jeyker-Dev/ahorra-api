from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

_MAX_PRODUCT_NAME_LENGTH = 80


class ProductModel(Base):
    """SQLAlchemy model for the products table."""

    __tablename__ = "products"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(_MAX_PRODUCT_NAME_LENGTH))
    unit: Mapped[str] = mapped_column(String(8))
    last_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    last_price_currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("uq_products_lower_name", func.lower(name), unique=True),
        Index("ix_products_updated_at", "updated_at"),
        CheckConstraint(
            "char_length(btrim(name)) BETWEEN 1 AND 80",
            name="ck_products_name",
        ),
        CheckConstraint("unit IN ('unit', 'kg', 'l')", name="ck_products_unit"),
        CheckConstraint(
            "last_price IS NULL OR last_price > 0",
            name="ck_products_last_price_positive",
        ),
        CheckConstraint(
            "last_price_currency IS NULL OR last_price_currency IN ('usd', 'ves')",
            name="ck_products_last_price_currency",
        ),
        CheckConstraint(
            "(last_price IS NULL AND last_price_currency IS NULL) "
            "OR (last_price IS NOT NULL AND last_price_currency IS NOT NULL)",
            name="ck_products_last_price_pair",
        ),
    )


class ProductPriceModel(Base):
    """SQLAlchemy model for the product_prices table."""

    __tablename__ = "product_prices"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    product_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("products.id", ondelete="RESTRICT"),
    )
    price: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(8))
    recorded_on: Mapped[date] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(16))
    list_item_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("shopping_list_items.id", ondelete="RESTRICT"),
        nullable=True,
    )
    bcv_rate_value: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    bcv_rate_date: Mapped[date] = mapped_column(Date)
    usdt_rate_value: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    usdt_rate_date: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_product_prices_product_id_recorded_on", "product_id", "recorded_on"),
        Index("ix_product_prices_updated_at", "updated_at"),
        Index(
            "uq_product_prices_list_item_id",
            "list_item_id",
            unique=True,
            postgresql_where=text("list_item_id IS NOT NULL"),
        ),
        CheckConstraint("price > 0", name="ck_product_prices_price_positive"),
        CheckConstraint("currency IN ('usd', 'ves')", name="ck_product_prices_currency"),
        CheckConstraint("source IN ('manual', 'purchase')", name="ck_product_prices_source"),
        CheckConstraint(
            "(source = 'purchase' AND list_item_id IS NOT NULL) "
            "OR (source = 'manual' AND list_item_id IS NULL)",
            name="ck_product_prices_source_list_item",
        ),
        CheckConstraint(
            "bcv_rate_value > 0",
            name="ck_product_prices_bcv_rate_value_positive",
        ),
        CheckConstraint(
            "usdt_rate_value > 0",
            name="ck_product_prices_usdt_rate_value_positive",
        ),
    )
