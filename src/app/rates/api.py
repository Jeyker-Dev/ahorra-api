from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.exceptions import RequestValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.clock import Clock, caracas_date
from app.config import Settings
from app.db import get_session
from app.rates.domain import RateSources
from app.rates.repository import SqlAlchemyExchangeRateRepository
from app.rates.schemas import LatestRatesOut, RateHistoryQuery, RateOut
from app.rates.service import RatesService
from app.users.domain import User

DEFAULT_HISTORY_DAYS = 30

router = APIRouter(prefix="/rates", tags=["rates"])


def get_rates_service(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RatesService:
    """Build RatesService with request-scoped repository and app singletons."""
    clock: Clock = request.app.state.clock
    settings: Settings = request.app.state.settings
    sources: RateSources = request.app.state.rate_sources
    return RatesService(
        repository=SqlAlchemyExchangeRateRepository(session),
        sources=sources,
        clock=clock,
        tolerance=settings.bcv_tolerance_percent,
    )


@router.get("/latest", response_model=LatestRatesOut)
async def read_latest_rates(
    _current_user: Annotated[User, Depends(get_current_user)],
    rates_service: Annotated[RatesService, Depends(get_rates_service)],
) -> LatestRatesOut:
    """Return the most recent BCV and USDT exchange rates regardless of status."""
    bcv, usdt = await rates_service.get_latest()
    return LatestRatesOut(
        bcv=RateOut.from_domain(bcv) if bcv is not None else None,
        usdt=RateOut.from_domain(usdt) if usdt is not None else None,
    )


@router.get("", response_model=list[RateOut])
async def read_rate_history(
    request: Request,
    query: Annotated[RateHistoryQuery, Query()],
    _current_user: Annotated[User, Depends(get_current_user)],
    rates_service: Annotated[RatesService, Depends(get_rates_service)],
) -> list[RateOut]:
    """Return exchange rate history for a kind ordered by value_date, then fetched_at."""
    clock: Clock = request.app.state.clock
    today = caracas_date(clock.now())
    to_date = query.to_date if query.to_date is not None else today
    from_date = (
        query.from_date
        if query.from_date is not None
        else to_date - timedelta(days=DEFAULT_HISTORY_DAYS)
    )
    if from_date > to_date:
        raise RequestValidationError(
            [
                {
                    "type": "value_error",
                    "loc": ("query", "from"),
                    "msg": "'from' must be less than or equal to 'to'.",
                    "input": str(from_date),
                }
            ]
        )

    rates = await rates_service.get_history(
        kind=query.kind,
        from_date=from_date,
        to_date=to_date,
    )
    return [RateOut.from_domain(rate) for rate in rates]
