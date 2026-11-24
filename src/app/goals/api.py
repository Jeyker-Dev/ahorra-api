from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.clock import Clock
from app.db import get_session
from app.goals.domain import ContributionInput, GoalCollaborators, GoalInput, RateSnapshot
from app.goals.repository import (
    SqlAlchemyGoalContributionRepository,
    SqlAlchemyGoalRepository,
)
from app.goals.schemas import (
    ContributionOut,
    ContributionUpsertIn,
    GoalListQuery,
    GoalOut,
    GoalUpsertIn,
)
from app.goals.service import GoalsService
from app.rates.api import get_rates_service
from app.rates.service import RatesService
from app.users.domain import User

router = APIRouter(prefix="/goals", tags=["goals"])


class _RatesServiceLookupAdapter:
    """Adapter exposing RatesService BCV and USDT lookups through RateLookup."""

    def __init__(self, rates_service: RatesService) -> None:
        self._rates_service = rates_service

    async def bcv_in_force(self, on: date) -> RateSnapshot | None:
        rate = await self._rates_service.bcv_in_force(on)
        if rate is None:
            return None
        return RateSnapshot(value=rate.value, value_date=rate.value_date)

    async def usdt_in_force(self, on: date) -> RateSnapshot | None:
        rate = await self._rates_service.usdt_in_force(on)
        if rate is None:
            return None
        return RateSnapshot(value=rate.value, value_date=rate.value_date)


def get_goals_service(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    rates_service: Annotated[RatesService, Depends(get_rates_service)],
) -> GoalsService:
    """Build GoalsService with request-scoped repositories and app singletons."""
    clock: Clock = request.app.state.clock
    return GoalsService(
        collaborators=GoalCollaborators(
            goals=SqlAlchemyGoalRepository(session),
            contributions=SqlAlchemyGoalContributionRepository(session),
            rates=_RatesServiceLookupAdapter(rates_service),
            clock=clock,
        )
    )


@router.get("", response_model=list[GoalOut])
async def list_goals(
    query: Annotated[GoalListQuery, Query()],
    _current_user: Annotated[User, Depends(get_current_user)],
    goals_service: Annotated[GoalsService, Depends(get_goals_service)],
) -> list[GoalOut]:
    """List non-deleted savings goals, active only by default or all with active first."""
    items = (
        await goals_service.list_all_goals()
        if query.include_archived
        else await goals_service.list_active_goals()
    )
    return [GoalOut.from_with_progress(item) for item in items]


@router.put("/{goal_id}", response_model=GoalOut)
async def upsert_goal(
    goal_id: UUID,
    body: GoalUpsertIn,
    response: Response,
    _current_user: Annotated[User, Depends(get_current_user)],
    goals_service: Annotated[GoalsService, Depends(get_goals_service)],
) -> GoalOut:
    """Create or idempotently update a savings goal."""
    result = await goals_service.upsert_goal(
        GoalInput(
            id=goal_id,
            name=body.name,
            target_usd=body.target_usd,
            target_date=body.target_date,
            note=body.note,
        )
    )
    response.status_code = status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
    return GoalOut.from_with_progress(result.item)


@router.get("/{goal_id}", response_model=GoalOut)
async def get_goal(
    goal_id: UUID,
    _current_user: Annotated[User, Depends(get_current_user)],
    goals_service: Annotated[GoalsService, Depends(get_goals_service)],
) -> GoalOut:
    """Return a single non-deleted savings goal with its progress."""
    item = await goals_service.get_goal(goal_id)
    return GoalOut.from_with_progress(item)


@router.post("/{goal_id}/archive", response_model=GoalOut)
async def archive_goal(
    goal_id: UUID,
    _current_user: Annotated[User, Depends(get_current_user)],
    goals_service: Annotated[GoalsService, Depends(get_goals_service)],
) -> GoalOut:
    """Archive a savings goal idempotently."""
    item = await goals_service.archive_goal(goal_id)
    return GoalOut.from_with_progress(item)


@router.post("/{goal_id}/unarchive", response_model=GoalOut)
async def unarchive_goal(
    goal_id: UUID,
    _current_user: Annotated[User, Depends(get_current_user)],
    goals_service: Annotated[GoalsService, Depends(get_goals_service)],
) -> GoalOut:
    """Unarchive a savings goal idempotently."""
    item = await goals_service.unarchive_goal(goal_id)
    return GoalOut.from_with_progress(item)


@router.delete("/{goal_id}", response_model=GoalOut)
async def delete_goal(
    goal_id: UUID,
    _current_user: Annotated[User, Depends(get_current_user)],
    goals_service: Annotated[GoalsService, Depends(get_goals_service)],
) -> GoalOut:
    """Soft-delete a savings goal idempotently."""
    item = await goals_service.delete_goal(goal_id)
    return GoalOut.from_with_progress(item)


@router.put("/{goal_id}/contributions/{contribution_id}", response_model=ContributionOut)
async def upsert_contribution(
    goal_id: UUID,
    contribution_id: UUID,
    body: ContributionUpsertIn,
    response: Response,
    _current_user: Annotated[User, Depends(get_current_user)],
    goals_service: Annotated[GoalsService, Depends(get_goals_service)],
) -> ContributionOut:
    """Create, update, or move a goal contribution idempotently."""
    result = await goals_service.upsert_contribution(
        ContributionInput(
            id=contribution_id,
            goal_id=goal_id,
            kind=body.kind,
            amount=body.amount,
            currency=body.currency,
            contributed_on=body.contributed_on,
            note=body.note,
        )
    )
    response.status_code = status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
    return ContributionOut.from_domain(result.contribution)


@router.get("/{goal_id}/contributions", response_model=list[ContributionOut])
async def list_contributions(
    goal_id: UUID,
    _current_user: Annotated[User, Depends(get_current_user)],
    goals_service: Annotated[GoalsService, Depends(get_goals_service)],
) -> list[ContributionOut]:
    """List non-deleted contributions for a goal."""
    contributions = await goals_service.list_contributions(goal_id)
    return [ContributionOut.from_domain(c) for c in contributions]


@router.delete("/{goal_id}/contributions/{contribution_id}", response_model=ContributionOut)
async def delete_contribution(
    goal_id: UUID,
    contribution_id: UUID,
    _current_user: Annotated[User, Depends(get_current_user)],
    goals_service: Annotated[GoalsService, Depends(get_goals_service)],
) -> ContributionOut:
    """Soft-delete a goal contribution idempotently."""
    contribution = await goals_service.delete_contribution(goal_id, contribution_id)
    return ContributionOut.from_domain(contribution)
