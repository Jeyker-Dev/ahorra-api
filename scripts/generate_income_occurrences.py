import asyncio
import logging
import sys
from datetime import date

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.clock import Clock, SystemClock
from app.config import Settings, get_settings
from app.db import create_engine, create_session_factory
from app.errors import BusinessRuleError
from app.income.domain import IncomeCollaborators, RateSnapshot
from app.income.repository import (
    SqlAlchemyIncomeOccurrenceRepository,
    SqlAlchemyIncomeRuleRepository,
)
from app.income.service import IncomeService

_CLI_ERRORS = (
    BusinessRuleError,
    ValueError,
    ValidationError,
    SQLAlchemyError,
    OSError,
)


class _NoopRateLookup:
    async def bcv_in_force(self, on: date) -> RateSnapshot | None:
        _ = on
        return None


async def run(
    *,
    settings: Settings,
    clock: Clock | None = None,
    collaborators: IncomeCollaborators | None = None,
) -> int:
    """Generate missing expected income occurrences up to the configured horizon."""
    if collaborators is not None:
        service = IncomeService(
            collaborators=collaborators,
            horizon_months=settings.income_horizon_months,
        )
        return await service.generate_occurrences()

    resolved_clock = clock or SystemClock()
    engine = create_engine(settings.database_url)
    try:
        session_factory = create_session_factory(engine)
        async with session_factory() as session:
            service = IncomeService(
                collaborators=IncomeCollaborators(
                    rules=SqlAlchemyIncomeRuleRepository(session),
                    occurrences=SqlAlchemyIncomeOccurrenceRepository(session),
                    rates=_NoopRateLookup(),
                    clock=resolved_clock,
                ),
                horizon_months=settings.income_horizon_months,
            )
            return await service.generate_occurrences()
    finally:
        await engine.dispose()


def main() -> int:
    """Run occurrence generation from the CLI and print the created count."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        created = asyncio.run(run(settings=get_settings()))
    except _CLI_ERRORS as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Aborted.", file=sys.stderr)
        return 1

    print(f"Generated {created} income occurrence(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
