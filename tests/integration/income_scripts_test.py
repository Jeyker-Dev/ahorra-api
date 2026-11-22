import asyncio
import importlib.util
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import ModuleType
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

TEST_JWT_SECRET = "test-secret-key-with-at-least-32-chars"
SCRIPTS_DIR = Path("scripts")


def _load_script(name: str) -> ModuleType:
    path = SCRIPTS_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"script_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
                    "TRUNCATE TABLE income_occurrences, income_rules, refresh_tokens, users CASCADE"
                )
            )
        yield test_database_url
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "TRUNCATE TABLE income_occurrences, income_rules, refresh_tokens, users CASCADE"
                )
            )
        await engine.dispose()


def _make_settings(database_url: str) -> Settings:
    return Settings(
        _env_file=None,
        database_url=database_url,
        jwt_secret=SecretStr(TEST_JWT_SECRET),
    )


async def test_generate_income_occurrences_run_twice_creates_only_once(
    migrated_db_url: str,
) -> None:
    script = _load_script("generate_income_occurrences")
    settings = _make_settings(migrated_db_url)
    now = datetime(2026, 11, 22, 4, 30, tzinfo=UTC)
    clock = _FakeClock(now)

    engine = create_async_engine(migrated_db_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO income_rules "
                    "(id, name, amount, currency, days, start_date, end_date, "
                    "ended_at, created_at, updated_at) "
                    "VALUES (:id, 'Salary', :amount, 'usd', :days, :start_date, NULL, "
                    "NULL, :now, :now)"
                ),
                {
                    "id": str(uuid4()),
                    "amount": Decimal("500.00"),
                    "days": [15],
                    "start_date": date(2026, 11, 1),
                    "now": now,
                },
            )
    finally:
        await engine.dispose()

    first_count = await script.run(settings=settings, clock=clock)
    second_count = await script.run(settings=settings, clock=clock)

    assert first_count == 3  # Dec 15, Jan 15, Feb 15 (no backfill before today)
    assert second_count == 0


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


async def test_put_rule_days_change_and_daily_job_do_not_backfill_past_occurrences(
    migrated_db_url: str,
) -> None:
    script = _load_script("generate_income_occurrences")
    settings = _make_settings(migrated_db_url)
    clock = _FakeClock(datetime(2026, 11, 22, 14, 0, tzinfo=UTC))
    application = create_app(settings=settings, clock=clock)
    rule_id = uuid4()

    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client,
    ):
        headers = await _create_auth_header(migrated_db_url, client, clock.current)

        create_resp = await client.put(
            f"/income/rules/{rule_id}",
            json={
                "name": "Salary",
                "amount": "500.00",
                "currency": "usd",
                "days": [15],
                "start_date": "2026-09-01",
            },
            headers=headers,
        )
        assert create_resp.status_code == 201

        update_resp = await client.put(
            f"/income/rules/{rule_id}",
            json={
                "name": "Salary",
                "amount": "500.00",
                "currency": "usd",
                "days": [16],
                "start_date": "2026-09-01",
            },
            headers=headers,
        )
        assert update_resp.status_code == 200

        created_on_daily_run = await script.run(settings=settings, clock=clock)
        assert created_on_daily_run == 0

        occ_resp = await client.get(
            "/income/occurrences",
            params={"from": "2026-09-01", "to": "2027-02-28"},
            headers=headers,
        )
        assert occ_resp.status_code == 200
        assert [item["expected_date"] for item in occ_resp.json()] == [
            "2026-09-15",
            "2026-10-15",
            "2026-11-15",
            "2026-12-16",
            "2027-01-16",
            "2027-02-16",
        ]


async def test_generate_income_occurrences_cli_exits_zero_and_prints_count(
    migrated_db_url: str,
) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = migrated_db_url
    env["JWT_SECRET"] = TEST_JWT_SECRET
    proc = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, str(SCRIPTS_DIR / "generate_income_occurrences.py")],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert proc.returncode == 0
    assert "Generated 0 income occurrence(s)" in proc.stdout
