"""add sync tombstones, triggers, product_prices updated_at, and updated_at indexes

Revision ID: 9c4f2a8b1d0e
Revises: 67194cb56ccd
Create Date: 2026-11-25 10:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9c4f2a8b1d0e"
down_revision: str | Sequence[str] | None = "67194cb56ccd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VALID_ENTITIES_SQL = (
    "entity IN ("
    "'income_rules', 'income_occurrences', 'expense_categories', 'expenses', "
    "'products', 'product_prices', 'shopping_lists', 'shopping_list_items', "
    "'goals', 'goal_contributions'"
    ")"
)

_UPDATED_AT_INDEXES: tuple[tuple[str, str], ...] = (
    ("ix_income_rules_updated_at", "income_rules"),
    ("ix_income_occurrences_updated_at", "income_occurrences"),
    ("ix_expense_categories_updated_at", "expense_categories"),
    ("ix_expenses_updated_at", "expenses"),
    ("ix_products_updated_at", "products"),
    ("ix_product_prices_updated_at", "product_prices"),
    ("ix_shopping_lists_updated_at", "shopping_lists"),
    ("ix_shopping_list_items_updated_at", "shopping_list_items"),
    ("ix_goals_updated_at", "goals"),
    ("ix_goal_contributions_updated_at", "goal_contributions"),
)

_TOMBSTONE_TRIGGERS: tuple[tuple[str, str], ...] = (
    ("tr_income_occurrences_sync_tombstone", "income_occurrences"),
    ("tr_shopping_list_items_sync_tombstone", "shopping_list_items"),
    ("tr_product_prices_sync_tombstone", "product_prices"),
)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "sync_tombstones",
        sa.Column("entity", sa.String(length=32), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(_VALID_ENTITIES_SQL, name="ck_sync_tombstones_entity"),
        sa.PrimaryKeyConstraint("entity", "id"),
    )
    op.create_index(
        "ix_sync_tombstones_deleted_at",
        "sync_tombstones",
        ["deleted_at"],
        unique=False,
    )

    op.add_column(
        "product_prices",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("UPDATE product_prices SET updated_at = created_at")
    op.alter_column("product_prices", "updated_at", nullable=False)

    for index_name, table_name in _UPDATED_AT_INDEXES:
        op.create_index(index_name, table_name, ["updated_at"], unique=False)

    op.execute(
        """
        CREATE FUNCTION record_sync_tombstone()
        RETURNS TRIGGER AS $$
        BEGIN
            INSERT INTO sync_tombstones (entity, id, deleted_at)
            VALUES (TG_TABLE_NAME, OLD.id, now())
            ON CONFLICT (entity, id) DO UPDATE
            SET deleted_at = EXCLUDED.deleted_at;
            RETURN OLD;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    for trigger_name, table_name in _TOMBSTONE_TRIGGERS:
        op.execute(
            f"CREATE TRIGGER {trigger_name} "
            f"AFTER DELETE ON {table_name} "
            "FOR EACH ROW EXECUTE FUNCTION record_sync_tombstone();"
        )


def downgrade() -> None:
    """Downgrade schema."""
    for trigger_name, table_name in reversed(_TOMBSTONE_TRIGGERS):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger_name} ON {table_name};")

    op.execute("DROP FUNCTION IF EXISTS record_sync_tombstone();")

    for index_name, table_name in reversed(_UPDATED_AT_INDEXES):
        op.drop_index(index_name, table_name=table_name)

    op.drop_column("product_prices", "updated_at")
    op.drop_index("ix_sync_tombstones_deleted_at", table_name="sync_tombstones")
    op.drop_table("sync_tombstones")
