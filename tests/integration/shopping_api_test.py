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


async def test_shopping_endpoints_require_authentication(migrated_db_url: str) -> None:
    clock = _FakeClock(datetime(2026, 11, 22, 14, 0, tzinfo=UTC))
    application = create_app(settings=_make_settings(migrated_db_url), clock=clock)
    some_id = uuid4()

    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client,
    ):
        responses = [
            await client.put(f"/shopping/lists/{some_id}", json={"name": "Market"}),
            await client.get("/shopping/lists"),
            await client.get(f"/shopping/lists/{some_id}"),
            await client.delete(f"/shopping/lists/{some_id}"),
            await client.get(f"/shopping/lists/{some_id}/totals"),
            await client.post(
                f"/shopping/lists/{some_id}/close",
                json={"category_id": str(some_id)},
            ),
            await client.put(
                f"/shopping/lists/{some_id}/items/{some_id}",
                json={"product_id": str(some_id), "quantity": "1"},
            ),
            await client.delete(f"/shopping/lists/{some_id}/items/{some_id}"),
            await client.post(f"/shopping/lists/{some_id}/items/{some_id}/buy"),
            await client.post(f"/shopping/lists/{some_id}/items/{some_id}/unbuy"),
            await client.get("/shopping/recommendations"),
        ]

    assert all(resp.status_code == 401 for resp in responses)


async def test_full_shopping_flow_creates_expense_visible_in_expenses(
    migrated_db_url: str,
) -> None:
    clock = _FakeClock(datetime(2026, 11, 22, 14, 0, tzinfo=UTC))
    await _insert_rate(migrated_db_url, RateKind.bcv, date(2026, 11, 22), Decimal("100.00000000"))
    await _insert_rate(migrated_db_url, RateKind.usdt, date(2026, 11, 22), Decimal("120.00000000"))
    application = create_app(settings=_make_settings(migrated_db_url), clock=clock)
    cat_id, p_usd, p_ves = uuid4(), uuid4(), uuid4()
    list_id, item_usd, item_ves = uuid4(), uuid4(), uuid4()

    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client,
    ):
        headers = await _create_auth_header(migrated_db_url, client, clock.current)
        await client.put(
            f"/expenses/categories/{cat_id}",
            json={"name": "Groceries"},
            headers=headers,
        )
        await client.put(
            f"/products/{p_usd}",
            json={"name": "Coffee", "unit": "kg", "last_price": "5.00", "currency": "usd"},
            headers=headers,
        )
        await client.put(
            f"/products/{p_ves}",
            json={"name": "Bread", "unit": "unit"},
            headers=headers,
        )

        created_list = await client.put(
            f"/shopping/lists/{list_id}",
            json={"name": "Sunday Market"},
            headers=headers,
        )
        assert created_list.status_code == 201

        assert (
            await client.put(
                f"/shopping/lists/{list_id}/items/{item_usd}",
                json={"product_id": str(p_usd), "quantity": "2"},
                headers=headers,
            )
        ).status_code == 201
        assert (
            await client.put(
                f"/shopping/lists/{list_id}/items/{item_ves}",
                json={"product_id": str(p_ves), "quantity": "3"},
                headers=headers,
            )
        ).status_code == 201

        buy_usd = await client.post(
            f"/shopping/lists/{list_id}/items/{item_usd}/buy",
            headers=headers,
        )
        assert buy_usd.status_code == 200
        assert buy_usd.json()["bought_price"] == "5.00"

        buy_ves = await client.post(
            f"/shopping/lists/{list_id}/items/{item_ves}/buy",
            json={"price": "200.00", "currency": "ves"},
            headers=headers,
        )
        assert buy_ves.status_code == 200

        totals = await client.get(f"/shopping/lists/{list_id}/totals", headers=headers)
        assert totals.status_code == 200
        assert totals.json()["bought"]["total_ves"] == "1600.00"
        assert totals.json()["bought"]["total_usd"] == "16.00"
        assert totals.json()["bought"]["usdt_to_sell"] == "13.33"

        closed = await client.post(
            f"/shopping/lists/{list_id}/close",
            json={"category_id": str(cat_id)},
            headers=headers,
        )
        assert closed.status_code == 200
        assert closed.json()["status"] == "closed"
        expense_id = closed.json()["expense_id"]

        expenses = await client.get(
            "/expenses",
            params={"from": "2026-11-22", "to": "2026-11-22"},
            headers=headers,
        )
        assert expenses.status_code == 200
        rows = expenses.json()
        assert len(rows) == 1
        assert rows[0]["id"] == expense_id
        assert rows[0]["amount"] == "1600.00"
        assert rows[0]["currency"] == "ves"
        assert rows[0]["note"] == "Shopping list: Sunday Market"


async def test_shopping_list_item_unbuy_delete_and_list_filters(migrated_db_url: str) -> None:
    clock = _FakeClock(datetime(2026, 11, 22, 14, 0, tzinfo=UTC))
    await _insert_rate(migrated_db_url, RateKind.bcv, date(2026, 11, 22), Decimal("100.00000000"))
    await _insert_rate(migrated_db_url, RateKind.usdt, date(2026, 11, 22), Decimal("120.00000000"))
    application = create_app(settings=_make_settings(migrated_db_url), clock=clock)
    prod_id, list1_id, list2_id, item1_id, item2_id = uuid4(), uuid4(), uuid4(), uuid4(), uuid4()

    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client,
    ):
        headers = await _create_auth_header(migrated_db_url, client, clock.current)
        await client.put(
            f"/products/{prod_id}",
            json={"name": "Milk", "unit": "l", "last_price": "2.00", "currency": "usd"},
            headers=headers,
        )
        await client.put(f"/shopping/lists/{list1_id}", json={"name": "List 1"}, headers=headers)
        await client.put(f"/shopping/lists/{list2_id}", json={"name": "List 2"}, headers=headers)
        await client.put(
            f"/shopping/lists/{list1_id}/items/{item1_id}",
            json={"product_id": str(prod_id), "quantity": "1"},
            headers=headers,
        )

        clock.current += timedelta(minutes=5)
        await client.post(
            f"/shopping/lists/{list1_id}/items/{item1_id}/buy",
            json={"price": "2.50", "currency": "usd"},
            headers=headers,
        )
        unbought = await client.post(
            f"/shopping/lists/{list1_id}/items/{item1_id}/unbuy",
            headers=headers,
        )
        assert unbought.status_code == 200
        assert unbought.json()["status"] == "pending"
        assert unbought.json()["product_last_price"] == "2.00"

        deleted_item = await client.delete(
            f"/shopping/lists/{list1_id}/items/{item1_id}",
            headers=headers,
        )
        assert deleted_item.status_code == 204

        await client.put(
            f"/shopping/lists/{list2_id}/items/{item2_id}",
            json={"product_id": str(prod_id), "quantity": "1"},
            headers=headers,
        )
        detail = await client.get(f"/shopping/lists/{list2_id}", headers=headers)
        assert detail.status_code == 200
        assert len(detail.json()["items"]) == 1

        await client.delete(f"/shopping/lists/{list1_id}", headers=headers)
        open_lists = await client.get("/shopping/lists", params={"status": "open"}, headers=headers)
        assert [lst["id"] for lst in open_lists.json()] == [str(list2_id)]


async def test_shopping_recommendations_endpoint(migrated_db_url: str) -> None:
    clock = _FakeClock(datetime(2026, 11, 10, 14, 0, tzinfo=UTC))
    await _insert_rate(migrated_db_url, RateKind.bcv, date(2026, 11, 10), Decimal("100.00000000"))
    await _insert_rate(migrated_db_url, RateKind.usdt, date(2026, 11, 10), Decimal("120.00000000"))
    application = create_app(settings=_make_settings(migrated_db_url), clock=clock)
    prod_id, list1_id, list2_id, item1_id, item2_id = uuid4(), uuid4(), uuid4(), uuid4(), uuid4()

    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client,
    ):
        headers = await _create_auth_header(migrated_db_url, client, clock.current)
        await client.put(
            f"/products/{prod_id}",
            json={"name": "Eggs", "unit": "unit", "last_price": "3.00", "currency": "usd"},
            headers=headers,
        )
        await client.put(f"/shopping/lists/{list1_id}", json={"name": "Week 1"}, headers=headers)
        await client.put(
            f"/shopping/lists/{list1_id}/items/{item1_id}",
            json={"product_id": str(prod_id), "quantity": "1"},
            headers=headers,
        )
        await client.post(f"/shopping/lists/{list1_id}/items/{item1_id}/buy", headers=headers)

        clock.current = datetime(2026, 11, 17, 14, 0, tzinfo=UTC)
        token2 = await client.post(
            "/auth/token",
            data={"username": "alice@example.com", "password": "correct-horse-12"},
        )
        headers = {"Authorization": f"Bearer {token2.json()['access_token']}"}
        await client.put(f"/shopping/lists/{list2_id}", json={"name": "Week 2"}, headers=headers)
        await client.put(
            f"/shopping/lists/{list2_id}/items/{item2_id}",
            json={"product_id": str(prod_id), "quantity": "3"},
            headers=headers,
        )
        await client.post(f"/shopping/lists/{list2_id}/items/{item2_id}/buy", headers=headers)

        clock.current = datetime(2026, 11, 22, 14, 0, tzinfo=UTC)
        token3 = await client.post(
            "/auth/token",
            data={"username": "alice@example.com", "password": "correct-horse-12"},
        )
        headers = {"Authorization": f"Bearer {token3.json()['access_token']}"}
        recs = await client.get(
            "/shopping/recommendations",
            params={"days_ahead": 7},
            headers=headers,
        )
        assert recs.status_code == 200
        body = recs.json()
        assert len(body["items"]) == 1
        assert body["items"][0]["product_id"] == str(prod_id)
        assert body["items"][0]["avg_interval_days"] == "7.0"
        assert body["items"][0]["next_due"] == "2026-11-24"
        assert body["items"][0]["suggested_quantity"] == "2.000"
        assert body["totals"]["total_usd"] == "6.00"


async def test_shopping_not_found_returns_404(migrated_db_url: str) -> None:
    clock = _FakeClock(datetime(2026, 11, 22, 14, 0, tzinfo=UTC))
    application = create_app(settings=_make_settings(migrated_db_url), clock=clock)
    missing_id = uuid4()

    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client,
    ):
        headers = await _create_auth_header(migrated_db_url, client, clock.current)
        responses = [
            await client.get(f"/shopping/lists/{missing_id}", headers=headers),
            await client.delete(f"/shopping/lists/{missing_id}", headers=headers),
            await client.get(f"/shopping/lists/{missing_id}/totals", headers=headers),
            await client.post(
                f"/shopping/lists/{missing_id}/close",
                json={"category_id": str(missing_id)},
                headers=headers,
            ),
            await client.put(
                f"/shopping/lists/{missing_id}/items/{missing_id}",
                json={"product_id": str(missing_id), "quantity": "1"},
                headers=headers,
            ),
            await client.delete(
                f"/shopping/lists/{missing_id}/items/{missing_id}",
                headers=headers,
            ),
            await client.post(
                f"/shopping/lists/{missing_id}/items/{missing_id}/buy",
                headers=headers,
            ),
            await client.post(
                f"/shopping/lists/{missing_id}/items/{missing_id}/unbuy",
                headers=headers,
            ),
        ]

    assert all(resp.status_code == 404 for resp in responses)


async def test_shopping_conflicts_return_409_and_expense_error_leaves_list_open(
    migrated_db_url: str,
) -> None:
    clock = _FakeClock(datetime(2026, 11, 22, 14, 0, tzinfo=UTC))
    await _insert_rate(migrated_db_url, RateKind.bcv, date(2026, 11, 22), Decimal("100.00000000"))
    await _insert_rate(migrated_db_url, RateKind.usdt, date(2026, 11, 22), Decimal("120.00000000"))
    application = create_app(settings=_make_settings(migrated_db_url), clock=clock)
    cat_id, prod_id, archived_prod_id = uuid4(), uuid4(), uuid4()
    list_id, item_id, deleted_list_id = uuid4(), uuid4(), uuid4()

    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client,
    ):
        headers = await _create_auth_header(migrated_db_url, client, clock.current)
        await client.put(f"/expenses/categories/{cat_id}", json={"name": "Food"}, headers=headers)
        await client.post(f"/expenses/categories/{cat_id}/archive", headers=headers)
        await client.put(
            f"/products/{prod_id}",
            json={"name": "Rice", "unit": "kg", "last_price": "2.00", "currency": "usd"},
            headers=headers,
        )
        await client.put(
            f"/products/{archived_prod_id}",
            json={"name": "Old", "unit": "kg"},
            headers=headers,
        )
        await client.post(f"/products/{archived_prod_id}/archive", headers=headers)
        await client.put(f"/shopping/lists/{list_id}", json={"name": "Main"}, headers=headers)

        archived_err = await client.put(
            f"/shopping/lists/{list_id}/items/{uuid4()}",
            json={"product_id": str(archived_prod_id), "quantity": "1"},
            headers=headers,
        )
        assert archived_err.status_code == 409

        await client.put(
            f"/shopping/lists/{list_id}/items/{item_id}",
            json={"product_id": str(prod_id), "quantity": "1"},
            headers=headers,
        )
        dup_err = await client.put(
            f"/shopping/lists/{list_id}/items/{uuid4()}",
            json={"product_id": str(prod_id), "quantity": "2"},
            headers=headers,
        )
        assert dup_err.status_code == 409

        await client.post(f"/shopping/lists/{list_id}/items/{item_id}/buy", headers=headers)
        assert (
            await client.delete(f"/shopping/lists/{list_id}/items/{item_id}", headers=headers)
        ).status_code == 409

        close_archived_cat = await client.post(
            f"/shopping/lists/{list_id}/close",
            json={"category_id": str(cat_id)},
            headers=headers,
        )
        assert close_archived_cat.status_code == 409
        assert (await client.get(f"/shopping/lists/{list_id}", headers=headers)).json()[
            "status"
        ] == "open"

        await client.put(
            f"/shopping/lists/{deleted_list_id}", json={"name": "Gone"}, headers=headers
        )
        await client.delete(f"/shopping/lists/{deleted_list_id}", headers=headers)
        assert (
            await client.put(
                f"/shopping/lists/{deleted_list_id}",
                json={"name": "Revive"},
                headers=headers,
            )
        ).status_code == 409


async def test_shopping_validation_errors_return_422(migrated_db_url: str) -> None:
    clock = _FakeClock(datetime(2026, 11, 22, 14, 0, tzinfo=UTC))
    application = create_app(settings=_make_settings(migrated_db_url), clock=clock)
    prod_id, list_id, item_id = uuid4(), uuid4(), uuid4()

    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client,
    ):
        headers = await _create_auth_header(migrated_db_url, client, clock.current)
        await client.put(
            f"/products/{prod_id}", json={"name": "Salt", "unit": "kg"}, headers=headers
        )
        await client.put(f"/shopping/lists/{list_id}", json={"name": "Market"}, headers=headers)
        await client.put(
            f"/shopping/lists/{list_id}/items/{item_id}",
            json={"product_id": str(prod_id), "quantity": "1"},
            headers=headers,
        )

        assert (
            await client.put(
                f"/shopping/lists/{uuid4()}",
                json={"name": "   "},
                headers=headers,
            )
        ).status_code == 422
        assert (
            await client.put(
                f"/shopping/lists/{list_id}/items/{uuid4()}",
                json={"product_id": str(prod_id), "quantity": "0"},
                headers=headers,
            )
        ).status_code == 422
        assert (
            await client.post(
                f"/shopping/lists/{list_id}/items/{item_id}/buy",
                headers=headers,
            )
        ).status_code == 422
        assert (
            await client.post(
                f"/shopping/lists/{list_id}/items/{item_id}/buy",
                json={"price": "2.00"},
                headers=headers,
            )
        ).status_code == 422
        assert (
            await client.post(
                f"/shopping/lists/{list_id}/close",
                json={"category_id": str(uuid4())},
                headers=headers,
            )
        ).status_code == 422
        assert (
            await client.get(
                "/shopping/recommendations",
                params={"days_ahead": 61},
                headers=headers,
            )
        ).status_code == 422
