from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.clock import Clock
from app.config import Settings
from app.db import get_session
from app.income.domain import IncomeCollaborators, IncomeRuleInput, RateSnapshot
from app.income.repository import (
    SqlAlchemyIncomeOccurrenceRepository,
    SqlAlchemyIncomeRuleRepository,
)
from app.income.schemas import (
    IncomeRuleOut,
    IncomeRuleUpsertIn,
    OccurrenceListQuery,
    OccurrenceOut,
    OccurrenceWithRuleOut,
    ReceiveOccurrenceIn,
)
from app.income.service import IncomeService
from app.rates.api import get_rates_service
from app.rates.service import RatesService
from app.users.domain import User

router = APIRouter(prefix="/income", tags=["income"])


class _RatesServiceLookupAdapter:
    """Adapter exposing RatesService.bcv_in_force through BcvRateLookup."""

    def __init__(self, rates_service: RatesService) -> None:
        self._rates_service = rates_service

    async def bcv_in_force(self, on: date) -> RateSnapshot | None:
        rate = await self._rates_service.bcv_in_force(on)
        if rate is None:
            return None
        return RateSnapshot(value=rate.value, value_date=rate.value_date)


def get_income_service(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    rates_service: Annotated[RatesService, Depends(get_rates_service)],
) -> IncomeService:
    """Build IncomeService with request-scoped repositories and app singletons."""
    clock: Clock = request.app.state.clock
    settings: Settings = request.app.state.settings
    return IncomeService(
        collaborators=IncomeCollaborators(
            rules=SqlAlchemyIncomeRuleRepository(session),
            occurrences=SqlAlchemyIncomeOccurrenceRepository(session),
            rates=_RatesServiceLookupAdapter(rates_service),
            clock=clock,
        ),
        horizon_months=settings.income_horizon_months,
    )


@router.put("/rules/{rule_id}", response_model=IncomeRuleOut)
async def upsert_income_rule(
    rule_id: UUID,
    body: IncomeRuleUpsertIn,
    response: Response,
    _current_user: Annotated[User, Depends(get_current_user)],
    income_service: Annotated[IncomeService, Depends(get_income_service)],
) -> IncomeRuleOut:
    """Create or idempotently update an income rule."""
    result = await income_service.upsert_rule(
        IncomeRuleInput(
            id=rule_id,
            name=body.name,
            amount=body.amount,
            currency=body.currency,
            days=body.days,
            start_date=body.start_date,
            end_date=body.end_date,
        )
    )
    response.status_code = status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
    return IncomeRuleOut.from_domain(result.rule)


@router.get("/rules", response_model=list[IncomeRuleOut])
async def list_income_rules(
    _current_user: Annotated[User, Depends(get_current_user)],
    income_service: Annotated[IncomeService, Depends(get_income_service)],
) -> list[IncomeRuleOut]:
    """List all income rules, active first, then ended, ordered by name."""
    rules = await income_service.list_rules()
    return [IncomeRuleOut.from_domain(rule) for rule in rules]


@router.get("/rules/{rule_id}", response_model=IncomeRuleOut)
async def get_income_rule(
    rule_id: UUID,
    _current_user: Annotated[User, Depends(get_current_user)],
    income_service: Annotated[IncomeService, Depends(get_income_service)],
) -> IncomeRuleOut:
    """Return a single income rule by ID."""
    rule = await income_service.get_rule(rule_id)
    return IncomeRuleOut.from_domain(rule)


@router.delete("/rules/{rule_id}", response_model=IncomeRuleOut)
async def end_income_rule(
    rule_id: UUID,
    _current_user: Annotated[User, Depends(get_current_user)],
    income_service: Annotated[IncomeService, Depends(get_income_service)],
) -> IncomeRuleOut:
    """End an income rule and remove its future expected occurrences."""
    rule = await income_service.end_rule(rule_id)
    return IncomeRuleOut.from_domain(rule)


@router.get("/occurrences", response_model=list[OccurrenceWithRuleOut])
async def list_income_occurrences(
    query: Annotated[OccurrenceListQuery, Query()],
    _current_user: Annotated[User, Depends(get_current_user)],
    income_service: Annotated[IncomeService, Depends(get_income_service)],
) -> list[OccurrenceWithRuleOut]:
    """List income occurrences within an inclusive expected_date range."""
    items = await income_service.list_occurrences(
        from_date=query.from_date,
        to_date=query.to_date,
        status=query.status,
    )
    return [OccurrenceWithRuleOut.from_with_rule(item) for item in items]


@router.post("/occurrences/{occurrence_id}/receive", response_model=OccurrenceOut)
async def receive_income_occurrence(
    occurrence_id: UUID,
    _current_user: Annotated[User, Depends(get_current_user)],
    income_service: Annotated[IncomeService, Depends(get_income_service)],
    body: Annotated[ReceiveOccurrenceIn | None, Body()] = None,
) -> OccurrenceOut:
    """Mark an occurrence as received or correct an already received one."""
    payload = body if body is not None else ReceiveOccurrenceIn()
    occurrence = await income_service.receive(
        occurrence_id,
        received_amount=payload.received_amount,
        received_date=payload.received_date,
    )
    return OccurrenceOut.from_domain(occurrence)


@router.post("/occurrences/{occurrence_id}/unreceive", response_model=OccurrenceOut)
async def unreceive_income_occurrence(
    occurrence_id: UUID,
    _current_user: Annotated[User, Depends(get_current_user)],
    income_service: Annotated[IncomeService, Depends(get_income_service)],
) -> OccurrenceOut:
    """Revert a received occurrence back to expected."""
    occurrence = await income_service.unreceive(occurrence_id)
    return OccurrenceOut.from_domain(occurrence)
