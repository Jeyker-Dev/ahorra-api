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


def _alembic_config(database_url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def _get_columns(conn: Connection) -> dict[str, dict[str, object]]:
    return {col["name"]: dict(col) for col in inspect(conn).get_columns("exchange_rates")}


def _get_index_names(conn: Connection) -> set[str]:
    return {
        idx["name"]
        for idx in inspect(conn).get_indexes("exchange_rates")
        if idx["name"] is not None
    }


@pytest.mark.usefixtures("ensure_test_database")
async def test_exchange_rates_migration_and_bcv_partial_unique_index(
    test_database_url: str,
) -> None:
    cfg = _alembic_config(test_database_url)
    await asyncio.to_thread(command.downgrade, cfg, "base")
    await asyncio.to_thread(command.upgrade, cfg, "head")

    engine = create_async_engine(test_database_url)
    try:
        async with engine.connect() as conn:
            cols = await conn.run_sync(_get_columns)
            assert set(cols) == {
                "id",
                "kind",
                "value",
                "value_date",
                "origin",
                "status",
                "fetched_at",
            }
            indexes = await conn.run_sync(_get_index_names)
            assert {
                "uq_exchange_rates_bcv_value_date",
                "ix_exchange_rates_kind_fetched_at",
            }.issubset(indexes)

        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE TABLE exchange_rates"))
            now = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
            day = date(2026, 11, 20)
            await conn.execute(
                text(
                    "INSERT INTO exchange_rates "
                    "(id, kind, value, value_date, origin, status, fetched_at) "
                    "VALUES (:id, 'bcv', :value, :value_date, 'bcv_html', 'confirmed', :fetched_at)"
                ),
                {
                    "id": str(uuid4()),
                    "value": Decimal("917.77510000"),
                    "value_date": day,
                    "fetched_at": now,
                },
            )
            await conn.execute(
                text(
                    "INSERT INTO exchange_rates "
                    "(id, kind, value, value_date, origin, status, fetched_at) "
                    "VALUES (:id, 'usdt', :value, :value_date, "
                    "'binance_p2p', 'confirmed', :fetched_at)"
                ),
                {
                    "id": str(uuid4()),
                    "value": Decimal("950.00000000"),
                    "value_date": day,
                    "fetched_at": now,
                },
            )
            await conn.execute(
                text(
                    "INSERT INTO exchange_rates "
                    "(id, kind, value, value_date, origin, status, fetched_at) "
                    "VALUES (:id, 'usdt', :value, :value_date, "
                    "'binance_p2p', 'confirmed', :fetched_at)"
                ),
                {
                    "id": str(uuid4()),
                    "value": Decimal("955.00000000"),
                    "value_date": day,
                    "fetched_at": now,
                },
            )

        with pytest.raises(IntegrityError):
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO exchange_rates "
                        "(id, kind, value, value_date, origin, status, fetched_at) "
                        "VALUES (:id, 'bcv', :value, :value_date, "
                        "'dolarapi', 'in_review', :fetched_at)"
                    ),
                    {
                        "id": str(uuid4()),
                        "value": Decimal("918.00000000"),
                        "value_date": date(2026, 11, 20),
                        "fetched_at": datetime(2026, 11, 20, 13, 0, tzinfo=UTC),
                    },
                )
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE TABLE exchange_rates"))
        await engine.dispose()
