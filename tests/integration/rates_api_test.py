import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
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
            await conn.execute(text("TRUNCATE TABLE exchange_rates, refresh_tokens, users CASCADE"))
        yield test_database_url
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE TABLE exchange_rates, refresh_tokens, users CASCADE"))
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
    access_token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {access_token}"}


async def _insert_rate(database_url: str, rate: ExchangeRate) -> str:
    rate_id = str(rate.id)
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
                    "id": rate_id,
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
    return rate_id


async def test_rates_endpoints_require_authentication(migrated_db_url: str) -> None:
    fixed_now = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)
    application = create_app(settings=_make_settings(migrated_db_url), clock=_FakeClock(fixed_now))

    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client,
    ):
        latest_resp = await client.get("/rates/latest")
        history_resp = await client.get("/rates", params={"kind": "bcv"})

    assert latest_resp.status_code == 401
    assert history_resp.status_code == 401


async def test_get_latest_rates_empty_and_populated(migrated_db_url: str) -> None:
    fixed_now = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)
    application = create_app(settings=_make_settings(migrated_db_url), clock=_FakeClock(fixed_now))

    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client,
    ):
        headers = await _create_auth_header(migrated_db_url, client, fixed_now)

        empty_resp = await client.get("/rates/latest", headers=headers)
        assert empty_resp.status_code == 200
        assert empty_resp.json() == {"bcv": None, "usdt": None}

        await _insert_rate(
            migrated_db_url,
            ExchangeRate(
                id=uuid4(),
                kind=RateKind.bcv,
                value=Decimal("915.00000000"),
                value_date=date(2026, 11, 19),
                origin=RateOrigin.bcv_html,
                status=RateStatus.confirmed,
                fetched_at=datetime(2026, 11, 19, 13, 0, tzinfo=UTC),
            ),
        )
        bcv_latest_id = await _insert_rate(
            migrated_db_url,
            ExchangeRate(
                id=uuid4(),
                kind=RateKind.bcv,
                value=Decimal("917.77510000"),
                value_date=date(2026, 11, 20),
                origin=RateOrigin.dolarapi,
                status=RateStatus.in_review,
                fetched_at=datetime(2026, 11, 20, 13, 0, tzinfo=UTC),
            ),
        )
        usdt_latest_id = await _insert_rate(
            migrated_db_url,
            ExchangeRate(
                id=uuid4(),
                kind=RateKind.usdt,
                value=Decimal("952.50000000"),
                value_date=date(2026, 11, 20),
                origin=RateOrigin.binance_p2p,
                status=RateStatus.confirmed,
                fetched_at=datetime(2026, 11, 20, 14, 0, tzinfo=UTC),
            ),
        )

        populated_resp = await client.get("/rates/latest", headers=headers)

    assert populated_resp.status_code == 200
    body = populated_resp.json()
    assert body["bcv"]["id"] == bcv_latest_id
    assert body["bcv"]["value"] == "917.77510000"
    assert body["bcv"]["status"] == "in_review"
    assert body["bcv"]["origin"] == "dolarapi"
    assert body["usdt"]["id"] == usdt_latest_id
    assert body["usdt"]["value"] == "952.50000000"
    assert body["usdt"]["status"] == "confirmed"


async def test_get_rates_history_filters_orders_and_validates_range(migrated_db_url: str) -> None:
    fixed_now = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)
    application = create_app(settings=_make_settings(migrated_db_url), clock=_FakeClock(fixed_now))

    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client,
    ):
        headers = await _create_auth_header(migrated_db_url, client, fixed_now)

        await _insert_rate(
            migrated_db_url,
            ExchangeRate(
                id=uuid4(),
                kind=RateKind.usdt,
                value=Decimal("940.00000000"),
                value_date=date(2026, 10, 10),
                origin=RateOrigin.binance_p2p,
                status=RateStatus.confirmed,
                fetched_at=datetime(2026, 10, 10, 12, 0, tzinfo=UTC),
            ),
        )
        second_id = await _insert_rate(
            migrated_db_url,
            ExchangeRate(
                id=uuid4(),
                kind=RateKind.usdt,
                value=Decimal("955.00000000"),
                value_date=date(2026, 11, 18),
                origin=RateOrigin.binance_p2p,
                status=RateStatus.confirmed,
                fetched_at=datetime(2026, 11, 18, 15, 0, tzinfo=UTC),
            ),
        )
        first_id = await _insert_rate(
            migrated_db_url,
            ExchangeRate(
                id=uuid4(),
                kind=RateKind.usdt,
                value=Decimal("950.00000000"),
                value_date=date(2026, 11, 18),
                origin=RateOrigin.binance_p2p,
                status=RateStatus.confirmed,
                fetched_at=datetime(2026, 11, 18, 14, 0, tzinfo=UTC),
            ),
        )
        third_id = await _insert_rate(
            migrated_db_url,
            ExchangeRate(
                id=uuid4(),
                kind=RateKind.usdt,
                value=Decimal("960.00000000"),
                value_date=date(2026, 11, 20),
                origin=RateOrigin.binance_p2p,
                status=RateStatus.confirmed,
                fetched_at=datetime(2026, 11, 20, 14, 0, tzinfo=UTC),
            ),
        )
        await _insert_rate(
            migrated_db_url,
            ExchangeRate(
                id=uuid4(),
                kind=RateKind.bcv,
                value=Decimal("917.77510000"),
                value_date=date(2026, 11, 20),
                origin=RateOrigin.bcv_html,
                status=RateStatus.confirmed,
                fetched_at=datetime(2026, 11, 20, 14, 0, tzinfo=UTC),
            ),
        )

        filtered_resp = await client.get(
            "/rates",
            params={"kind": "usdt", "from": "2026-11-18", "to": "2026-11-20"},
            headers=headers,
        )
        default_range_resp = await client.get(
            "/rates",
            params={"kind": "usdt"},
            headers=headers,
        )
        bad_range_resp = await client.get(
            "/rates",
            params={"kind": "bcv", "from": "2026-11-21", "to": "2026-11-20"},
            headers=headers,
        )
        missing_kind_resp = await client.get("/rates", headers=headers)

    assert filtered_resp.status_code == 200
    items = filtered_resp.json()
    assert [item["id"] for item in items] == [first_id, second_id, third_id]
    assert [item["value"] for item in items] == ["950.00000000", "955.00000000", "960.00000000"]

    assert default_range_resp.status_code == 200
    assert [item["id"] for item in default_range_resp.json()] == [first_id, second_id, third_id]

    assert bad_range_resp.status_code == 422
    assert missing_kind_resp.status_code == 422


async def test_get_latest_bcv_excludes_future_value_date_and_keeps_usdt_by_fetched_at(
    migrated_db_url: str,
) -> None:
    # 2026-11-21 02:00 UTC is 2026-11-20 22:00 in America/Caracas.
    fixed_now = datetime(2026, 11, 21, 2, 0, tzinfo=UTC)
    application = create_app(settings=_make_settings(migrated_db_url), clock=_FakeClock(fixed_now))

    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client,
    ):
        headers = await _create_auth_header(migrated_db_url, client, fixed_now)

        await _insert_rate(
            migrated_db_url,
            ExchangeRate(
                id=uuid4(),
                kind=RateKind.bcv,
                value=Decimal("915.00000000"),
                value_date=date(2026, 11, 19),
                origin=RateOrigin.bcv_html,
                status=RateStatus.confirmed,
                fetched_at=datetime(2026, 11, 19, 13, 0, tzinfo=UTC),
            ),
        )
        in_force_id = await _insert_rate(
            migrated_db_url,
            ExchangeRate(
                id=uuid4(),
                kind=RateKind.bcv,
                value=Decimal("917.77510000"),
                value_date=date(2026, 11, 20),
                origin=RateOrigin.bcv_html,
                status=RateStatus.confirmed,
                fetched_at=datetime(2026, 11, 20, 13, 0, tzinfo=UTC),
            ),
        )
        await _insert_rate(
            migrated_db_url,
            ExchangeRate(
                id=uuid4(),
                kind=RateKind.bcv,
                value=Decimal("920.00000000"),
                value_date=date(2026, 11, 21),
                origin=RateOrigin.bcv_html,
                status=RateStatus.confirmed,
                fetched_at=datetime(2026, 11, 21, 1, 0, tzinfo=UTC),
            ),
        )
        await _insert_rate(
            migrated_db_url,
            ExchangeRate(
                id=uuid4(),
                kind=RateKind.bcv,
                value=Decimal("925.00000000"),
                value_date=date(2026, 11, 24),
                origin=RateOrigin.bcv_html,
                status=RateStatus.confirmed,
                fetched_at=datetime(2026, 11, 20, 21, 0, tzinfo=UTC),
            ),
        )
        await _insert_rate(
            migrated_db_url,
            ExchangeRate(
                id=uuid4(),
                kind=RateKind.usdt,
                value=Decimal("950.00000000"),
                value_date=date(2026, 11, 20),
                origin=RateOrigin.binance_p2p,
                status=RateStatus.confirmed,
                fetched_at=datetime(2026, 11, 20, 14, 0, tzinfo=UTC),
            ),
        )
        usdt_latest_id = await _insert_rate(
            migrated_db_url,
            ExchangeRate(
                id=uuid4(),
                kind=RateKind.usdt,
                value=Decimal("955.00000000"),
                value_date=date(2026, 11, 20),
                origin=RateOrigin.binance_p2p,
                status=RateStatus.confirmed,
                fetched_at=datetime(2026, 11, 21, 1, 30, tzinfo=UTC),
            ),
        )

        resp = await client.get("/rates/latest", headers=headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["bcv"]["id"] == in_force_id
    assert body["bcv"]["value"] == "917.77510000"
    assert body["bcv"]["value_date"] == "2026-11-20"
    assert body["usdt"]["id"] == usdt_latest_id
    assert body["usdt"]["value"] == "955.00000000"


async def test_get_latest_returns_null_bcv_when_only_future_bcv_exists(
    migrated_db_url: str,
) -> None:
    fixed_now = datetime(2026, 11, 20, 21, 30, tzinfo=UTC)
    application = create_app(settings=_make_settings(migrated_db_url), clock=_FakeClock(fixed_now))

    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client,
    ):
        headers = await _create_auth_header(migrated_db_url, client, fixed_now)

        await _insert_rate(
            migrated_db_url,
            ExchangeRate(
                id=uuid4(),
                kind=RateKind.bcv,
                value=Decimal("925.00000000"),
                value_date=date(2026, 11, 24),
                origin=RateOrigin.bcv_html,
                status=RateStatus.confirmed,
                fetched_at=datetime(2026, 11, 20, 21, 0, tzinfo=UTC),
            ),
        )
        usdt_id = await _insert_rate(
            migrated_db_url,
            ExchangeRate(
                id=uuid4(),
                kind=RateKind.usdt,
                value=Decimal("955.00000000"),
                value_date=date(2026, 11, 20),
                origin=RateOrigin.binance_p2p,
                status=RateStatus.confirmed,
                fetched_at=datetime(2026, 11, 20, 20, 0, tzinfo=UTC),
            ),
        )

        resp = await client.get("/rates/latest", headers=headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["bcv"] is None
    assert body["usdt"]["id"] == usdt_id
