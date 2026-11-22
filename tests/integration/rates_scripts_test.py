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
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings
from app.rates.domain import (
    Quote,
    RateKind,
    RateOrigin,
    RateSources,
    RateStatus,
)
from app.rates.errors import QuoteUnavailable, RateUnavailable

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


class _FakeSource:
    def __init__(self, result: Quote | Exception) -> None:
        self._result = result

    async def fetch(self) -> Quote:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


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
            await conn.execute(text("TRUNCATE TABLE exchange_rates CASCADE"))
        yield test_database_url
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE TABLE exchange_rates CASCADE"))
        await engine.dispose()


def _make_settings(database_url: str) -> Settings:
    return Settings(
        _env_file=None,
        database_url=database_url,
        jwt_secret=SecretStr(TEST_JWT_SECRET),
    )


def _run_cli_subprocess(
    database_url: str,
    script: str,
    *args: str,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["JWT_SECRET"] = TEST_JWT_SECRET
    return subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / script), *args],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


async def test_fetch_rates_run_persists_bcv_and_usdt_with_fake_sources(
    migrated_db_url: str,
) -> None:
    fetch_rates = _load_script("fetch_rates")
    settings = _make_settings(migrated_db_url)
    day = date(2026, 11, 20)
    clock = _FakeClock(datetime(2026, 11, 20, 15, 0, tzinfo=UTC))
    sources = RateSources(
        bcv_html=_FakeSource(Quote(value=Decimal("917.7751"), value_date=day)),
        dolarapi=_FakeSource(Quote(value=Decimal("918.00"), value_date=day)),
        binance=_FakeSource(Quote(value=Decimal("952.50"), value_date=day)),
    )

    bcv_rate = await fetch_rates.run(RateKind.bcv, settings=settings, sources=sources, clock=clock)
    usdt_rate = await fetch_rates.run(
        RateKind.usdt,
        settings=settings,
        sources=sources,
        clock=clock,
    )

    assert bcv_rate.origin == RateOrigin.bcv_html
    assert bcv_rate.status == RateStatus.confirmed
    assert usdt_rate.origin == RateOrigin.binance_p2p
    assert usdt_rate.status == RateStatus.confirmed

    failing_sources = RateSources(
        bcv_html=_FakeSource(QuoteUnavailable("down")),
        dolarapi=_FakeSource(QuoteUnavailable("down")),
        binance=_FakeSource(QuoteUnavailable("down")),
    )
    with pytest.raises(RateUnavailable):
        await fetch_rates.run(
            RateKind.bcv,
            settings=settings,
            sources=failing_sources,
            clock=clock,
        )


async def test_set_rate_and_review_rates_run_against_db(migrated_db_url: str) -> None:
    fetch_rates = _load_script("fetch_rates")
    set_rate = _load_script("set_rate")
    review_rates = _load_script("review_rates")
    settings = _make_settings(migrated_db_url)
    clock = _FakeClock(datetime(2026, 11, 20, 2, 0, tzinfo=UTC))

    # 02:00 UTC is 22:00 on 2026-11-19 in America/Caracas.
    manual_usdt = await set_rate.run(
        RateKind.usdt,
        Decimal("955.125"),
        None,
        settings=settings,
        clock=clock,
    )
    assert manual_usdt.value_date == date(2026, 11, 19)
    assert manual_usdt.status == RateStatus.confirmed

    # Create two in_review rates via fetch_rates (dolarapi fallback and mismatch).
    day_one = date(2026, 11, 18)
    day_two = date(2026, 11, 19)
    in_review_one = await fetch_rates.run(
        RateKind.bcv,
        settings=settings,
        sources=RateSources(
            bcv_html=_FakeSource(QuoteUnavailable("down")),
            dolarapi=_FakeSource(Quote(value=Decimal("915.00"), value_date=day_one)),
            binance=_FakeSource(QuoteUnavailable("unused")),
        ),
        clock=clock,
    )
    in_review_two = await fetch_rates.run(
        RateKind.bcv,
        settings=settings,
        sources=RateSources(
            bcv_html=_FakeSource(Quote(value=Decimal("917.00"), value_date=day_two)),
            dolarapi=_FakeSource(Quote(value=Decimal("930.00"), value_date=day_two)),
            binance=_FakeSource(QuoteUnavailable("unused")),
        ),
        clock=clock,
    )

    pending = await review_rates.run_list(settings=settings, clock=clock)
    assert {rate.id for rate in pending} == {in_review_one.id, in_review_two.id}

    confirmed = await review_rates.run_confirm(in_review_one.id, settings=settings, clock=clock)
    assert confirmed.status == RateStatus.confirmed

    corrected = await review_rates.run_correct(
        in_review_two.id,
        Decimal("917.7751"),
        settings=settings,
        clock=clock,
    )
    assert corrected.status == RateStatus.confirmed
    assert corrected.origin == RateOrigin.manual
    assert corrected.value == Decimal("917.77510000")

    assert await review_rates.run_list(settings=settings, clock=clock) == []


async def test_scripts_cli_parsing_and_exit_codes(migrated_db_url: str) -> None:
    set_ok = await asyncio.to_thread(
        _run_cli_subprocess,
        migrated_db_url,
        "set_rate.py",
        "--kind",
        "bcv",
        "--value",
        "917.7751",
        "--date",
        "2026-11-20",
    )
    assert set_ok.returncode == 0
    assert "Saved manual bcv" in set_ok.stdout

    set_bad_value = await asyncio.to_thread(
        _run_cli_subprocess,
        migrated_db_url,
        "set_rate.py",
        "--kind",
        "bcv",
        "--value",
        "-5",
    )
    assert set_bad_value.returncode == 1
    assert "Traceback" not in set_bad_value.stderr
    assert set_bad_value.stderr.strip() != ""

    review_missing = await asyncio.to_thread(
        _run_cli_subprocess,
        migrated_db_url,
        "review_rates.py",
        "confirm",
        str(uuid4()),
    )
    assert review_missing.returncode == 1
    assert "Traceback" not in review_missing.stderr
    assert review_missing.stderr.strip() != ""

    bad_arg = await asyncio.to_thread(
        _run_cli_subprocess,
        migrated_db_url,
        "fetch_rates.py",
        "--kind",
        "eur",
    )
    assert bad_arg.returncode == 2
