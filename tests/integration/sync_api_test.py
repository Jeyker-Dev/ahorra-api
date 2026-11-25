import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
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

_TRUNCATE_SQL = (
    "TRUNCATE TABLE sync_tombstones, goal_contributions, goals, product_prices, "
    "shopping_list_items, shopping_lists, products, expenses, expense_categories, "
    "income_occurrences, income_rules, exchange_rates, refresh_tokens, users CASCADE"
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
    fetched_at: datetime,
) -> None:
    rate = ExchangeRate(
        id=uuid4(),
        kind=kind,
        value=value,
        value_date=value_date,
        origin=RateOrigin.manual,
        status=RateStatus.confirmed,
        fetched_at=fetched_at,
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


async def test_sync_changes_requires_authentication(migrated_db_url: str) -> None:
    clock = _FakeClock(datetime(2026, 11, 25, 14, 0, 30, tzinfo=UTC))
    app = create_app(settings=_make_settings(migrated_db_url), clock=clock)

    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        resp = await client.get("/sync/changes")
        assert resp.status_code == 401


async def test_sync_changes_invalid_cursor_returns_422_detail(migrated_db_url: str) -> None:
    clock = _FakeClock(datetime(2026, 11, 25, 14, 0, 30, tzinfo=UTC))
    app = create_app(settings=_make_settings(migrated_db_url), clock=clock)

    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        headers = await _create_auth_header(migrated_db_url, client, clock.now())

        for bad in (
            "not-a-date",
            "2026-11-25T14:00:00",
            "2026-11-25T14:00:00-04:00",
            "2026-11-25T14:00:01Z",
        ):
            resp = await client.get("/sync/changes", params={"since": bad}, headers=headers)
            assert resp.status_code == 422
            body = resp.json()
            assert isinstance(body["detail"], str)


async def test_sync_changes_boundary_until_soft_deleted_archived_ended_and_no_duplicates(
    migrated_db_url: str,
) -> None:
    t0 = datetime(2026, 11, 25, 13, 50, 0, tzinfo=UTC)
    clock = _FakeClock(t0)
    app = create_app(settings=_make_settings(migrated_db_url), clock=clock)

    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        headers = await _create_auth_header(migrated_db_url, client, t0)
        today_caracas = date(2026, 11, 25)
        await _insert_rate(
            migrated_db_url, RateKind.bcv, today_caracas, Decimal("900.00000000"), t0
        )
        await _insert_rate(
            migrated_db_url, RateKind.usdt, today_caracas, Decimal("950.00000000"), t0
        )

        # Create & end an income rule (ended row)
        rule_id = uuid4()
        assert (
            await client.put(
                f"/income/rules/{rule_id}",
                json={
                    "name": "Salary",
                    "amount": "500.00",
                    "currency": "usd",
                    "days": [25],
                    "start_date": "2026-11-01",
                },
                headers=headers,
            )
        ).status_code == 201
        assert (await client.delete(f"/income/rules/{rule_id}", headers=headers)).status_code == 200

        # Create & archive an expense category + create & soft-delete an expense
        cat_id = uuid4()
        exp_id = uuid4()
        assert (
            await client.put(
                f"/expenses/categories/{cat_id}",
                json={"name": "Food"},
                headers=headers,
            )
        ).status_code == 201
        assert (
            await client.put(
                f"/expenses/{exp_id}",
                json={
                    "category_id": str(cat_id),
                    "amount": "12.50",
                    "currency": "usd",
                    "spent_on": "2026-11-25",
                    "note": "Lunch",
                },
                headers=headers,
            )
        ).status_code == 201
        assert (await client.delete(f"/expenses/{exp_id}", headers=headers)).status_code == 200
        assert (
            await client.post(f"/expenses/categories/{cat_id}/archive", headers=headers)
        ).status_code == 200

        # Create a product exactly at `until` for the upcoming pull
        exact_until = datetime(2026, 11, 25, 14, 0, 0, tzinfo=UTC)
        clock.current = exact_until
        prod_id = uuid4()
        assert (
            await client.put(
                f"/products/{prod_id}",
                json={
                    "name": "Coffee",
                    "unit": "kg",
                    "last_price": "8.00",
                    "currency": "usd",
                },
                headers=headers,
            )
        ).status_code == 201

        # Create a goal & soft-delete it
        goal_id = uuid4()
        assert (
            await client.put(
                f"/goals/{goal_id}",
                json={"name": "Emergency", "target_usd": "1000.00"},
                headers=headers,
            )
        ).status_code == 201
        assert (await client.delete(f"/goals/{goal_id}", headers=headers)).status_code == 200

        # Pull at exact_until + 30s -> until == exact_until
        clock.current = exact_until + timedelta(seconds=30)
        first_pull = await client.get("/sync/changes", headers=headers)
        assert first_pull.status_code == 200
        data = first_pull.json()
        assert data["next_cursor"] == "2026-11-25T14:00:00Z"

        # Ended rule, archived category, soft-deleted expense, product at `until`, deleted goal
        assert [r["id"] for r in data["income_rules"]] == [str(rule_id)]
        assert data["income_rules"][0]["ended_at"] is not None
        assert [c["id"] for c in data["expense_categories"]] == [str(cat_id)]
        assert data["expense_categories"][0]["archived_at"] is not None
        assert [e["id"] for e in data["expenses"]] == [str(exp_id)]
        assert data["expenses"][0]["deleted_at"] is not None
        assert "category_name" not in data["expenses"][0]
        assert data["expenses"][0]["amount"] == "12.50"
        assert [p["id"] for p in data["products"]] == [str(prod_id)]
        assert len(data["product_prices"]) == 1
        assert "updated_at" in data["product_prices"][0]
        assert [g["id"] for g in data["goals"]] == [str(goal_id)]
        assert data["goals"][0]["deleted_at"] is not None
        assert "progress" not in data["goals"][0]

        # Consecutive pull with returned cursor -> no duplicates when nothing changed
        clock.current = exact_until + timedelta(minutes=2)
        second_pull = await client.get(
            "/sync/changes",
            params={"since": data["next_cursor"]},
            headers=headers,
        )
        assert second_pull.status_code == 200
        second_data = second_pull.json()
        for key in (
            "income_rules",
            "income_occurrences",
            "expense_categories",
            "expenses",
            "products",
            "product_prices",
            "shopping_lists",
            "shopping_list_items",
            "goals",
            "goal_contributions",
            "tombstones",
        ):
            assert second_data[key] == []


async def test_all_three_hard_delete_paths_produce_tombstones_returned_by_sync(
    migrated_db_url: str,
) -> None:
    # Align fake clock with real Postgres now() so trigger deleted_at falls inside window
    real_now = datetime.now(UTC)
    clock = _FakeClock(real_now)
    app = create_app(settings=_make_settings(migrated_db_url), clock=clock)

    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        headers = await _create_auth_header(migrated_db_url, client, real_now)
        today_caracas = date.today()
        await _insert_rate(
            migrated_db_url, RateKind.bcv, today_caracas, Decimal("900.00000000"), real_now
        )
        await _insert_rate(
            migrated_db_url, RateKind.usdt, today_caracas, Decimal("950.00000000"), real_now
        )

        # 1. Income hard-delete paths (end_rule and upsert_rule days change)
        rule_a = uuid4()
        rule_b = uuid4()
        await client.put(
            f"/income/rules/{rule_a}",
            json={
                "name": "Rule A",
                "amount": "100.00",
                "currency": "usd",
                "days": [28],
                "start_date": today_caracas.isoformat(),
            },
            headers=headers,
        )
        await client.delete(f"/income/rules/{rule_a}", headers=headers)

        await client.put(
            f"/income/rules/{rule_b}",
            json={
                "name": "Rule B",
                "amount": "100.00",
                "currency": "usd",
                "days": [28],
                "start_date": today_caracas.isoformat(),
            },
            headers=headers,
        )
        await client.put(
            f"/income/rules/{rule_b}",
            json={
                "name": "Rule B",
                "amount": "100.00",
                "currency": "usd",
                "days": [27],
                "start_date": today_caracas.isoformat(),
            },
            headers=headers,
        )

        # 2 & 3. Shopping list item hard delete and product_prices unbuy hard delete
        prod_id = uuid4()
        list_id = uuid4()
        pending_item_id = uuid4()
        bought_item_id = uuid4()

        await client.put(
            f"/products/{prod_id}",
            json={"name": "Milk", "unit": "l", "last_price": "2.00", "currency": "usd"},
            headers=headers,
        )
        prod_other = uuid4()
        await client.put(
            f"/products/{prod_other}",
            json={"name": "Bread", "unit": "unit", "last_price": "1.50", "currency": "usd"},
            headers=headers,
        )
        await client.put(f"/shopping/lists/{list_id}", json={"name": "Market"}, headers=headers)
        await client.put(
            f"/shopping/lists/{list_id}/items/{pending_item_id}",
            json={"product_id": str(prod_other), "quantity": "1.000"},
            headers=headers,
        )
        await client.put(
            f"/shopping/lists/{list_id}/items/{bought_item_id}",
            json={"product_id": str(prod_id), "quantity": "2.000"},
            headers=headers,
        )
        assert (
            await client.post(
                f"/shopping/lists/{list_id}/items/{bought_item_id}/buy",
                json={"price": "2.20", "currency": "usd"},
                headers=headers,
            )
        ).status_code == 200
        # Correct bought price in place (verifies product_prices.updated_at update)
        assert (
            await client.post(
                f"/shopping/lists/{list_id}/items/{bought_item_id}/buy",
                json={"price": "2.30", "currency": "usd"},
                headers=headers,
            )
        ).status_code == 200
        # Unbuy -> hard-deletes product_prices row
        assert (
            await client.post(
                f"/shopping/lists/{list_id}/items/{bought_item_id}/unbuy",
                headers=headers,
            )
        ).status_code == 200
        # Delete pending item -> hard-deletes shopping_list_items row
        assert (
            await client.delete(
                f"/shopping/lists/{list_id}/items/{pending_item_id}",
                headers=headers,
            )
        ).status_code == 204

        # Advance fake clock past Postgres trigger now() + 30s safety lag
        clock.current = datetime.now(UTC) + timedelta(minutes=2)
        sync_resp = await client.get("/sync/changes", headers=headers)
        assert sync_resp.status_code == 200
        body = sync_resp.json()

        tombstones = body["tombstones"]
        entities_with_tombstones = {t["entity"] for t in tombstones}
        assert {
            "income_occurrences",
            "shopping_list_items",
            "product_prices",
        }.issubset(entities_with_tombstones)

        item_tombstones = [
            UUID(t["id"]) for t in tombstones if t["entity"] == "shopping_list_items"
        ]
        assert item_tombstones == [pending_item_id]
        # Verify trigger tombstones match non-existent occurrence rows
        occ_ids_returned = {o["id"] for o in body["income_occurrences"]}
        occ_tombstones = [t["id"] for t in tombstones if t["entity"] == "income_occurrences"]
        assert len(occ_tombstones) >= 2
        assert all(tid not in occ_ids_returned for tid in occ_tombstones)
