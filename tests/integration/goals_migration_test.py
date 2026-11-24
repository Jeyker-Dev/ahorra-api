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

_TRUNCATE_SQL = "TRUNCATE TABLE goal_contributions, goals CASCADE"


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


def _get_fk_ondelete(conn: Connection, table: str) -> list[str | None]:
    return [fk["options"].get("ondelete") for fk in inspect(conn).get_foreign_keys(table)]


async def _insert_goal(engine: AsyncEngine, **overrides: object) -> UUID:
    goal_id = uuid4()
    now = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
    params: dict[str, object] = {
        "id": str(goal_id),
        "name": f"Goal-{goal_id}",
        "target_usd": Decimal("500.00"),
        "target_date": None,
        "note": None,
        "archived_at": None,
        "deleted_at": None,
        "created_at": now,
        "updated_at": now,
    }
    params.update(overrides)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO goals "
                "(id, name, target_usd, target_date, note, "
                "archived_at, deleted_at, created_at, updated_at) "
                "VALUES (:id, :name, :target_usd, :target_date, :note, "
                ":archived_at, :deleted_at, :created_at, :updated_at)"
            ),
            params,
        )
    return goal_id


async def _insert_contribution(
    engine: AsyncEngine,
    goal_id: UUID,
    **overrides: object,
) -> UUID:
    contrib_id = uuid4()
    now = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
    params: dict[str, object] = {
        "id": str(contrib_id),
        "goal_id": str(goal_id),
        "kind": "deposit",
        "amount": Decimal("50.00"),
        "currency": "usd",
        "contributed_on": date(2026, 11, 23),
        "note": None,
        "bcv_rate_value": Decimal("900.00000000"),
        "bcv_rate_date": date(2026, 11, 23),
        "usdt_rate_value": Decimal("950.00000000"),
        "usdt_rate_date": date(2026, 11, 23),
        "deleted_at": None,
        "created_at": now,
        "updated_at": now,
    }
    params.update(overrides)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO goal_contributions "
                "(id, goal_id, kind, amount, currency, contributed_on, note, "
                "bcv_rate_value, bcv_rate_date, usdt_rate_value, usdt_rate_date, "
                "deleted_at, created_at, updated_at) "
                "VALUES (:id, :goal_id, :kind, :amount, :currency, :contributed_on, :note, "
                ":bcv_rate_value, :bcv_rate_date, :usdt_rate_value, :usdt_rate_date, "
                ":deleted_at, :created_at, :updated_at)"
            ),
            params,
        )
    return contrib_id


@pytest.mark.usefixtures("ensure_test_database")
async def test_goals_migration_creates_and_drops_tables(test_database_url: str) -> None:
    cfg = _alembic_config(test_database_url)
    await asyncio.to_thread(command.downgrade, cfg, "base")
    await asyncio.to_thread(command.upgrade, cfg, "head")

    engine = create_async_engine(test_database_url)
    try:
        async with engine.connect() as conn:
            assert {"goals", "goal_contributions"}.issubset(await conn.run_sync(_get_table_names))
            assert set(await conn.run_sync(_get_columns, "goals")) == {
                "id",
                "name",
                "target_usd",
                "target_date",
                "note",
                "archived_at",
                "deleted_at",
                "created_at",
                "updated_at",
            }
            assert set(await conn.run_sync(_get_columns, "goal_contributions")) == {
                "id",
                "goal_id",
                "kind",
                "amount",
                "currency",
                "contributed_on",
                "note",
                "bcv_rate_value",
                "bcv_rate_date",
                "usdt_rate_value",
                "usdt_rate_date",
                "deleted_at",
                "created_at",
                "updated_at",
            }
            assert "uq_goals_lower_name_active" in await conn.run_sync(_get_index_names, "goals")
            assert "ix_goal_contributions_goal_id_contributed_on" in await conn.run_sync(
                _get_index_names, "goal_contributions"
            )
            assert await conn.run_sync(_get_fk_ondelete, "goal_contributions") == ["RESTRICT"]

        await asyncio.to_thread(command.downgrade, cfg, "4f4eb979f775")
        async with engine.connect() as conn:
            tables_after = await conn.run_sync(_get_table_names)
            assert "goals" not in tables_after
            assert "goal_contributions" not in tables_after
        await asyncio.to_thread(command.upgrade, cfg, "head")
    finally:
        await engine.dispose()


@pytest.mark.usefixtures("ensure_test_database")
async def test_goals_constraints_enforced(test_database_url: str) -> None:
    cfg = _alembic_config(test_database_url)
    await asyncio.to_thread(command.upgrade, cfg, "head")

    engine = create_async_engine(test_database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(_TRUNCATE_SQL))

        await _insert_goal(engine, name="Emergency Fund")
        with pytest.raises(IntegrityError):
            await _insert_goal(engine, name="emergency fund")

        # Soft-deleted goal's name can be reused
        await _insert_goal(
            engine,
            name="Vacation",
            deleted_at=datetime(2026, 11, 23, 12, 0, tzinfo=UTC),
        )
        await _insert_goal(engine, name="vacation")

        bad_goals: list[dict[str, object]] = [
            {"name": "   "},
            {"target_usd": Decimal("0")},
            {"target_usd": Decimal("-10.00")},
            {"note": "   "},
        ]
        for overrides in bad_goals:
            with pytest.raises(IntegrityError):
                await _insert_goal(engine, **overrides)
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(_TRUNCATE_SQL))
        await engine.dispose()


@pytest.mark.usefixtures("ensure_test_database")
async def test_goal_contributions_constraints_and_fk_enforced(test_database_url: str) -> None:
    cfg = _alembic_config(test_database_url)
    await asyncio.to_thread(command.upgrade, cfg, "head")

    engine = create_async_engine(test_database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(_TRUNCATE_SQL))

        goal_id = await _insert_goal(engine, name="Laptop")
        await _insert_contribution(engine, goal_id)

        with pytest.raises(IntegrityError):
            async with engine.begin() as conn:
                await conn.execute(
                    text("DELETE FROM goals WHERE id = :id"),
                    {"id": str(goal_id)},
                )

        bad_contributions: list[dict[str, object]] = [
            {"kind": "transfer"},
            {"amount": Decimal("0")},
            {"currency": "eur"},
            {"note": "   "},
            {"bcv_rate_value": Decimal("0")},
            {"usdt_rate_value": Decimal("0")},
        ]
        for overrides in bad_contributions:
            with pytest.raises(IntegrityError):
                await _insert_contribution(engine, goal_id, **overrides)
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(_TRUNCATE_SQL))
        await engine.dispose()
