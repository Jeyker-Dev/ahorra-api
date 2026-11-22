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


def _get_unique_names(conn: Connection, table: str) -> set[str]:
    return {
        uc["name"] for uc in inspect(conn).get_unique_constraints(table) if uc["name"] is not None
    }


async def _insert_rule(engine: AsyncEngine, **overrides: object) -> UUID:
    resolved_id = uuid4()
    now = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
    params: dict[str, object] = {
        "id": str(resolved_id),
        "name": "Salary",
        "amount": Decimal("500.00"),
        "currency": "usd",
        "days": [15],
        "start_date": date(2026, 11, 1),
        "end_date": None,
        "created_at": now,
        "updated_at": now,
    }
    params.update(overrides)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO income_rules "
                "(id, name, amount, currency, days, start_date, end_date, "
                "ended_at, created_at, updated_at) "
                "VALUES (:id, :name, :amount, :currency, :days, :start_date, :end_date, "
                "NULL, :created_at, :updated_at)"
            ),
            params,
        )
    return resolved_id


async def _insert_occurrence(engine: AsyncEngine, rule_id: UUID, **overrides: object) -> UUID:
    occ_id = uuid4()
    now = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
    params: dict[str, object] = {
        "id": str(occ_id),
        "rule_id": str(rule_id),
        "expected_date": date(2026, 11, 15),
        "expected_amount": Decimal("500.00"),
        "currency": "usd",
        "status": "expected",
        "received_date": None,
        "received_amount": None,
        "rate_value": None,
        "rate_date": None,
        "received_at": None,
        "updated_at": now,
    }
    params.update(overrides)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO income_occurrences "
                "(id, rule_id, expected_date, expected_amount, currency, status, "
                "received_date, received_amount, rate_value, rate_date, received_at, updated_at) "
                "VALUES (:id, :rule_id, :expected_date, :expected_amount, :currency, :status, "
                ":received_date, :received_amount, :rate_value, :rate_date, :received_at, "
                ":updated_at)"
            ),
            params,
        )
    return occ_id


async def _assert_rule_checks(engine: AsyncEngine) -> None:
    bad_rules: list[dict[str, object]] = [
        {"name": "   "},
        {"amount": Decimal("0")},
        {"currency": "eur"},
        {"days": []},
        {"start_date": date(2026, 11, 10), "end_date": date(2026, 11, 9)},
    ]
    for overrides in bad_rules:
        with pytest.raises(IntegrityError):
            await _insert_rule(engine, **overrides)


async def _assert_occurrence_checks(engine: AsyncEngine, rule_id: UUID, now: datetime) -> None:
    bad_occurrences: list[dict[str, object]] = [
        {"expected_date": date(2027, 1, 1), "expected_amount": Decimal("0")},
        {"expected_date": date(2027, 1, 2), "currency": "eur"},
        {"expected_date": date(2027, 1, 3), "status": "skipped"},
        {
            "expected_date": date(2027, 1, 4),
            "status": "expected",
            "received_date": date(2027, 1, 4),
        },
        {
            "expected_date": date(2027, 1, 5),
            "status": "received",
            "received_date": date(2027, 1, 5),
            "received_amount": Decimal("500.00"),
            "rate_value": Decimal("920.00000000"),
            "rate_date": date(2027, 1, 5),
            "received_at": None,
        },
        {
            "expected_date": date(2027, 1, 6),
            "status": "received",
            "received_date": date(2027, 1, 6),
            "received_amount": Decimal("-1.00"),
            "rate_value": Decimal("920.00000000"),
            "rate_date": date(2027, 1, 6),
            "received_at": now,
        },
        {
            "expected_date": date(2027, 1, 7),
            "status": "received",
            "received_date": date(2027, 1, 7),
            "received_amount": Decimal("500.00"),
            "rate_value": Decimal("0"),
            "rate_date": date(2027, 1, 7),
            "received_at": now,
        },
    ]
    for overrides in bad_occurrences:
        with pytest.raises(IntegrityError):
            await _insert_occurrence(engine, rule_id, **overrides)


@pytest.mark.usefixtures("ensure_test_database")
async def test_income_migration_creates_and_drops_tables(test_database_url: str) -> None:
    cfg = _alembic_config(test_database_url)
    await asyncio.to_thread(command.downgrade, cfg, "base")
    await asyncio.to_thread(command.upgrade, cfg, "head")

    engine = create_async_engine(test_database_url)
    try:
        async with engine.connect() as conn:
            assert {"income_rules", "income_occurrences"}.issubset(
                await conn.run_sync(_get_table_names)
            )
            assert set(await conn.run_sync(_get_columns, "income_rules")) == {
                "id",
                "name",
                "amount",
                "currency",
                "days",
                "start_date",
                "end_date",
                "ended_at",
                "created_at",
                "updated_at",
            }
            assert set(await conn.run_sync(_get_columns, "income_occurrences")) == {
                "id",
                "rule_id",
                "expected_date",
                "expected_amount",
                "currency",
                "status",
                "received_date",
                "received_amount",
                "rate_value",
                "rate_date",
                "received_at",
                "updated_at",
            }
            assert "ix_income_occurrences_expected_date" in await conn.run_sync(
                _get_index_names,
                "income_occurrences",
            )
            assert "uq_income_occurrences_rule_expected_date" in await conn.run_sync(
                _get_unique_names,
                "income_occurrences",
            )
            fks = await conn.run_sync(lambda c: inspect(c).get_foreign_keys("income_occurrences"))
            assert len(fks) == 1
            assert fks[0]["referred_table"] == "income_rules"
            assert fks[0]["options"].get("ondelete") == "RESTRICT"

        await asyncio.to_thread(command.downgrade, cfg, "base")
        async with engine.connect() as conn:
            tables_after = await conn.run_sync(_get_table_names)
            assert "income_rules" not in tables_after
            assert "income_occurrences" not in tables_after
        await asyncio.to_thread(command.upgrade, cfg, "head")
    finally:
        await engine.dispose()


@pytest.mark.usefixtures("ensure_test_database")
async def test_income_migration_enforces_unique_fk_and_check_constraints(
    test_database_url: str,
) -> None:
    cfg = _alembic_config(test_database_url)
    await asyncio.to_thread(command.upgrade, cfg, "head")

    engine = create_async_engine(test_database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE TABLE income_occurrences, income_rules CASCADE"))

        await _assert_rule_checks(engine)
        rule_id = await _insert_rule(engine)
        now = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)

        await _insert_occurrence(engine, rule_id, expected_date=date(2026, 11, 15))
        await _insert_occurrence(
            engine,
            rule_id,
            expected_date=date(2026, 12, 15),
            status="received",
            received_date=date(2026, 12, 15),
            received_amount=Decimal("500.00"),
            rate_value=Decimal("920.00000000"),
            rate_date=date(2026, 12, 15),
            received_at=now,
        )

        with pytest.raises(IntegrityError):
            await _insert_occurrence(engine, rule_id, expected_date=date(2026, 11, 15))

        with pytest.raises(IntegrityError):
            async with engine.begin() as conn:
                await conn.execute(
                    text("DELETE FROM income_rules WHERE id = :id"),
                    {"id": str(rule_id)},
                )

        await _assert_occurrence_checks(engine, rule_id, now)
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE TABLE income_occurrences, income_rules CASCADE"))
        await engine.dispose()
