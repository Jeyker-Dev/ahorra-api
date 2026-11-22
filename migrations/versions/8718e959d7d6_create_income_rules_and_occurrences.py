"""create income rules and occurrences

Revision ID: 8718e959d7d6
Revises: dabc5ff65682
Create Date: 2026-11-22 10:08:01.487598

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "8718e959d7d6"
down_revision: str | Sequence[str] | None = "dabc5ff65682"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "income_rules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("amount", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("days", postgresql.ARRAY(sa.SmallInteger()), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("currency IN ('usd', 'ves')", name="ck_income_rules_currency"),
        sa.CheckConstraint("amount > 0", name="ck_income_rules_amount_positive"),
        sa.CheckConstraint("cardinality(days) > 0", name="ck_income_rules_days_non_empty"),
        sa.CheckConstraint(
            "char_length(btrim(name)) BETWEEN 1 AND 100",
            name="ck_income_rules_name",
        ),
        sa.CheckConstraint(
            "end_date IS NULL OR end_date >= start_date",
            name="ck_income_rules_period",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "income_occurrences",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("rule_id", sa.Uuid(), nullable=False),
        sa.Column("expected_date", sa.Date(), nullable=False),
        sa.Column("expected_amount", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("received_date", sa.Date(), nullable=True),
        sa.Column("received_amount", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("rate_value", sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column("rate_date", sa.Date(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(status = 'expected' AND received_date IS NULL AND received_amount IS NULL "
            "AND rate_value IS NULL AND rate_date IS NULL AND received_at IS NULL) "
            "OR (status = 'received' AND received_date IS NOT NULL AND received_amount IS NOT NULL "
            "AND rate_value IS NOT NULL AND rate_date IS NOT NULL AND received_at IS NOT NULL)",
            name="ck_income_occurrences_receipt_fields",
        ),
        sa.CheckConstraint("currency IN ('usd', 'ves')", name="ck_income_occurrences_currency"),
        sa.CheckConstraint(
            "status IN ('expected', 'received')",
            name="ck_income_occurrences_status",
        ),
        sa.CheckConstraint(
            "expected_amount > 0",
            name="ck_income_occurrences_expected_amount_positive",
        ),
        sa.CheckConstraint(
            "rate_value IS NULL OR rate_value > 0",
            name="ck_income_occurrences_rate_value_positive",
        ),
        sa.CheckConstraint(
            "received_amount IS NULL OR received_amount > 0",
            name="ck_income_occurrences_received_amount_positive",
        ),
        sa.ForeignKeyConstraint(["rule_id"], ["income_rules.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "rule_id",
            "expected_date",
            name="uq_income_occurrences_rule_expected_date",
        ),
    )
    op.create_index(
        "ix_income_occurrences_expected_date",
        "income_occurrences",
        ["expected_date"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_income_occurrences_expected_date", table_name="income_occurrences")
    op.drop_table("income_occurrences")
    op.drop_table("income_rules")
