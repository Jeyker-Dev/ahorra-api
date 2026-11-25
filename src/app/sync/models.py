from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

_MAX_ENTITY_LENGTH = 32
_VALID_ENTITIES_SQL = (
    "entity IN ("
    "'income_rules', 'income_occurrences', 'expense_categories', 'expenses', "
    "'products', 'product_prices', 'shopping_lists', 'shopping_list_items', "
    "'goals', 'goal_contributions'"
    ")"
)


class SyncTombstoneModel(Base):
    """SQLAlchemy model for the sync_tombstones table."""

    __tablename__ = "sync_tombstones"

    entity: Mapped[str] = mapped_column(String(_MAX_ENTITY_LENGTH), primary_key=True)
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    deleted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_sync_tombstones_deleted_at", "deleted_at"),
        CheckConstraint(_VALID_ENTITIES_SQL, name="ck_sync_tombstones_entity"),
    )
