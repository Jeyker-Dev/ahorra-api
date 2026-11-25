import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

_PREV_REVISION = "67194cb56ccd"

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

_TRUNCATE_SQL = (
    "TRUNCATE TABLE sync_tombstones, product_prices, shopping_list_items, "
    "shopping_lists, products, income_occurrences, income_rules CASCADE"
)


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


def _get_pk_columns(conn: Connection, table: str) -> list[str]:
    return list(inspect(conn).get_pk_constraint(table)["constrained_columns"])


@pytest.mark.usefixtures("ensure_test_database")
async def test_sync_migration_upgrade_backfill_and_downgrade(test_database_url: str) -> None:
    cfg = _alembic_config(test_database_url)
    await asyncio.to_thread(command.downgrade, cfg, _PREV_REVISION)

    engine = create_async_engine(test_database_url)
    product_id = uuid4()
    price_id = uuid4()
    created_at = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)

    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO products "
                    "(id, name, unit, last_price, last_price_currency, "
                    "archived_at, created_at, updated_at) "
                    "VALUES (:id, 'Sugar', 'kg', 2.00, 'usd', NULL, :ts, :ts)"
                ),
                {"id": str(product_id), "ts": created_at},
            )
            await conn.execute(
                text(
                    "INSERT INTO product_prices "
                    "(id, product_id, price, currency, recorded_on, source, list_item_id, "
                    "bcv_rate_value, bcv_rate_date, usdt_rate_value, usdt_rate_date, created_at) "
                    "VALUES (:id, :product_id, 2.00, 'usd', :d, 'manual', NULL, "
                    "900.00000000, :d, 950.00000000, :d, :ts)"
                ),
                {
                    "id": str(price_id),
                    "product_id": str(product_id),
                    "d": date(2026, 11, 22),
                    "ts": created_at,
                },
            )

        await asyncio.to_thread(command.upgrade, cfg, "head")

        async with engine.connect() as conn:
            assert "sync_tombstones" in await conn.run_sync(_get_table_names)
            tomb_cols = await conn.run_sync(_get_columns, "sync_tombstones")
            assert set(tomb_cols) == {"entity", "id", "deleted_at"}
            assert await conn.run_sync(_get_pk_columns, "sync_tombstones") == ["entity", "id"]
            assert "ix_sync_tombstones_deleted_at" in await conn.run_sync(
                _get_index_names, "sync_tombstones"
            )

            pp_cols = await conn.run_sync(_get_columns, "product_prices")
            assert "updated_at" in pp_cols
            assert pp_cols["updated_at"]["nullable"] is False
            backfilled = (
                await conn.execute(
                    text("SELECT updated_at FROM product_prices WHERE id = :id"),
                    {"id": str(price_id)},
                )
            ).scalar_one()
            assert backfilled == created_at

            for index_name, table_name in _UPDATED_AT_INDEXES:
                assert index_name in await conn.run_sync(_get_index_names, table_name)

            triggers = set(
                (
                    await conn.execute(
                        text(
                            "SELECT tgname FROM pg_trigger "
                            "WHERE NOT tgisinternal AND tgname LIKE '%sync_tombstone'"
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert triggers == {
                "tr_income_occurrences_sync_tombstone",
                "tr_shopping_list_items_sync_tombstone",
                "tr_product_prices_sync_tombstone",
            }

        await asyncio.to_thread(command.downgrade, cfg, _PREV_REVISION)

        async with engine.connect() as conn:
            assert "sync_tombstones" not in await conn.run_sync(_get_table_names)
            pp_cols_after = await conn.run_sync(_get_columns, "product_prices")
            assert "updated_at" not in pp_cols_after
            for index_name, table_name in _UPDATED_AT_INDEXES:
                if table_name != "product_prices":
                    assert index_name not in await conn.run_sync(_get_index_names, table_name)
            fn_exists = (
                await conn.execute(
                    text("SELECT 1 FROM pg_proc WHERE proname = 'record_sync_tombstone'")
                )
            ).scalar_one_or_none()
            assert fn_exists is None
    finally:
        await asyncio.to_thread(command.upgrade, cfg, "head")
        async with engine.begin() as conn:
            await conn.execute(text(_TRUNCATE_SQL))
        await engine.dispose()


@pytest.mark.usefixtures("ensure_test_database")
async def test_sync_tombstones_constraints_and_rollback_behavior(test_database_url: str) -> None:
    cfg = _alembic_config(test_database_url)
    await asyncio.to_thread(command.upgrade, cfg, "head")

    engine = create_async_engine(test_database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(_TRUNCATE_SQL))

        now = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
        with pytest.raises(IntegrityError):
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO sync_tombstones (entity, id, deleted_at) "
                        "VALUES ('users', :id, :ts)"
                    ),
                    {"id": str(uuid4()), "ts": now},
                )

        product_id = uuid4()
        list_id = uuid4()
        item_id = uuid4()
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO products "
                    "(id, name, unit, last_price, last_price_currency, "
                    "archived_at, created_at, updated_at) "
                    "VALUES (:id, 'Salt', 'kg', NULL, NULL, NULL, :ts, :ts)"
                ),
                {"id": str(product_id), "ts": now},
            )
            await conn.execute(
                text(
                    "INSERT INTO shopping_lists "
                    "(id, name, status, closed_at, expense_id, deleted_at, created_at, updated_at) "
                    "VALUES (:id, 'Weekly', 'open', NULL, NULL, NULL, :ts, :ts)"
                ),
                {"id": str(list_id), "ts": now},
            )
            await conn.execute(
                text(
                    "INSERT INTO shopping_list_items "
                    "(id, list_id, product_id, quantity, status, bought_price, "
                    "bought_currency, bought_at, created_at, updated_at) "
                    "VALUES (:id, :list_id, :product_id, :qty, 'pending', "
                    "NULL, NULL, NULL, :ts, :ts)"
                ),
                {
                    "id": str(item_id),
                    "list_id": str(list_id),
                    "product_id": str(product_id),
                    "qty": Decimal("1.000"),
                    "ts": now,
                },
            )

        async with engine.connect() as conn:
            tx = await conn.begin()
            await conn.execute(
                text("DELETE FROM shopping_list_items WHERE id = :id"),
                {"id": str(item_id)},
            )
            await tx.rollback()

        async with engine.connect() as conn:
            tombstone_count = (
                await conn.execute(
                    text("SELECT count(*) FROM sync_tombstones WHERE id = :id"),
                    {"id": str(item_id)},
                )
            ).scalar_one()
            assert tombstone_count == 0
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(_TRUNCATE_SQL))
        await engine.dispose()
