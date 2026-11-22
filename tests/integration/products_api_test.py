import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.auth.passwords import PasswordHasher
from app.config import Settings
from app.main import create_app
from app.rates.domain import ExchangeRate, RateKind, RateOrigin, RateStatus

TEST_JWT_SECRET = "test-secret-key-with-at-least-32-chars"

_TRUNCATE_SQL = (
    "TRUNCATE TABLE product_prices, shopping_list_items, shopping_lists, "
    "products, expenses, expense_categories, exchange_rates, refresh_tokens, users CASCADE"
)


class _FakeClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def now(self) -> datetime:
        return self.current


@pytest.fixture
async def migrated_db_url(
    test_database_url: str,
    ensure_test_database: None,
) -> AsyncIterator[str]:
    _ = ensure_test_database
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", test_database_url)
    await asyncio.to_thread(command.upgrade, cfg, "head")

    engine = create_async_engine(test_database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(_TRUNCATE_SQL))
        yield test_database_url
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(_TRUNCATE_SQL))
        await engine.dispose()


def _make_settings(database_url: str) -> Settings:
    return Settings(
        _env_file=None,
        database_url=database_url,
        jwt_secret=SecretStr(TEST_JWT_SECRET),
    )


async def _create_auth_header(
    database_url: str,
    client: AsyncClient,
    now: datetime,
) -> dict[str, str]:
    password_hash = PasswordHasher().hash("correct-horse-12")
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO users "
                    "(id, email, password_hash, is_active, created_at, updated_at) "
                    "VALUES (:id, :email, :password_hash, true, :created_at, :updated_at)"
                ),
                {
                    "id": str(uuid4()),
                    "email": "alice@example.com",
                    "password_hash": password_hash,
                    "created_at": now,
                    "updated_at": now,
                },
            )
    finally:
        await engine.dispose()

    resp = await client.post(
        "/auth/token",
        data={"username": "alice@example.com", "password": "correct-horse-12"},
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _insert_rate(
    database_url: str,
    kind: RateKind,
    value_date: date,
    value: Decimal,
) -> None:
    rate = ExchangeRate(
        id=uuid4(),
        kind=kind,
        value=value,
        value_date=value_date,
        origin=RateOrigin.bcv_html if kind == RateKind.bcv else RateOrigin.binance_p2p,
        status=RateStatus.confirmed,
        fetched_at=datetime(2026, 11, 22, 12, 0, tzinfo=UTC),
    )
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO exchange_rates "
                    "(id, kind, value, value_date, origin, status, fetched_at) "
                    "VALUES (:id, :kind, :value, :value_date, :origin, :status, :fetched_at)"
                ),
                {
                    "id": str(rate.id),
                    "kind": rate.kind.value,
                    "value": rate.value,
                    "value_date": rate.value_date,
                    "origin": rate.origin.value,
                    "status": rate.status.value,
                    "fetched_at": rate.fetched_at,
                },
            )
    finally:
        await engine.dispose()


async def test_product_endpoints_require_authentication(migrated_db_url: str) -> None:
    clock = _FakeClock(datetime(2026, 11, 22, 14, 0, tzinfo=UTC))
    application = create_app(settings=_make_settings(migrated_db_url), clock=clock)
    some_id = uuid4()

    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client,
    ):
        responses = [
            await client.put(f"/products/{some_id}", json={"name": "Rice", "unit": "kg"}),
            await client.get("/products"),
            await client.get(f"/products/{some_id}"),
            await client.post(f"/products/{some_id}/archive"),
            await client.post(f"/products/{some_id}/unarchive"),
            await client.get(
                f"/products/{some_id}/prices",
                params={"from": "2026-11-01", "to": "2026-11-30"},
            ),
        ]

    assert all(resp.status_code == 401 for resp in responses)


async def test_upsert_product_lifecycle_and_price_history(migrated_db_url: str) -> None:
    clock = _FakeClock(datetime(2026, 11, 22, 14, 0, tzinfo=UTC))
    await _insert_rate(migrated_db_url, RateKind.bcv, date(2026, 11, 22), Decimal("900.12345678"))
    await _insert_rate(migrated_db_url, RateKind.usdt, date(2026, 11, 22), Decimal("950.00000000"))
    application = create_app(settings=_make_settings(migrated_db_url), clock=clock)
    product_id = uuid4()

    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client,
    ):
        headers = await _create_auth_header(migrated_db_url, client, clock.current)

        created = await client.put(
            f"/products/{product_id}",
            json={"name": "  Rice  ", "unit": "kg", "last_price": "2.50", "currency": "usd"},
            headers=headers,
        )
        assert created.status_code == 201
        body = created.json()
        assert body["id"] == str(product_id)
        assert body["name"] == "Rice"
        assert body["unit"] == "kg"
        assert body["last_price"] == "2.50"
        assert body["last_price_currency"] == "usd"
        assert "updated_at" in body

        retry = await client.put(
            f"/products/{product_id}",
            json={"name": "Rice", "unit": "kg", "last_price": "2.50", "currency": "usd"},
            headers=headers,
        )
        assert retry.status_code == 200
        assert retry.json()["updated_at"] == body["updated_at"]

        clock.current += timedelta(minutes=5)
        updated = await client.put(
            f"/products/{product_id}",
            json={"name": "White Rice", "unit": "kg", "last_price": "2.75", "currency": "usd"},
            headers=headers,
        )
        assert updated.status_code == 200
        assert updated.json()["name"] == "White Rice"
        assert updated.json()["last_price"] == "2.75"

        clock.current += timedelta(minutes=5)
        renamed_only = await client.put(
            f"/products/{product_id}",
            json={"name": "Long Grain Rice", "unit": "kg"},
            headers=headers,
        )
        assert renamed_only.status_code == 200
        assert renamed_only.json()["last_price"] == "2.75"
        assert renamed_only.json()["last_price_currency"] == "usd"

        fetched = await client.get(f"/products/{product_id}", headers=headers)
        assert fetched.status_code == 200
        assert fetched.json()["name"] == "Long Grain Rice"

        prices = await client.get(
            f"/products/{product_id}/prices",
            params={"from": "2026-11-22", "to": "2026-11-22"},
            headers=headers,
        )
        assert prices.status_code == 200
        entries = prices.json()
        assert isinstance(entries, list)
        assert [entry["price"] for entry in entries] == ["2.75", "2.50"]
        assert entries[0]["bcv_rate_value"] == "900.12345678"
        assert entries[0]["usdt_rate_value"] == "950.00000000"


async def test_list_and_archive_unarchive_products(migrated_db_url: str) -> None:
    clock = _FakeClock(datetime(2026, 11, 22, 14, 0, tzinfo=UTC))
    application = create_app(settings=_make_settings(migrated_db_url), clock=clock)
    first_id = uuid4()
    second_id = uuid4()

    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client,
    ):
        headers = await _create_auth_header(migrated_db_url, client, clock.current)
        await client.put(
            f"/products/{first_id}",
            json={"name": "Apples", "unit": "kg"},
            headers=headers,
        )
        await client.put(
            f"/products/{second_id}",
            json={"name": "Zucchini", "unit": "kg"},
            headers=headers,
        )

        archived = await client.post(f"/products/{first_id}/archive", headers=headers)
        assert archived.status_code == 200
        assert archived.json()["archived_at"] is not None

        active_only = await client.get("/products", headers=headers)
        assert active_only.status_code == 200
        assert [item["id"] for item in active_only.json()] == [str(second_id)]

        all_items = await client.get(
            "/products",
            params={"include_archived": "true"},
            headers=headers,
        )
        assert all_items.status_code == 200
        assert [item["id"] for item in all_items.json()] == [str(second_id), str(first_id)]

        unarchived = await client.post(f"/products/{first_id}/unarchive", headers=headers)
        assert unarchived.status_code == 200
        assert unarchived.json()["archived_at"] is None


async def test_product_not_found_returns_404(migrated_db_url: str) -> None:
    clock = _FakeClock(datetime(2026, 11, 22, 14, 0, tzinfo=UTC))
    application = create_app(settings=_make_settings(migrated_db_url), clock=clock)
    missing_id = uuid4()

    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client,
    ):
        headers = await _create_auth_header(migrated_db_url, client, clock.current)
        responses = [
            await client.get(f"/products/{missing_id}", headers=headers),
            await client.post(f"/products/{missing_id}/archive", headers=headers),
            await client.post(f"/products/{missing_id}/unarchive", headers=headers),
            await client.get(
                f"/products/{missing_id}/prices",
                params={"from": "2026-11-01", "to": "2026-11-30"},
                headers=headers,
            ),
        ]

    assert all(resp.status_code == 404 for resp in responses)


async def test_product_conflicts_return_409(migrated_db_url: str) -> None:
    clock = _FakeClock(datetime(2026, 11, 22, 14, 0, tzinfo=UTC))
    application = create_app(settings=_make_settings(migrated_db_url), clock=clock)

    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client,
    ):
        headers = await _create_auth_header(migrated_db_url, client, clock.current)

        missing_rate = await client.put(
            f"/products/{uuid4()}",
            json={"name": "Milk", "unit": "l", "last_price": "1.50", "currency": "usd"},
            headers=headers,
        )
        assert missing_rate.status_code == 409

        await client.put(
            f"/products/{uuid4()}",
            json={"name": "Coffee", "unit": "kg"},
            headers=headers,
        )
        name_taken = await client.put(
            f"/products/{uuid4()}",
            json={"name": "coffee", "unit": "kg"},
            headers=headers,
        )
        assert name_taken.status_code == 409


async def test_product_validation_errors_return_422(migrated_db_url: str) -> None:
    clock = _FakeClock(datetime(2026, 11, 22, 14, 0, tzinfo=UTC))
    application = create_app(settings=_make_settings(migrated_db_url), clock=clock)
    product_id = uuid4()

    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client,
    ):
        headers = await _create_auth_header(migrated_db_url, client, clock.current)

        bad_name = await client.put(
            f"/products/{product_id}",
            json={"name": "   ", "unit": "unit"},
            headers=headers,
        )
        assert bad_name.status_code == 422

        bad_price = await client.put(
            f"/products/{product_id}",
            json={"name": "Soap", "unit": "unit", "last_price": "0", "currency": "usd"},
            headers=headers,
        )
        assert bad_price.status_code == 422

        incomplete_pair = await client.put(
            f"/products/{product_id}",
            json={"name": "Soap", "unit": "unit", "last_price": "1.00"},
            headers=headers,
        )
        assert incomplete_pair.status_code == 422

        bad_period = await client.get(
            f"/products/{product_id}/prices",
            params={"from": "2026-11-30", "to": "2026-11-01"},
            headers=headers,
        )
        assert bad_period.status_code == 422

        missing_query = await client.get(f"/products/{product_id}/prices", headers=headers)
        assert missing_query.status_code == 422
