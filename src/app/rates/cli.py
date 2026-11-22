import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from decimal import Decimal, InvalidOperation

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.clock import Clock, SystemClock
from app.config import Settings
from app.db import create_engine, create_session_factory
from app.errors import BusinessRuleError
from app.rates.domain import Quote, QuoteSource, RateSources
from app.rates.errors import QuoteUnavailable
from app.rates.repository import SqlAlchemyExchangeRateRepository
from app.rates.service import RatesService
from app.rates.sources import (
    BcvHtmlSource,
    BinanceP2PSource,
    DolarApiSource,
    build_bcv_http_client,
    build_default_http_client,
)

CLI_ERRORS = (
    BusinessRuleError,
    ValueError,
    InvalidOperation,
    ValidationError,
    SQLAlchemyError,
    OSError,
)


class _NoopSource:
    async def fetch(self) -> Quote:
        raise QuoteUnavailable("Network sources are disabled for this command.")


def noop_sources() -> RateSources:
    """Return placeholder sources for offline rate commands."""
    source: QuoteSource = _NoopSource()
    return RateSources(bcv_html=source, dolarapi=source, binance=source)


@asynccontextmanager
async def open_rates_service(
    settings: Settings,
    *,
    sources: RateSources | None = None,
    clock: Clock | None = None,
) -> AsyncIterator[RatesService]:
    """Yield a wired RatesService and dispose of DB/HTTP resources on exit."""
    resolved_clock = clock or SystemClock()
    engine = create_engine(settings.database_url)
    try:
        session_factory = create_session_factory(engine)
        async with session_factory() as session:
            if sources is not None:
                yield RatesService(
                    repository=SqlAlchemyExchangeRateRepository(session),
                    sources=sources,
                    clock=resolved_clock,
                    tolerance=settings.bcv_tolerance_percent,
                )
            else:
                async with (
                    build_bcv_http_client() as bcv_client,
                    build_default_http_client() as default_client,
                ):
                    live_sources = RateSources(
                        bcv_html=BcvHtmlSource(bcv_client),
                        dolarapi=DolarApiSource(default_client),
                        binance=BinanceP2PSource(default_client, resolved_clock),
                    )
                    yield RatesService(
                        repository=SqlAlchemyExchangeRateRepository(session),
                        sources=live_sources,
                        clock=resolved_clock,
                        tolerance=settings.bcv_tolerance_percent,
                    )
    finally:
        await engine.dispose()


def parse_decimal_arg(raw: str) -> Decimal:
    """Parse a CLI decimal argument, rejecting non-finite values."""
    value = Decimal(raw)
    if not value.is_finite():
        raise ValueError(f"Invalid numeric value: {raw!r}")
    return value


def run_cli(action: Callable[[], int]) -> int:
    """Execute a CLI callback and convert domain/DB errors to exit code 1."""
    try:
        return action()
    except CLI_ERRORS as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Aborted.", file=sys.stderr)
        return 1
