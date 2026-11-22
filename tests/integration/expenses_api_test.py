import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

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
            await conn.execute(
                text(
                    "TRUNCATE TABLE expenses, expense_categories, "
                    "exchange_rates, refresh_tokens, users CASCADE"
                )
            )
        yield test_database_url
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "TRUNCATE TABLE expenses, expense_categories, "
                    "exchange_rates, refresh_tokens, users CASCADE"
                )
            )
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
        fetched_at=datetime(2026, 11, 20, 12, 0, tzinfo=UTC),
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


async def test_expense_endpoints_require_authentication(migrated_db_url: str) -> None:
    clock = _FakeClock(datetime(2026, 11, 22, 14, 0, tzinfo=UTC))
    application = create_app(settings=_make_settings(migrated_db_url), clock=clock)
    some_id = uuid4()

    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client,
    ):
        responses = [
            await client.put(f"/expenses/categories/{some_id}", json={"name": "Food"}),
            await client.get("/expenses/categories"),
            await client.post(f"/expenses/categories/{some_id}/archive"),
            await client.post(f"/expenses/categories/{some_id}/unarchive"),
            await client.put(
                f"/expenses/{some_id}",
                json={
                    "category_id": str(some_id),
                    "amount": "10.00",
                    "currency": "usd",
                    "spent_on": "2026-11-20",
                },
            ),
            await client.get(f"/expenses/{some_id}"),
            await client.delete(f"/expenses/{some_id}"),
            await client.get(
                "/expenses",
                params={"from": "2026-11-01", "to": "2026-11-30"},
            ),
            await client.get(
                "/expenses/summary",
                params={"from": "2026-11-01", "to": "2026-11-30"},
            ),
        ]

    assert all(resp.status_code == 401 for resp in responses)


async def test_category_lifecycle_idempotency_conflicts_and_ordering(
    migrated_db_url: str,
) -> None:
    clock = _FakeClock(datetime(2026, 11, 22, 14, 0, tzinfo=UTC))
    application = create_app(settings=_make_settings(migrated_db_url), clock=clock)
    cat_food = uuid4()
    cat_transit = uuid4()
    cat_dup = uuid4()

    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client,
    ):
        headers = await _create_auth_header(migrated_db_url, client, clock.current)

        # Create 201, retry 200
        create_resp = await client.put(
            f"/expenses/categories/{cat_food}",
            json={"name": "  Food "},
            headers=headers,
        )
        assert create_resp.status_code == 201
        created = create_resp.json()
        assert created["name"] == "Food"
        assert created["archived_at"] is None

        clock.current = datetime(2026, 11, 22, 14, 5, tzinfo=UTC)
        retry_resp = await client.put(
            f"/expenses/categories/{cat_food}",
            json={"name": "Food"},
            headers=headers,
        )
        assert retry_resp.status_code == 200
        assert retry_resp.json() == created

        # Case-only rename on same id -> 200
        rename_resp = await client.put(
            f"/expenses/categories/{cat_food}",
            json={"name": "food"},
            headers=headers,
        )
        assert rename_resp.status_code == 200
        assert rename_resp.json()["name"] == "food"

        # Case-insensitive duplicate on different id -> 409
        dup_resp = await client.put(
            f"/expenses/categories/{cat_dup}",
            json={"name": "FOOD"},
            headers=headers,
        )
        assert dup_resp.status_code == 409

        # Invalid category name -> 422
        bad_resp = await client.put(
            f"/expenses/categories/{uuid4()}",
            json={"name": "   "},
            headers=headers,
        )
        assert bad_resp.status_code == 422

        # Create Transit, archive food -> test archive/unarchive & list ordering
        await client.put(
            f"/expenses/categories/{cat_transit}",
            json={"name": "Transit"},
            headers=headers,
        )
        clock.current = datetime(2026, 11, 22, 14, 10, tzinfo=UTC)
        arch_resp = await client.post(
            f"/expenses/categories/{cat_food}/archive",
            headers=headers,
        )
        assert arch_resp.status_code == 200
        assert arch_resp.json()["archived_at"] is not None

        # Active only by default
        active_list = await client.get("/expenses/categories", headers=headers)
        assert active_list.status_code == 200
        assert [c["id"] for c in active_list.json()] == [str(cat_transit)]

        # Include archived -> active first, then archived
        all_list = await client.get(
            "/expenses/categories",
            params={"include_archived": "true"},
            headers=headers,
        )
        assert all_list.status_code == 200
        assert [c["id"] for c in all_list.json()] == [str(cat_transit), str(cat_food)]

        # Unarchive -> back to active list
        unarch_resp = await client.post(
            f"/expenses/categories/{cat_food}/unarchive",
            headers=headers,
        )
        assert unarch_resp.status_code == 200
        assert unarch_resp.json()["archived_at"] is None


async def _seed_rates_and_categories(
    migrated_db_url: str,
    client: AsyncClient,
    headers: dict[str, str],
    cat_food: UUID,
    cat_transit: UUID,
) -> None:
    await _insert_rate(migrated_db_url, RateKind.bcv, date(2026, 11, 10), Decimal("300.00"))
    await _insert_rate(migrated_db_url, RateKind.usdt, date(2026, 11, 10), Decimal("320.00"))
    await _insert_rate(migrated_db_url, RateKind.bcv, date(2026, 11, 15), Decimal("310.00"))
    await _insert_rate(migrated_db_url, RateKind.usdt, date(2026, 11, 15), Decimal("330.00"))
    await client.put(f"/expenses/categories/{cat_food}", json={"name": "Food"}, headers=headers)
    await client.put(
        f"/expenses/categories/{cat_transit}",
        json={"name": "Transit"},
        headers=headers,
    )


async def test_expense_crud_rates_and_route_ordering(
    migrated_db_url: str,
) -> None:
    clock = _FakeClock(datetime(2026, 11, 22, 14, 0, tzinfo=UTC))
    application = create_app(settings=_make_settings(migrated_db_url), clock=clock)
    cat_food = uuid4()
    cat_transit = uuid4()
    exp_id = uuid4()

    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client,
    ):
        headers = await _create_auth_header(migrated_db_url, client, clock.current)

        # Static routes on empty DB must not be captured by /{expense_id}
        empty_cats = await client.get("/expenses/categories", headers=headers)
        assert empty_cats.status_code == 200
        assert empty_cats.json() == []

        empty_summary = await client.get(
            "/expenses/summary",
            params={"from": "2026-11-01", "to": "2026-11-30"},
            headers=headers,
        )
        assert empty_summary.status_code == 200
        assert empty_summary.json() == []

        await _seed_rates_and_categories(migrated_db_url, client, headers, cat_food, cat_transit)

        # Create 201 + repeat 200
        create_exp = await client.put(
            f"/expenses/{exp_id}",
            json={
                "category_id": str(cat_food),
                "amount": "10.00",
                "currency": "usd",
                "spent_on": "2026-11-10",
                "note": "  Lunch ",
            },
            headers=headers,
        )
        assert create_exp.status_code == 201
        exp_body = create_exp.json()
        assert exp_body["category_name"] == "Food"
        assert exp_body["amount"] == "10.00"
        assert exp_body["note"] == "Lunch"
        assert exp_body["bcv_rate_value"] == "300.00000000"
        assert exp_body["usdt_rate_value"] == "320.00000000"
        assert exp_body["deleted_at"] is None

        repeat_exp = await client.put(
            f"/expenses/{exp_id}",
            json={
                "category_id": str(cat_food),
                "amount": "10.00",
                "currency": "usd",
                "spent_on": "2026-11-10",
                "note": "Lunch",
            },
            headers=headers,
        )
        assert repeat_exp.status_code == 200
        assert repeat_exp.json() == exp_body

        # Changing spent_on refreshes both BCV and USDT rates
        edit_date_resp = await client.put(
            f"/expenses/{exp_id}",
            json={
                "category_id": str(cat_food),
                "amount": "10.00",
                "currency": "usd",
                "spent_on": "2026-11-15",
                "note": "   ",
            },
            headers=headers,
        )
        assert edit_date_resp.status_code == 200
        assert edit_date_resp.json()["note"] is None
        assert edit_date_resp.json()["bcv_rate_value"] == "310.00000000"
        assert edit_date_resp.json()["usdt_rate_value"] == "330.00000000"

        get_resp = await client.get(f"/expenses/{exp_id}", headers=headers)
        assert get_resp.status_code == 200
        assert get_resp.json()["category_name"] == "Food"


async def test_expense_soft_delete_list_and_summary(
    migrated_db_url: str,
) -> None:
    clock = _FakeClock(datetime(2026, 11, 22, 14, 0, tzinfo=UTC))
    application = create_app(settings=_make_settings(migrated_db_url), clock=clock)
    cat_food = uuid4()
    cat_transit = uuid4()
    exp1_id = uuid4()
    exp2_id = uuid4()
    exp3_id = uuid4()
    exp_transit = uuid4()

    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client,
    ):
        headers = await _create_auth_header(migrated_db_url, client, clock.current)
        await _seed_rates_and_categories(migrated_db_url, client, headers, cat_food, cat_transit)

        for eid, cat_id, amt, curr, day in (
            (exp1_id, cat_food, "10.00", "usd", "2026-11-10"),
            (exp2_id, cat_food, "1000.00", "ves", "2026-11-12"),
            (exp3_id, cat_food, "1000.00", "ves", "2026-11-14"),
            (exp_transit, cat_transit, "20.00", "usd", "2026-11-15"),
        ):
            await client.put(
                f"/expenses/{eid}",
                json={
                    "category_id": str(cat_id),
                    "amount": amt,
                    "currency": curr,
                    "spent_on": day,
                },
                headers=headers,
            )

        summary_resp = await client.get(
            "/expenses/summary",
            params={"from": "2026-11-01", "to": "2026-11-30"},
            headers=headers,
        )
        assert summary_resp.status_code == 200
        assert summary_resp.json() == [
            {
                "category_id": str(cat_transit),
                "category_name": "Transit",
                "count": 1,
                "total_usd": "20.00",
            },
            {
                "category_id": str(cat_food),
                "category_name": "Food",
                "count": 3,
                "total_usd": "16.67",
            },
        ]

        all_exps = await client.get(
            "/expenses",
            params={"from": "2026-11-01", "to": "2026-11-30"},
            headers=headers,
        )
        assert [item["id"] for item in all_exps.json()] == [
            str(exp_transit),
            str(exp3_id),
            str(exp2_id),
            str(exp1_id),
        ]

        transit_only = await client.get(
            "/expenses",
            params={
                "from": "2026-11-01",
                "to": "2026-11-30",
                "category_id": str(cat_transit),
            },
            headers=headers,
        )
        assert [item["id"] for item in transit_only.json()] == [str(exp_transit)]

        # Soft delete -> 200 with deleted_at; idempotent; GET -> 404; PUT -> 409
        del_resp = await client.delete(f"/expenses/{exp_transit}", headers=headers)
        assert del_resp.status_code == 200
        assert del_resp.json()["deleted_at"] is not None
        assert del_resp.json()["category_name"] == "Transit"

        del_again = await client.delete(f"/expenses/{exp_transit}", headers=headers)
        assert del_again.status_code == 200
        assert del_again.json() == del_resp.json()

        assert (await client.get(f"/expenses/{exp_transit}", headers=headers)).status_code == 404
        upsert_deleted = await client.put(
            f"/expenses/{exp_transit}",
            json={
                "category_id": str(cat_transit),
                "amount": "20.00",
                "currency": "usd",
                "spent_on": "2026-11-15",
            },
            headers=headers,
        )
        assert upsert_deleted.status_code == 409

        summary_after_del = await client.get(
            "/expenses/summary",
            params={"from": "2026-11-01", "to": "2026-11-30"},
            headers=headers,
        )
        assert [row["category_id"] for row in summary_after_del.json()] == [str(cat_food)]


async def test_expense_error_mappings_404_409_422(migrated_db_url: str) -> None:
    clock = _FakeClock(datetime(2026, 11, 22, 14, 0, tzinfo=UTC))
    application = create_app(settings=_make_settings(migrated_db_url), clock=clock)
    missing_id = uuid4()
    active_cat = uuid4()
    archived_cat = uuid4()

    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client,
    ):
        headers = await _create_auth_header(migrated_db_url, client, clock.current)
        await client.put(
            f"/expenses/categories/{active_cat}",
            json={"name": "Active"},
            headers=headers,
        )
        await client.put(
            f"/expenses/categories/{archived_cat}",
            json={"name": "Archived"},
            headers=headers,
        )
        await client.post(f"/expenses/categories/{archived_cat}/archive", headers=headers)

        # 404s
        assert (
            await client.post(f"/expenses/categories/{missing_id}/archive", headers=headers)
        ).status_code == 404
        assert (
            await client.post(f"/expenses/categories/{missing_id}/unarchive", headers=headers)
        ).status_code == 404
        assert (await client.get(f"/expenses/{missing_id}", headers=headers)).status_code == 404
        assert (await client.delete(f"/expenses/{missing_id}", headers=headers)).status_code == 404
        assert (
            await client.put(
                f"/expenses/{uuid4()}",
                json={
                    "category_id": str(missing_id),
                    "amount": "10.00",
                    "currency": "usd",
                    "spent_on": "2026-11-20",
                },
                headers=headers,
            )
        ).status_code == 404

        # 422s
        assert (
            await client.put(
                f"/expenses/{uuid4()}",
                json={
                    "category_id": str(active_cat),
                    "amount": "0",
                    "currency": "usd",
                    "spent_on": "2026-11-20",
                },
                headers=headers,
            )
        ).status_code == 422
        assert (
            await client.put(
                f"/expenses/{uuid4()}",
                json={
                    "category_id": str(active_cat),
                    "amount": "10.00",
                    "currency": "usd",
                    "spent_on": "2026-11-20",
                    "note": "x" * 501,
                },
                headers=headers,
            )
        ).status_code == 422
        assert (
            await client.get(
                "/expenses",
                params={"from": "2026-11-20", "to": "2026-11-10"},
                headers=headers,
            )
        ).status_code == 422
        assert (
            await client.get(
                "/expenses/summary",
                params={"from": "2026-11-20", "to": "2026-11-10"},
                headers=headers,
            )
        ).status_code == 422

        # 409 CategoryArchived & RateNotAvailableForDate
        assert (
            await client.put(
                f"/expenses/{uuid4()}",
                json={
                    "category_id": str(archived_cat),
                    "amount": "10.00",
                    "currency": "usd",
                    "spent_on": "2026-11-20",
                },
                headers=headers,
            )
        ).status_code == 409

        # Missing rate -> 409 RateNotAvailableForDate
        no_rate_resp = await client.put(
            f"/expenses/{UUID(int=1)}",
            json={
                "category_id": str(active_cat),
                "amount": "10.00",
                "currency": "usd",
                "spent_on": "2026-11-10",
            },
            headers=headers,
        )
        assert no_rate_resp.status_code == 409
        assert "rate" in no_rate_resp.json()["detail"].lower()

        # Editing an existing expense whose category was archived later -> 200
        await _insert_rate(migrated_db_url, RateKind.bcv, date(2026, 11, 20), Decimal("300.00"))
        await _insert_rate(migrated_db_url, RateKind.usdt, date(2026, 11, 20), Decimal("320.00"))
        exp_in_active = uuid4()
        assert (
            await client.put(
                f"/expenses/{exp_in_active}",
                json={
                    "category_id": str(active_cat),
                    "amount": "10.00",
                    "currency": "usd",
                    "spent_on": "2026-11-20",
                },
                headers=headers,
            )
        ).status_code == 201
        await client.post(f"/expenses/categories/{active_cat}/archive", headers=headers)
        assert (
            await client.put(
                f"/expenses/{exp_in_active}",
                json={
                    "category_id": str(active_cat),
                    "amount": "12.00",
                    "currency": "usd",
                    "spent_on": "2026-11-20",
                    "note": "Edited after archive",
                },
                headers=headers,
            )
        ).status_code == 200
