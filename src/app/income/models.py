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
    SmallInteger,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

_MAX_NAME_LENGTH = 100


class IncomeRuleModel(Base):
    """SQLAlchemy model for the income_rules table."""

    __tablename__ = "income_rules"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(_MAX_NAME_LENGTH))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(8))
    days: Mapped[list[int]] = mapped_column(ARRAY(SmallInteger))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_income_rules_updated_at", "updated_at"),
        CheckConstraint(
            "char_length(btrim(name)) BETWEEN 1 AND 100",
            name="ck_income_rules_name",
        ),
        CheckConstraint("amount > 0", name="ck_income_rules_amount_positive"),
        CheckConstraint("currency IN ('usd', 'ves')", name="ck_income_rules_currency"),
        CheckConstraint("cardinality(days) > 0", name="ck_income_rules_days_non_empty"),
        CheckConstraint(
            "end_date IS NULL OR end_date >= start_date",
            name="ck_income_rules_period",
        ),
    )


class IncomeOccurrenceModel(Base):
    """SQLAlchemy model for the income_occurrences table."""

    __tablename__ = "income_occurrences"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    rule_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("income_rules.id", ondelete="RESTRICT"),
    )
    expected_date: Mapped[date] = mapped_column(Date)
    expected_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(16))
    received_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    received_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    rate_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    rate_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "rule_id",
            "expected_date",
            name="uq_income_occurrences_rule_expected_date",
        ),
        Index("ix_income_occurrences_expected_date", "expected_date"),
        Index("ix_income_occurrences_updated_at", "updated_at"),
        CheckConstraint(
            "expected_amount > 0",
            name="ck_income_occurrences_expected_amount_positive",
        ),
        CheckConstraint(
            "received_amount IS NULL OR received_amount > 0",
            name="ck_income_occurrences_received_amount_positive",
        ),
        CheckConstraint(
            "rate_value IS NULL OR rate_value > 0",
            name="ck_income_occurrences_rate_value_positive",
        ),
        CheckConstraint("currency IN ('usd', 'ves')", name="ck_income_occurrences_currency"),
        CheckConstraint(
            "status IN ('expected', 'received')",
            name="ck_income_occurrences_status",
        ),
        CheckConstraint(
            "(status = 'expected' AND received_date IS NULL AND received_amount IS NULL "
            "AND rate_value IS NULL AND rate_date IS NULL AND received_at IS NULL) "
            "OR (status = 'received' AND received_date IS NOT NULL AND received_amount IS NOT NULL "
            "AND rate_value IS NOT NULL AND rate_date IS NOT NULL AND received_at IS NOT NULL)",
            name="ck_income_occurrences_receipt_fields",
        ),
    )
