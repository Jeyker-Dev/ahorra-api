import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def _alembic_config(database_url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def _get_table_names(conn: Connection) -> set[str]:
    return set(inspect(conn).get_table_names())


def _get_columns(conn: Connection, table: str) -> dict[str, dict[str, object]]:
    return {col["name"]: dict(col) for col in inspect(conn).get_columns(table)}


def _get_index_names(conn: Connection, table: str) -> set[str]:
    return {idx["name"] for idx in inspect(conn).get_indexes(table) if idx["name"] is not None}


async def _insert_category(engine: AsyncEngine, **overrides: object) -> UUID:
    cat_id = uuid4()
    now = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
    params: dict[str, object] = {
        "id": str(cat_id),
        "name": "Food",
        "archived_at": None,
        "created_at": now,
        "updated_at": now,
    }
    params.update(overrides)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO expense_categories "
                "(id, name, archived_at, created_at, updated_at) "
                "VALUES (:id, :name, :archived_at, :created_at, :updated_at)"
            ),
            params,
        )
    return cat_id


async def _insert_expense(engine: AsyncEngine, category_id: UUID, **overrides: object) -> UUID:
    exp_id = uuid4()
    now = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
    params: dict[str, object] = {
        "id": str(exp_id),
        "category_id": str(category_id),
        "amount": Decimal("25.00"),
        "currency": "usd",
        "spent_on": date(2026, 11, 20),
        "note": None,
        "bcv_rate_value": Decimal("917.77510000"),
        "bcv_rate_date": date(2026, 11, 20),
        "usdt_rate_value": Decimal("950.00000000"),
        "usdt_rate_date": date(2026, 11, 20),
        "deleted_at": None,
        "created_at": now,
        "updated_at": now,
    }
    params.update(overrides)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO expenses "
                "(id, category_id, amount, currency, spent_on, note, "
                "bcv_rate_value, bcv_rate_date, usdt_rate_value, usdt_rate_date, "
                "deleted_at, created_at, updated_at) "
                "VALUES (:id, :category_id, :amount, :currency, :spent_on, :note, "
                ":bcv_rate_value, :bcv_rate_date, :usdt_rate_value, :usdt_rate_date, "
                ":deleted_at, :created_at, :updated_at)"
            ),
            params,
        )
    return exp_id


@pytest.mark.usefixtures("ensure_test_database")
async def test_expenses_migration_creates_and_drops_tables(test_database_url: str) -> None:
    cfg = _alembic_config(test_database_url)
    await asyncio.to_thread(command.downgrade, cfg, "base")
    await asyncio.to_thread(command.upgrade, cfg, "head")

    engine = create_async_engine(test_database_url)
    try:
        async with engine.connect() as conn:
            assert {"expense_categories", "expenses"}.issubset(
                await conn.run_sync(_get_table_names)
            )
            assert set(await conn.run_sync(_get_columns, "expense_categories")) == {
                "id",
                "name",
                "archived_at",
                "created_at",
                "updated_at",
            }
            assert set(await conn.run_sync(_get_columns, "expenses")) == {
                "id",
                "category_id",
                "amount",
                "currency",
                "spent_on",
                "note",
                "bcv_rate_value",
                "bcv_rate_date",
                "usdt_rate_value",
                "usdt_rate_date",
                "deleted_at",
                "created_at",
                "updated_at",
            }
            assert "uq_expense_categories_lower_name" in await conn.run_sync(
                _get_index_names,
                "expense_categories",
            )
            assert {
                "ix_expenses_spent_on",
                "ix_expenses_category_id_spent_on",
            }.issubset(await conn.run_sync(_get_index_names, "expenses"))
            fks = await conn.run_sync(lambda c: inspect(c).get_foreign_keys("expenses"))
            assert len(fks) == 1
            assert fks[0]["referred_table"] == "expense_categories"
            assert fks[0]["options"].get("ondelete") == "RESTRICT"

        await asyncio.to_thread(command.downgrade, cfg, "base")
        async with engine.connect() as conn:
            tables_after = await conn.run_sync(_get_table_names)
            assert "expense_categories" not in tables_after
            assert "expenses" not in tables_after
        await asyncio.to_thread(command.upgrade, cfg, "head")
    finally:
        await engine.dispose()


@pytest.mark.usefixtures("ensure_test_database")
async def test_expenses_migration_enforces_unique_fk_and_check_constraints(
    test_database_url: str,
) -> None:
    cfg = _alembic_config(test_database_url)
    await asyncio.to_thread(command.upgrade, cfg, "head")

    engine = create_async_engine(test_database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE TABLE expenses, expense_categories CASCADE"))

        # Category name checks + lower(name) uniqueness
        with pytest.raises(IntegrityError):
            await _insert_category(engine, name="   ")

        cat_id = await _insert_category(engine, name="Groceries")
        with pytest.raises(IntegrityError):
            await _insert_category(engine, name="groceries")

        await _insert_expense(engine, cat_id)

        # FK ON DELETE RESTRICT
        with pytest.raises(IntegrityError):
            async with engine.begin() as conn:
                await conn.execute(
                    text("DELETE FROM expense_categories WHERE id = :id"),
                    {"id": str(cat_id)},
                )

        # Expense CHECK constraints
        bad_expenses: list[dict[str, object]] = [
            {"amount": Decimal("0")},
            {"amount": Decimal("-1.00")},
            {"currency": "eur"},
            {"note": "   "},
            {"bcv_rate_value": Decimal("0")},
            {"usdt_rate_value": Decimal("0")},
        ]
        for overrides in bad_expenses:
            with pytest.raises(IntegrityError):
                await _insert_expense(engine, cat_id, **overrides)
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE TABLE expenses, expense_categories CASCADE"))
        await engine.dispose()
