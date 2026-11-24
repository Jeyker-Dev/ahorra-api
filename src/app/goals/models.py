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

_MAX_GOAL_NAME_LENGTH = 50
_MAX_NOTE_LENGTH = 500


class GoalModel(Base):
    """SQLAlchemy model for the goals table."""

    __tablename__ = "goals"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(_MAX_GOAL_NAME_LENGTH))
    target_usd: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    target_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    note: Mapped[str | None] = mapped_column(String(_MAX_NOTE_LENGTH), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index(
            "uq_goals_lower_name_active",
            func.lower(name),
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        CheckConstraint(
            "char_length(btrim(name)) BETWEEN 1 AND 50",
            name="ck_goals_name",
        ),
        CheckConstraint("target_usd > 0", name="ck_goals_target_usd_positive"),
        CheckConstraint(
            "note IS NULL OR (char_length(btrim(note)) BETWEEN 1 AND 500)",
            name="ck_goals_note",
        ),
    )


class GoalContributionModel(Base):
    """SQLAlchemy model for the goal_contributions table."""

    __tablename__ = "goal_contributions"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    goal_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("goals.id", ondelete="RESTRICT"),
    )
    kind: Mapped[str] = mapped_column(String(16))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(16))
    contributed_on: Mapped[date] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(String(_MAX_NOTE_LENGTH), nullable=True)
    bcv_rate_value: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    bcv_rate_date: Mapped[date] = mapped_column(Date)
    usdt_rate_value: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    usdt_rate_date: Mapped[date] = mapped_column(Date)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_goal_contributions_goal_id_contributed_on", "goal_id", "contributed_on"),
        CheckConstraint(
            "kind IN ('deposit', 'withdrawal')",
            name="ck_goal_contributions_kind",
        ),
        CheckConstraint("amount > 0", name="ck_goal_contributions_amount_positive"),
        CheckConstraint("currency IN ('usd', 'ves')", name="ck_goal_contributions_currency"),
        CheckConstraint(
            "note IS NULL OR (char_length(btrim(note)) BETWEEN 1 AND 500)",
            name="ck_goal_contributions_note",
        ),
        CheckConstraint(
            "bcv_rate_value > 0",
            name="ck_goal_contributions_bcv_rate_value_positive",
        ),
        CheckConstraint(
            "usdt_rate_value > 0",
            name="ck_goal_contributions_usdt_rate_value_positive",
        ),
    )
