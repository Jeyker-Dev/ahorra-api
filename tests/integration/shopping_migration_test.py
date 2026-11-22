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

_TRUNCATE_SQL = (
    "TRUNCATE TABLE product_prices, shopping_list_items, shopping_lists, "
    "products, expenses, expense_categories CASCADE"
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


def _get_unique_names(conn: Connection, table: str) -> set[str]:
    return {
        uc["name"] for uc in inspect(conn).get_unique_constraints(table) if uc["name"] is not None
    }


def _get_fk_ondelete(conn: Connection, table: str) -> list[str | None]:
    return [fk["options"].get("ondelete") for fk in inspect(conn).get_foreign_keys(table)]


async def _insert_product(engine: AsyncEngine, **overrides: object) -> UUID:
    product_id = uuid4()
    now = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
    params: dict[str, object] = {
        "id": str(product_id),
        "name": f"Product-{product_id}",
        "unit": "unit",
        "last_price": None,
        "last_price_currency": None,
        "archived_at": None,
        "created_at": now,
        "updated_at": now,
    }
    params.update(overrides)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO products "
                "(id, name, unit, last_price, last_price_currency, "
                "archived_at, created_at, updated_at) "
                "VALUES (:id, :name, :unit, :last_price, :last_price_currency, "
                ":archived_at, :created_at, :updated_at)"
            ),
            params,
        )
    return product_id


async def _insert_list(engine: AsyncEngine, **overrides: object) -> UUID:
    list_id = uuid4()
    now = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
    params: dict[str, object] = {
        "id": str(list_id),
        "name": "Weekly groceries",
        "status": "open",
        "closed_at": None,
        "expense_id": None,
        "deleted_at": None,
        "created_at": now,
        "updated_at": now,
    }
    params.update(overrides)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO shopping_lists "
                "(id, name, status, closed_at, expense_id, deleted_at, created_at, updated_at) "
                "VALUES (:id, :name, :status, :closed_at, :expense_id, "
                ":deleted_at, :created_at, :updated_at)"
            ),
            params,
        )
    return list_id


async def _insert_item(
    engine: AsyncEngine,
    list_id: UUID,
    product_id: UUID,
    **overrides: object,
) -> UUID:
    item_id = uuid4()
    now = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
    params: dict[str, object] = {
        "id": str(item_id),
        "list_id": str(list_id),
        "product_id": str(product_id),
        "quantity": Decimal("1.000"),
        "status": "pending",
        "bought_price": None,
        "bought_currency": None,
        "bought_at": None,
        "created_at": now,
        "updated_at": now,
    }
    params.update(overrides)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO shopping_list_items "
                "(id, list_id, product_id, quantity, status, bought_price, "
                "bought_currency, bought_at, created_at, updated_at) "
                "VALUES (:id, :list_id, :product_id, :quantity, :status, :bought_price, "
                ":bought_currency, :bought_at, :created_at, :updated_at)"
            ),
            params,
        )
    return item_id


async def _insert_price(
    engine: AsyncEngine,
    product_id: UUID,
    **overrides: object,
) -> UUID:
    price_id = uuid4()
    now = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
    params: dict[str, object] = {
        "id": str(price_id),
        "product_id": str(product_id),
        "price": Decimal("4.50"),
        "currency": "usd",
        "recorded_on": date(2026, 11, 22),
        "source": "manual",
        "list_item_id": None,
        "bcv_rate_value": Decimal("917.77510000"),
        "bcv_rate_date": date(2026, 11, 22),
        "usdt_rate_value": Decimal("950.00000000"),
        "usdt_rate_date": date(2026, 11, 22),
        "created_at": now,
    }
    params.update(overrides)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO product_prices "
                "(id, product_id, price, currency, recorded_on, source, list_item_id, "
                "bcv_rate_value, bcv_rate_date, usdt_rate_value, usdt_rate_date, created_at) "
                "VALUES (:id, :product_id, :price, :currency, :recorded_on, :source, "
                ":list_item_id, :bcv_rate_value, :bcv_rate_date, :usdt_rate_value, "
                ":usdt_rate_date, :created_at)"
            ),
            params,
        )
    return price_id


async def _insert_category_and_expense(engine: AsyncEngine) -> UUID:
    cat_id = uuid4()
    exp_id = uuid4()
    now = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO expense_categories "
                "(id, name, archived_at, created_at, updated_at) "
                "VALUES (:id, :name, NULL, :created_at, :updated_at)"
            ),
            {"id": str(cat_id), "name": f"Cat-{cat_id}", "created_at": now, "updated_at": now},
        )
        await conn.execute(
            text(
                "INSERT INTO expenses "
                "(id, category_id, amount, currency, spent_on, note, "
                "bcv_rate_value, bcv_rate_date, usdt_rate_value, usdt_rate_date, "
                "deleted_at, created_at, updated_at) "
                "VALUES (:id, :category_id, 10.00, 'usd', :spent_on, NULL, "
                "917.77510000, :spent_on, 950.00000000, :spent_on, "
                "NULL, :created_at, :updated_at)"
            ),
            {
                "id": str(exp_id),
                "category_id": str(cat_id),
                "spent_on": date(2026, 11, 22),
                "created_at": now,
                "updated_at": now,
            },
        )
    return exp_id


@pytest.mark.usefixtures("ensure_test_database")
async def test_shopping_migration_creates_and_drops_tables(test_database_url: str) -> None:
    cfg = _alembic_config(test_database_url)
    await asyncio.to_thread(command.downgrade, cfg, "base")
    await asyncio.to_thread(command.upgrade, cfg, "head")

    engine = create_async_engine(test_database_url)
    try:
        async with engine.connect() as conn:
            assert {
                "products",
                "product_prices",
                "shopping_lists",
                "shopping_list_items",
            }.issubset(await conn.run_sync(_get_table_names))
            assert "uq_products_lower_name" in await conn.run_sync(_get_index_names, "products")
            assert {
                "ix_product_prices_product_id_recorded_on",
                "uq_product_prices_list_item_id",
            }.issubset(await conn.run_sync(_get_index_names, "product_prices"))
            assert "uq_shopping_list_items_list_product" in await conn.run_sync(
                _get_unique_names, "shopping_list_items"
            )

        await asyncio.to_thread(command.downgrade, cfg, "072720097c63")
        async with engine.connect() as conn:
            tables_after = await conn.run_sync(_get_table_names)
            for table in ("products", "product_prices", "shopping_lists", "shopping_list_items"):
                assert table not in tables_after
        await asyncio.to_thread(command.upgrade, cfg, "head")
    finally:
        await engine.dispose()


@pytest.mark.usefixtures("ensure_test_database")
async def test_shopping_migration_columns_and_fks(test_database_url: str) -> None:
    cfg = _alembic_config(test_database_url)
    await asyncio.to_thread(command.upgrade, cfg, "head")

    engine = create_async_engine(test_database_url)
    try:
        async with engine.connect() as conn:
            assert set(await conn.run_sync(_get_columns, "products")) == {
                "id",
                "name",
                "unit",
                "last_price",
                "last_price_currency",
                "archived_at",
                "created_at",
                "updated_at",
            }
            assert set(await conn.run_sync(_get_columns, "product_prices")) == {
                "id",
                "product_id",
                "price",
                "currency",
                "recorded_on",
                "source",
                "list_item_id",
                "bcv_rate_value",
                "bcv_rate_date",
                "usdt_rate_value",
                "usdt_rate_date",
                "created_at",
            }
            assert set(await conn.run_sync(_get_columns, "shopping_lists")) == {
                "id",
                "name",
                "status",
                "closed_at",
                "expense_id",
                "deleted_at",
                "created_at",
                "updated_at",
            }
            assert set(await conn.run_sync(_get_columns, "shopping_list_items")) == {
                "id",
                "list_id",
                "product_id",
                "quantity",
                "status",
                "bought_price",
                "bought_currency",
                "bought_at",
                "created_at",
                "updated_at",
            }
            for table in ("product_prices", "shopping_lists", "shopping_list_items"):
                ondeletes = await conn.run_sync(_get_fk_ondelete, table)
                assert all(ondelete == "RESTRICT" for ondelete in ondeletes)
    finally:
        await engine.dispose()


@pytest.mark.usefixtures("ensure_test_database")
async def test_products_constraints_enforced(test_database_url: str) -> None:
    cfg = _alembic_config(test_database_url)
    await asyncio.to_thread(command.upgrade, cfg, "head")

    engine = create_async_engine(test_database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(_TRUNCATE_SQL))

        await _insert_product(engine, name="Rice")
        with pytest.raises(IntegrityError):
            await _insert_product(engine, name="rice")

        bad_products: list[dict[str, object]] = [
            {"name": "   "},
            {"unit": "box"},
            {"last_price": Decimal("0.00"), "last_price_currency": "usd"},
            {"last_price": Decimal("1.00"), "last_price_currency": "eur"},
            {"last_price": Decimal("1.00"), "last_price_currency": None},
            {"last_price": None, "last_price_currency": "usd"},
        ]
        for overrides in bad_products:
            with pytest.raises(IntegrityError):
                await _insert_product(engine, **overrides)
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(_TRUNCATE_SQL))
        await engine.dispose()


@pytest.mark.usefixtures("ensure_test_database")
async def test_product_prices_constraints_and_fk_enforced(test_database_url: str) -> None:
    cfg = _alembic_config(test_database_url)
    await asyncio.to_thread(command.upgrade, cfg, "head")

    engine = create_async_engine(test_database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(_TRUNCATE_SQL))

        product_id = await _insert_product(engine, name="Pasta")
        list_id = await _insert_list(engine)
        item_id = await _insert_item(engine, list_id, product_id)
        await _insert_price(engine, product_id, source="purchase", list_item_id=str(item_id))

        with pytest.raises(IntegrityError):
            await _insert_price(engine, product_id, source="purchase", list_item_id=str(item_id))

        with pytest.raises(IntegrityError):
            async with engine.begin() as conn:
                await conn.execute(
                    text("DELETE FROM products WHERE id = :id"),
                    {"id": str(product_id)},
                )

        bad_prices: list[dict[str, object]] = [
            {"price": Decimal("0.00")},
            {"currency": "eur"},
            {"source": "other"},
            {"source": "manual", "list_item_id": str(item_id)},
            {"source": "purchase", "list_item_id": None},
            {"bcv_rate_value": Decimal("0")},
            {"usdt_rate_value": Decimal("0")},
        ]
        for overrides in bad_prices:
            with pytest.raises(IntegrityError):
                await _insert_price(engine, product_id, **overrides)
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(_TRUNCATE_SQL))
        await engine.dispose()


@pytest.mark.usefixtures("ensure_test_database")
async def test_shopping_lists_and_items_constraints_enforced(test_database_url: str) -> None:
    cfg = _alembic_config(test_database_url)
    await asyncio.to_thread(command.upgrade, cfg, "head")

    engine = create_async_engine(test_database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(_TRUNCATE_SQL))

        now = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
        exp_id = await _insert_category_and_expense(engine)
        await _insert_list(engine, status="closed", closed_at=now, expense_id=str(exp_id))

        bad_lists: list[dict[str, object]] = [
            {"name": "   "},
            {"status": "archived"},
            {"status": "open", "closed_at": now, "expense_id": None},
            {"status": "closed", "closed_at": None, "expense_id": str(exp_id)},
            {"status": "closed", "closed_at": now, "expense_id": None},
        ]
        for bad_list in bad_lists:
            with pytest.raises(IntegrityError):
                await _insert_list(engine, **bad_list)

        product_id = await _insert_product(engine, name="Coffee")
        list_id = await _insert_list(engine)
        await _insert_item(engine, list_id, product_id)
        with pytest.raises(IntegrityError):
            await _insert_item(engine, list_id, product_id)

        other_product = await _insert_product(engine, name="Milk")
        bad_items: list[dict[str, object]] = [
            {"quantity": Decimal("0.000")},
            {"status": "canceled"},
            {"bought_price": Decimal("0.00")},
            {"bought_currency": "eur"},
            {"status": "pending", "bought_price": Decimal("1.00")},
            {"status": "bought", "bought_price": Decimal("1.00"), "bought_currency": "usd"},
        ]
        for bad_item in bad_items:
            with pytest.raises(IntegrityError):
                await _insert_item(engine, list_id, other_product, **bad_item)
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(_TRUNCATE_SQL))
        await engine.dispose()
