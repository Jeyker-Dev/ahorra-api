"""create exchange_rates

Revision ID: dabc5ff65682
Revises: 2e215cbe92c7
Create Date: 2026-11-20 22:42:35.523906

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "dabc5ff65682"
down_revision: str | Sequence[str] | None = "2e215cbe92c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "exchange_rates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("value", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("value_date", sa.Date(), nullable=False),
        sa.Column("origin", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('bcv', 'usdt')", name="ck_exchange_rates_kind"),
        sa.CheckConstraint(
            "origin IN ('bcv_html', 'dolarapi', 'binance_p2p', 'manual')",
            name="ck_exchange_rates_origin",
        ),
        sa.CheckConstraint("status IN ('confirmed', 'in_review')", name="ck_exchange_rates_status"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_exchange_rates_kind_fetched_at",
        "exchange_rates",
        ["kind", sa.literal_column("fetched_at DESC")],
        unique=False,
    )
    op.create_index(
        "uq_exchange_rates_bcv_value_date",
        "exchange_rates",
        ["value_date"],
        unique=True,
        postgresql_where=sa.text("kind = 'bcv'"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "uq_exchange_rates_bcv_value_date",
        table_name="exchange_rates",
        postgresql_where=sa.text("kind = 'bcv'"),
    )
    op.drop_index("ix_exchange_rates_kind_fetched_at", table_name="exchange_rates")
    op.drop_table("exchange_rates")
