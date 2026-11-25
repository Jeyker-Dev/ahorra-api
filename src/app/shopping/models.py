from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

_MAX_LIST_NAME_LENGTH = 80


class ShoppingListModel(Base):
    """SQLAlchemy model for the shopping_lists table."""

    __tablename__ = "shopping_lists"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(_MAX_LIST_NAME_LENGTH))
    status: Mapped[str] = mapped_column(String(16))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expense_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("expenses.id", ondelete="RESTRICT"),
        nullable=True,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_shopping_lists_updated_at", "updated_at"),
        CheckConstraint(
            "char_length(btrim(name)) BETWEEN 1 AND 80",
            name="ck_shopping_lists_name",
        ),
        CheckConstraint("status IN ('open', 'closed')", name="ck_shopping_lists_status"),
        CheckConstraint(
            "(status = 'open' AND closed_at IS NULL AND expense_id IS NULL) "
            "OR (status = 'closed' AND closed_at IS NOT NULL AND expense_id IS NOT NULL)",
            name="ck_shopping_lists_closed_fields",
        ),
    )


class ShoppingListItemModel(Base):
    """SQLAlchemy model for the shopping_list_items table."""

    __tablename__ = "shopping_list_items"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    list_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("shopping_lists.id", ondelete="RESTRICT"),
    )
    product_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("products.id", ondelete="RESTRICT"),
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 3))
    status: Mapped[str] = mapped_column(String(16))
    bought_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    bought_currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    bought_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_shopping_list_items_updated_at", "updated_at"),
        UniqueConstraint(
            "list_id",
            "product_id",
            name="uq_shopping_list_items_list_product",
        ),
        CheckConstraint("quantity > 0", name="ck_shopping_list_items_quantity_positive"),
        CheckConstraint("status IN ('pending', 'bought')", name="ck_shopping_list_items_status"),
        CheckConstraint(
            "bought_price IS NULL OR bought_price > 0",
            name="ck_shopping_list_items_bought_price_positive",
        ),
        CheckConstraint(
            "bought_currency IS NULL OR bought_currency IN ('usd', 'ves')",
            name="ck_shopping_list_items_bought_currency",
        ),
        CheckConstraint(
            "(status = 'pending' AND bought_price IS NULL "
            "AND bought_currency IS NULL AND bought_at IS NULL) "
            "OR (status = 'bought' AND bought_price IS NOT NULL "
            "AND bought_currency IS NOT NULL AND bought_at IS NOT NULL)",
            name="ck_shopping_list_items_bought_fields",
        ),
    )
