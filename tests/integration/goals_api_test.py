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
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.auth.passwords import PasswordHasher
from app.config import Settings
from app.main import create_app
from app.rates.domain import ExchangeRate, RateKind, RateOrigin, RateStatus

TEST_JWT_SECRET = "test-secret-key-with-at-least-32-chars"

_TRUNCATE_SQL = (
    "TRUNCATE TABLE goal_contributions, goals, exchange_rates, refresh_tokens, users CASCADE"
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
        fetched_at=datetime(2026, 11, 23, 12, 0, tzinfo=UTC),
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


async def test_goal_endpoints_require_authentication(migrated_db_url: str) -> None:
    clock = _FakeClock(datetime(2026, 11, 23, 14, 0, tzinfo=UTC))
    application = create_app(settings=_make_settings(migrated_db_url), clock=clock)
    some_id = uuid4()

    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client,
    ):
        responses = [
            await client.put(f"/goals/{some_id}", json={"name": "Fund", "target_usd": "100"}),
            await client.get("/goals"),
            await client.get(f"/goals/{some_id}"),
            await client.post(f"/goals/{some_id}/archive"),
            await client.post(f"/goals/{some_id}/unarchive"),
            await client.delete(f"/goals/{some_id}"),
            await client.put(
                f"/goals/{some_id}/contributions/{some_id}",
                json={
                    "kind": "deposit",
                    "amount": "10",
                    "currency": "usd",
                    "contributed_on": "2026-11-23",
                },
            ),
            await client.get(f"/goals/{some_id}/contributions"),
            await client.delete(f"/goals/{some_id}/contributions/{some_id}"),
        ]

    assert all(resp.status_code == 401 for resp in responses)


async def test_goal_and_contribution_happy_paths_and_put_idempotency(
    migrated_db_url: str,
) -> None:
    clock = _FakeClock(datetime(2026, 11, 23, 14, 0, tzinfo=UTC))
    await _insert_rate(migrated_db_url, RateKind.bcv, date(2026, 11, 23), Decimal("900.00000000"))
    await _insert_rate(migrated_db_url, RateKind.usdt, date(2026, 11, 23), Decimal("950.00000000"))
    application = create_app(settings=_make_settings(migrated_db_url), clock=clock)
    goal_id, other_goal_id = uuid4(), uuid4()
    c1_id, c2_id, c3_id = uuid4(), uuid4(), uuid4()

    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client,
    ):
        headers = await _create_auth_header(migrated_db_url, client, clock.current)

        created = await client.put(
            f"/goals/{goal_id}",
            json={"name": "  Emergency  ", "target_usd": "100.00", "note": "  Rainy day  "},
            headers=headers,
        )
        assert created.status_code == 201
        assert created.json()["name"] == "Emergency"
        assert created.json()["progress"]["saved_usd"] == "0.00"

        retry = await client.put(
            f"/goals/{goal_id}",
            json={"name": "Emergency", "target_usd": "100", "note": "Rainy day"},
            headers=headers,
        )
        assert retry.status_code == 200
        assert retry.json()["updated_at"] == created.json()["updated_at"]

        await client.put(
            f"/goals/{other_goal_id}",
            json={"name": "Travel", "target_usd": "200.00"},
            headers=headers,
        )

        assert (
            await client.put(
                f"/goals/{goal_id}/contributions/{c1_id}",
                json={
                    "kind": "deposit",
                    "amount": "50.00",
                    "currency": "usd",
                    "contributed_on": "2026-11-23",
                },
                headers=headers,
            )
        ).status_code == 201
        assert (
            await client.put(
                f"/goals/{goal_id}/contributions/{c2_id}",
                json={
                    "kind": "deposit",
                    "amount": "9000.00",
                    "currency": "ves",
                    "contributed_on": "2026-11-23",
                },
                headers=headers,
            )
        ).status_code == 201
        assert (
            await client.put(
                f"/goals/{goal_id}/contributions/{c3_id}",
                json={
                    "kind": "withdrawal",
                    "amount": "10.00",
                    "currency": "usd",
                    "contributed_on": "2026-11-23",
                },
                headers=headers,
            )
        ).status_code == 201

        fetched = await client.get(f"/goals/{goal_id}", headers=headers)
        assert fetched.status_code == 200
        prog = fetched.json()["progress"]
        assert prog["saved_usd"] == "50.00"
        assert prog["remaining_usd"] == "50.00"
        assert prog["percent"] == "50.0"
        assert prog["saved_ves_bcv"] == "45000.00"
        assert prog["saved_ves_usdt"] == "47500.00"

        # Move c3 to other_goal_id
        moved = await client.put(
            f"/goals/{other_goal_id}/contributions/{c3_id}",
            json={
                "kind": "withdrawal",
                "amount": "10.00",
                "currency": "usd",
                "contributed_on": "2026-11-23",
            },
            headers=headers,
        )
        assert moved.status_code == 200
        assert moved.json()["goal_id"] == str(other_goal_id)

        listed_contribs = await client.get(f"/goals/{goal_id}/contributions", headers=headers)
        assert listed_contribs.status_code == 200
        assert {c["id"] for c in listed_contribs.json()} == {str(c1_id), str(c2_id)}

        deleted_contrib = await client.delete(
            f"/goals/{goal_id}/contributions/{c2_id}",
            headers=headers,
        )
        assert deleted_contrib.status_code == 200
        assert deleted_contrib.json()["deleted_at"] is not None

        archived = await client.post(f"/goals/{goal_id}/archive", headers=headers)
        assert archived.status_code == 200
        assert archived.json()["archived_at"] is not None
        assert "progress" in archived.json()

        unarchived = await client.post(f"/goals/{goal_id}/unarchive", headers=headers)
        assert unarchived.status_code == 200
        assert unarchived.json()["archived_at"] is None


async def test_deleted_goal_hides_goal_and_allows_name_reuse(migrated_db_url: str) -> None:
    clock = _FakeClock(datetime(2026, 11, 23, 14, 0, tzinfo=UTC))
    application = create_app(settings=_make_settings(migrated_db_url), clock=clock)
    first_id, second_id = uuid4(), uuid4()

    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client,
    ):
        headers = await _create_auth_header(migrated_db_url, client, clock.current)
        await client.put(
            f"/goals/{first_id}",
            json={"name": "Laptop", "target_usd": "800"},
            headers=headers,
        )
        deleted = await client.delete(f"/goals/{first_id}", headers=headers)
        assert deleted.status_code == 200
        assert deleted.json()["deleted_at"] is not None
        assert "progress" in deleted.json()

        assert (await client.get(f"/goals/{first_id}", headers=headers)).status_code == 404

        reused = await client.put(
            f"/goals/{second_id}",
            json={"name": "laptop", "target_usd": "900"},
            headers=headers,
        )
        assert reused.status_code == 201


async def test_list_goals_uses_single_aggregated_contributions_query(
    migrated_db_url: str,
) -> None:
    clock = _FakeClock(datetime(2026, 11, 23, 14, 0, tzinfo=UTC))
    await _insert_rate(migrated_db_url, RateKind.bcv, date(2026, 11, 23), Decimal("900.00000000"))
    await _insert_rate(migrated_db_url, RateKind.usdt, date(2026, 11, 23), Decimal("950.00000000"))
    application = create_app(settings=_make_settings(migrated_db_url), clock=clock)
    goal_ids = [uuid4(), uuid4(), uuid4()]

    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client,
    ):
        headers = await _create_auth_header(migrated_db_url, client, clock.current)
        for idx, gid in enumerate(goal_ids):
            await client.put(
                f"/goals/{gid}",
                json={"name": f"Goal {idx}", "target_usd": "100"},
                headers=headers,
            )
            await client.put(
                f"/goals/{gid}/contributions/{uuid4()}",
                json={
                    "kind": "deposit",
                    "amount": str((idx + 1) * 10),
                    "currency": "usd",
                    "contributed_on": "2026-11-23",
                },
                headers=headers,
            )

        executed_statements: list[str] = []

        def _capture_sql(
            _conn: object,
            _cursor: object,
            statement: str,
            _params: object,
            _context: object,
            _executemany: object,
        ) -> None:
            executed_statements.append(statement)

        sync_engine = application.state.engine.sync_engine
        event.listen(sync_engine, "before_cursor_execute", _capture_sql)
        try:
            listed = await client.get("/goals", headers=headers)
        finally:
            event.remove(sync_engine, "before_cursor_execute", _capture_sql)

        assert listed.status_code == 200
        assert [g["progress"]["saved_usd"] for g in listed.json()] == ["10.00", "20.00", "30.00"]
        contrib_queries = [sql for sql in executed_statements if "goal_contributions" in sql]
        assert len(contrib_queries) == 1
        assert "GROUP BY" in contrib_queries[0]


async def test_goal_not_found_returns_404(migrated_db_url: str) -> None:
    clock = _FakeClock(datetime(2026, 11, 23, 14, 0, tzinfo=UTC))
    application = create_app(settings=_make_settings(migrated_db_url), clock=clock)
    goal_id = uuid4()
    missing_id = uuid4()

    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client,
    ):
        headers = await _create_auth_header(migrated_db_url, client, clock.current)
        await client.put(
            f"/goals/{goal_id}", json={"name": "Fund", "target_usd": "100"}, headers=headers
        )
        responses = [
            await client.get(f"/goals/{missing_id}", headers=headers),
            await client.post(f"/goals/{missing_id}/archive", headers=headers),
            await client.post(f"/goals/{missing_id}/unarchive", headers=headers),
            await client.delete(f"/goals/{missing_id}", headers=headers),
            await client.get(f"/goals/{missing_id}/contributions", headers=headers),
            await client.delete(
                f"/goals/{goal_id}/contributions/{missing_id}",
                headers=headers,
            ),
            await client.put(
                f"/goals/{missing_id}/contributions/{missing_id}",
                json={
                    "kind": "deposit",
                    "amount": "10",
                    "currency": "usd",
                    "contributed_on": "2026-11-23",
                },
                headers=headers,
            ),
        ]

    assert all(resp.status_code == 404 for resp in responses)


async def test_goal_conflicts_return_409(migrated_db_url: str) -> None:
    clock = _FakeClock(datetime(2026, 11, 23, 14, 0, tzinfo=UTC))
    application = create_app(settings=_make_settings(migrated_db_url), clock=clock)
    g1, g2, g_deleted, c_deleted = uuid4(), uuid4(), uuid4(), uuid4()

    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client,
    ):
        headers = await _create_auth_header(migrated_db_url, client, clock.current)
        await client.put(
            f"/goals/{g1}", json={"name": "Fund", "target_usd": "100"}, headers=headers
        )
        assert (
            await client.put(
                f"/goals/{g2}",
                json={"name": "fund", "target_usd": "100"},
                headers=headers,
            )
        ).status_code == 409

        # Missing rate -> 409
        assert (
            await client.put(
                f"/goals/{g1}/contributions/{c_deleted}",
                json={
                    "kind": "deposit",
                    "amount": "10",
                    "currency": "usd",
                    "contributed_on": "2026-11-23",
                },
                headers=headers,
            )
        ).status_code == 409

        await _insert_rate(
            migrated_db_url, RateKind.bcv, date(2026, 11, 23), Decimal("900.00000000")
        )
        await _insert_rate(
            migrated_db_url, RateKind.usdt, date(2026, 11, 23), Decimal("950.00000000")
        )
        await client.put(
            f"/goals/{g1}/contributions/{c_deleted}",
            json={
                "kind": "deposit",
                "amount": "10",
                "currency": "usd",
                "contributed_on": "2026-11-23",
            },
            headers=headers,
        )
        await client.delete(f"/goals/{g1}/contributions/{c_deleted}", headers=headers)
        assert (
            await client.put(
                f"/goals/{g1}/contributions/{c_deleted}",
                json={
                    "kind": "deposit",
                    "amount": "10",
                    "currency": "usd",
                    "contributed_on": "2026-11-23",
                },
                headers=headers,
            )
        ).status_code == 409

        await client.post(f"/goals/{g1}/archive", headers=headers)
        assert (
            await client.put(
                f"/goals/{g1}/contributions/{uuid4()}",
                json={
                    "kind": "deposit",
                    "amount": "10",
                    "currency": "usd",
                    "contributed_on": "2026-11-23",
                },
                headers=headers,
            )
        ).status_code == 409

        await client.put(
            f"/goals/{g_deleted}",
            json={"name": "Temp", "target_usd": "100"},
            headers=headers,
        )
        await client.delete(f"/goals/{g_deleted}", headers=headers)
        assert (
            await client.put(
                f"/goals/{g_deleted}",
                json={"name": "Temp", "target_usd": "100"},
                headers=headers,
            )
        ).status_code == 409


async def test_goal_validation_errors_return_422(migrated_db_url: str) -> None:
    clock = _FakeClock(datetime(2026, 11, 23, 14, 0, tzinfo=UTC))
    application = create_app(settings=_make_settings(migrated_db_url), clock=clock)
    goal_id = uuid4()

    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client,
    ):
        headers = await _create_auth_header(migrated_db_url, client, clock.current)
        await client.put(
            f"/goals/{goal_id}",
            json={"name": "Fund", "target_usd": "100"},
            headers=headers,
        )

        responses = [
            await client.put(
                f"/goals/{uuid4()}",
                json={"name": "   ", "target_usd": "100"},
                headers=headers,
            ),
            await client.put(
                f"/goals/{uuid4()}",
                json={"name": "Trip", "target_usd": "0"},
                headers=headers,
            ),
            await client.put(
                f"/goals/{uuid4()}",
                json={"name": "Trip", "target_usd": "100", "note": "x" * 501},
                headers=headers,
            ),
            await client.put(
                f"/goals/{goal_id}/contributions/{uuid4()}",
                json={
                    "kind": "deposit",
                    "amount": "-5",
                    "currency": "usd",
                    "contributed_on": "2026-11-23",
                },
                headers=headers,
            ),
            await client.put(
                f"/goals/{goal_id}/contributions/{uuid4()}",
                json={
                    "kind": "deposit",
                    "amount": "10",
                    "currency": "usd",
                    "contributed_on": "2026-11-23",
                    "note": "x" * 501,
                },
                headers=headers,
            ),
        ]

    assert all(resp.status_code == 422 for resp in responses)
