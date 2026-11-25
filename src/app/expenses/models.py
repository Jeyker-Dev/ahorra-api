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
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

_MAX_CATEGORY_NAME_LENGTH = 50
_MAX_NOTE_LENGTH = 500


class ExpenseCategoryModel(Base):
    """SQLAlchemy model for the expense_categories table."""

    __tablename__ = "expense_categories"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(_MAX_CATEGORY_NAME_LENGTH))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("uq_expense_categories_lower_name", func.lower(name), unique=True),
        Index("ix_expense_categories_updated_at", "updated_at"),
        CheckConstraint(
            "char_length(btrim(name)) BETWEEN 1 AND 50",
            name="ck_expense_categories_name",
        ),
    )


class ExpenseModel(Base):
    """SQLAlchemy model for the expenses table."""

    __tablename__ = "expenses"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    category_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("expense_categories.id", ondelete="RESTRICT"),
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(8))
    spent_on: Mapped[date] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(String(_MAX_NOTE_LENGTH), nullable=True)
    bcv_rate_value: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    bcv_rate_date: Mapped[date] = mapped_column(Date)
    usdt_rate_value: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    usdt_rate_date: Mapped[date] = mapped_column(Date)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_expenses_spent_on", "spent_on"),
        Index("ix_expenses_category_id_spent_on", "category_id", "spent_on"),
        Index("ix_expenses_updated_at", "updated_at"),
        CheckConstraint("amount > 0", name="ck_expenses_amount_positive"),
        CheckConstraint("currency IN ('usd', 'ves')", name="ck_expenses_currency"),
        CheckConstraint(
            "note IS NULL OR (char_length(btrim(note)) BETWEEN 1 AND 500)",
            name="ck_expenses_note",
        ),
        CheckConstraint("bcv_rate_value > 0", name="ck_expenses_bcv_rate_value_positive"),
        CheckConstraint("usdt_rate_value > 0", name="ck_expenses_usdt_rate_value_positive"),
    )
