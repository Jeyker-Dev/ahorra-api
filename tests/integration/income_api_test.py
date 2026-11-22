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
                    "TRUNCATE TABLE income_occurrences, income_rules, "
                    "exchange_rates, refresh_tokens, users CASCADE"
                )
            )
        yield test_database_url
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "TRUNCATE TABLE income_occurrences, income_rules, "
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


async def _insert_bcv_rate(
    database_url: str,
    value_date: date,
    value: Decimal,
) -> None:
    rate = ExchangeRate(
        id=uuid4(),
        kind=RateKind.bcv,
        value=value,
        value_date=value_date,
        origin=RateOrigin.bcv_html,
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


async def test_income_endpoints_require_authentication(migrated_db_url: str) -> None:
    fixed_now = datetime(2026, 11, 22, 14, 0, tzinfo=UTC)
    application = create_app(settings=_make_settings(migrated_db_url), clock=_FakeClock(fixed_now))
    some_id = uuid4()

    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client,
    ):
        responses = [
            await client.put(
                f"/income/rules/{some_id}",
                json={
                    "name": "Salary",
                    "amount": "500.00",
                    "currency": "usd",
                    "days": [15],
                    "start_date": "2026-11-01",
                },
            ),
            await client.get("/income/rules"),
            await client.get(f"/income/rules/{some_id}"),
            await client.delete(f"/income/rules/{some_id}"),
            await client.get(
                "/income/occurrences",
                params={"from": "2026-11-01", "to": "2026-12-31"},
            ),
            await client.post(f"/income/occurrences/{some_id}/receive"),
            await client.post(f"/income/occurrences/{some_id}/unreceive"),
        ]

    assert all(resp.status_code == 401 for resp in responses)


async def test_put_rule_idempotency_update_and_get_rules_ordering(migrated_db_url: str) -> None:
    clock = _FakeClock(datetime(2026, 11, 22, 14, 0, tzinfo=UTC))
    application = create_app(settings=_make_settings(migrated_db_url), clock=clock)
    salary_id = uuid4()
    bonus_id = uuid4()

    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client,
    ):
        headers = await _create_auth_header(migrated_db_url, client, clock.current)

        create_resp = await client.put(
            f"/income/rules/{salary_id}",
            json={
                "name": "  Salary ",
                "amount": "1000.555",
                "currency": "usd",
                "days": [30, 15, 30],
                "start_date": "2026-11-01",
            },
            headers=headers,
        )
        assert create_resp.status_code == 201
        created_rule = create_resp.json()
        assert created_rule["id"] == str(salary_id)
        assert created_rule["name"] == "Salary"
        assert created_rule["amount"] == "1000.56"
        assert created_rule["days"] == [15, 30]
        assert created_rule["ended_at"] is None
        assert "updated_at" in created_rule

        # Repeat same payload -> 200 and no duplicate occurrences
        clock.current = datetime(2026, 11, 22, 14, 5, tzinfo=UTC)
        repeat_resp = await client.put(
            f"/income/rules/{salary_id}",
            json={
                "name": "Salary",
                "amount": "1000.56",
                "currency": "usd",
                "days": [15, 30],
                "start_date": "2026-11-01",
            },
            headers=headers,
        )
        assert repeat_resp.status_code == 200
        assert repeat_resp.json() == created_rule

        # Update rule -> 200 and regenerates future expected occurrences
        clock.current = datetime(2026, 11, 22, 14, 6, tzinfo=UTC)
        update_resp = await client.put(
            f"/income/rules/{salary_id}",
            json={
                "name": "Salary Updated",
                "amount": "1100.00",
                "currency": "usd",
                "days": [15, 30],
                "start_date": "2026-11-01",
            },
            headers=headers,
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["name"] == "Salary Updated"
        assert update_resp.json()["amount"] == "1100.00"

        occ_resp = await client.get(
            "/income/occurrences",
            params={"from": "2026-11-01", "to": "2027-02-28"},
            headers=headers,
        )
        assert occ_resp.status_code == 200
        occ_dates = [item["expected_date"] for item in occ_resp.json()]
        assert occ_dates == [
            "2026-11-15",
            "2026-11-30",
            "2026-12-15",
            "2026-12-30",
            "2027-01-15",
            "2027-01-30",
            "2027-02-15",
        ]

        # Create second rule and end the first -> active first, then ended, ordered by name
        bonus_resp = await client.put(
            f"/income/rules/{bonus_id}",
            json={
                "name": "Zulu Bonus",
                "amount": "200.00",
                "currency": "ves",
                "days": [1],
                "start_date": "2026-11-01",
            },
            headers=headers,
        )
        assert bonus_resp.status_code == 201

        end_resp = await client.delete(f"/income/rules/{salary_id}", headers=headers)
        assert end_resp.status_code == 200
        assert end_resp.json()["ended_at"] is not None

        rules_resp = await client.get("/income/rules", headers=headers)
        assert rules_resp.status_code == 200
        assert [r["id"] for r in rules_resp.json()] == [str(bonus_id), str(salary_id)]

        get_one = await client.get(f"/income/rules/{salary_id}", headers=headers)
        assert get_one.status_code == 200
        assert get_one.json()["id"] == str(salary_id)


async def test_income_occurrences_receive_unreceive_and_filters(migrated_db_url: str) -> None:
    clock = _FakeClock(datetime(2026, 11, 22, 14, 0, tzinfo=UTC))
    application = create_app(settings=_make_settings(migrated_db_url), clock=clock)
    rule_id = uuid4()

    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client,
    ):
        headers = await _create_auth_header(migrated_db_url, client, clock.current)
        await _insert_bcv_rate(migrated_db_url, date(2026, 11, 15), Decimal("915.00000000"))
        await _insert_bcv_rate(migrated_db_url, date(2026, 11, 20), Decimal("920.50000000"))

        await client.put(
            f"/income/rules/{rule_id}",
            json={
                "name": "Salary",
                "amount": "500.00",
                "currency": "usd",
                "days": [15, 30],
                "start_date": "2026-11-01",
            },
            headers=headers,
        )

        occ_list = (
            await client.get(
                "/income/occurrences",
                params={"from": "2026-11-01", "to": "2026-11-30"},
                headers=headers,
            )
        ).json()
        assert len(occ_list) == 2
        assert occ_list[0]["rule_id"] == str(rule_id)
        assert occ_list[0]["rule_name"] == "Salary"
        first_occ_id = UUID(occ_list[0]["id"])
        second_occ_id = UUID(occ_list[1]["id"])

        # Receive with omitted body -> defaults
        recv_default = await client.post(
            f"/income/occurrences/{first_occ_id}/receive",
            headers=headers,
        )
        assert recv_default.status_code == 200
        body_default = recv_default.json()
        assert body_default["status"] == "received"
        assert body_default["received_amount"] == "500.00"
        assert body_default["received_date"] == "2026-11-15"
        assert body_default["rate_value"] == "915.00000000"
        assert body_default["rate_date"] == "2026-11-15"

        # Correct received occurrence with explicit values
        recv_corrected = await client.post(
            f"/income/occurrences/{first_occ_id}/receive",
            json={"received_amount": "510.25", "received_date": "2026-11-20"},
            headers=headers,
        )
        assert recv_corrected.status_code == 200
        assert recv_corrected.json()["received_amount"] == "510.25"
        assert recv_corrected.json()["rate_value"] == "920.50000000"

        # Filter by status
        only_received = await client.get(
            "/income/occurrences",
            params={"from": "2026-11-01", "to": "2026-11-30", "status": "received"},
            headers=headers,
        )
        assert [item["id"] for item in only_received.json()] == [str(first_occ_id)]

        only_expected = await client.get(
            "/income/occurrences",
            params={"from": "2026-11-01", "to": "2026-11-30", "status": "expected"},
            headers=headers,
        )
        assert [item["id"] for item in only_expected.json()] == [str(second_occ_id)]

        # Unreceive
        unrecv = await client.post(
            f"/income/occurrences/{first_occ_id}/unreceive",
            headers=headers,
        )
        assert unrecv.status_code == 200
        assert unrecv.json()["status"] == "expected"
        assert unrecv.json()["received_amount"] is None
        assert unrecv.json()["rate_value"] is None


async def test_income_error_mappings_404_409_422(migrated_db_url: str) -> None:
    clock = _FakeClock(datetime(2026, 11, 22, 14, 0, tzinfo=UTC))
    application = create_app(settings=_make_settings(migrated_db_url), clock=clock)
    missing_id = uuid4()
    rule_id = uuid4()

    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client,
    ):
        headers = await _create_auth_header(migrated_db_url, client, clock.current)

        # 404 cases
        assert (await client.get(f"/income/rules/{missing_id}", headers=headers)).status_code == 404
        assert (
            await client.delete(f"/income/rules/{missing_id}", headers=headers)
        ).status_code == 404
        assert (
            await client.post(f"/income/occurrences/{missing_id}/receive", headers=headers)
        ).status_code == 404
        assert (
            await client.post(f"/income/occurrences/{missing_id}/unreceive", headers=headers)
        ).status_code == 404

        # 422 cases
        bad_payloads: list[dict[str, object]] = [
            {
                "name": "   ",
                "amount": "100",
                "currency": "usd",
                "days": [15],
                "start_date": "2026-11-01",
            },
            {
                "name": "Salary",
                "amount": "0",
                "currency": "usd",
                "days": [15],
                "start_date": "2026-11-01",
            },
            {
                "name": "Salary",
                "amount": "100",
                "currency": "usd",
                "days": [],
                "start_date": "2026-11-01",
            },
            {
                "name": "Salary",
                "amount": "100",
                "currency": "usd",
                "days": [32],
                "start_date": "2026-11-01",
            },
            {
                "name": "Salary",
                "amount": "100",
                "currency": "usd",
                "days": [15],
                "start_date": "2026-11-10",
                "end_date": "2026-11-09",
            },
        ]
        for payload in bad_payloads:
            resp = await client.put(f"/income/rules/{uuid4()}", json=payload, headers=headers)
            assert resp.status_code == 422

        bad_range = await client.get(
            "/income/occurrences",
            params={"from": "2026-11-20", "to": "2026-11-10"},
            headers=headers,
        )
        assert bad_range.status_code == 422

        # Create rule to test 409 cases
        await client.put(
            f"/income/rules/{rule_id}",
            json={
                "name": "Salary",
                "amount": "100.00",
                "currency": "usd",
                "days": [15],
                "start_date": "2026-11-01",
            },
            headers=headers,
        )
        occ_id = (
            await client.get(
                "/income/occurrences",
                params={"from": "2026-11-01", "to": "2026-11-30"},
                headers=headers,
            )
        ).json()[0]["id"]

        # 409 RateNotAvailableForDate (no BCV rates seeded)
        assert (
            await client.post(f"/income/occurrences/{occ_id}/receive", headers=headers)
        ).status_code == 409

        # 422 InvalidIncomeAmount on receive
        assert (
            await client.post(
                f"/income/occurrences/{occ_id}/receive",
                json={"received_amount": "-10"},
                headers=headers,
            )
        ).status_code == 422

        # 409 OccurrenceNotReceived on unreceive
        assert (
            await client.post(f"/income/occurrences/{occ_id}/unreceive", headers=headers)
        ).status_code == 409

        # End rule then 409 IncomeRuleEnded on PUT
        await client.delete(f"/income/rules/{rule_id}", headers=headers)
        ended_put = await client.put(
            f"/income/rules/{rule_id}",
            json={
                "name": "Salary Updated",
                "amount": "200.00",
                "currency": "usd",
                "days": [15],
                "start_date": "2026-11-01",
            },
            headers=headers,
        )
        assert ended_put.status_code == 409
