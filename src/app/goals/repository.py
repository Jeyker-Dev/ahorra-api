from collections.abc import Sequence
from decimal import Decimal
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.goals.domain import (
    ContributionKind,
    Currency,
    Goal,
    GoalContribution,
)
from app.goals.errors import ContributionNotFound, GoalNameTaken, GoalNotFound
from app.goals.models import GoalContributionModel, GoalModel

_GOAL_LOWER_NAME_INDEX = "uq_goals_lower_name_active"


def _goal_to_domain(model: GoalModel) -> Goal:
    return Goal(
        id=model.id,
        name=model.name,
        target_usd=model.target_usd,
        target_date=model.target_date,
        note=model.note,
        archived_at=model.archived_at,
        deleted_at=model.deleted_at,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _contribution_to_domain(model: GoalContributionModel) -> GoalContribution:
    return GoalContribution(
        id=model.id,
        goal_id=model.goal_id,
        kind=ContributionKind(model.kind),
        amount=model.amount,
        currency=Currency(model.currency),
        contributed_on=model.contributed_on,
        note=model.note,
        bcv_rate_value=model.bcv_rate_value,
        bcv_rate_date=model.bcv_rate_date,
        usdt_rate_value=model.usdt_rate_value,
        usdt_rate_date=model.usdt_rate_date,
        deleted_at=model.deleted_at,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


class SqlAlchemyGoalRepository:
    """SQLAlchemy implementation of GoalRepository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, goal_id: UUID) -> Goal | None:
        stmt = select(GoalModel).where(GoalModel.id == goal_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return _goal_to_domain(model) if model is not None else None

    async def get_by_lower_name(self, name: str) -> Goal | None:
        stmt = select(GoalModel).where(
            GoalModel.deleted_at.is_(None),
            func.lower(GoalModel.name) == name.lower(),
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return _goal_to_domain(model) if model is not None else None

    async def list_active(self) -> list[Goal]:
        stmt = (
            select(GoalModel)
            .where(
                GoalModel.deleted_at.is_(None),
                GoalModel.archived_at.is_(None),
            )
            .order_by(GoalModel.name.asc(), GoalModel.id.asc())
        )
        result = await self._session.execute(stmt)
        return [_goal_to_domain(model) for model in result.scalars().all()]

    async def list_all(self) -> list[Goal]:
        stmt = (
            select(GoalModel)
            .where(GoalModel.deleted_at.is_(None))
            .order_by(
                GoalModel.archived_at.is_not(None).asc(),
                GoalModel.name.asc(),
                GoalModel.id.asc(),
            )
        )
        result = await self._session.execute(stmt)
        return [_goal_to_domain(model) for model in result.scalars().all()]

    async def insert_if_absent(self, goal: Goal) -> tuple[Goal, bool]:
        stmt = (
            pg_insert(GoalModel)
            .values(
                id=goal.id,
                name=goal.name,
                target_usd=goal.target_usd,
                target_date=goal.target_date,
                note=goal.note,
                archived_at=goal.archived_at,
                deleted_at=goal.deleted_at,
                created_at=goal.created_at,
                updated_at=goal.updated_at,
            )
            .on_conflict_do_nothing(index_elements=[GoalModel.id])
            .returning(GoalModel.id)
        )
        try:
            result = await self._session.execute(stmt)
            inserted_id = result.scalar_one_or_none()
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            if _GOAL_LOWER_NAME_INDEX in str(exc):
                raise GoalNameTaken(f"Goal name {goal.name!r} is already taken.") from exc
            raise

        if inserted_id is not None:
            return goal, True

        existing = await self.get_by_id(goal.id)
        assert existing is not None
        return existing, False

    async def update(self, goal: Goal) -> Goal:
        model = await self._session.get(GoalModel, goal.id)
        if model is None:
            raise GoalNotFound(f"Goal {goal.id} was not found.")
        model.name = goal.name
        model.target_usd = goal.target_usd
        model.target_date = goal.target_date
        model.note = goal.note
        model.archived_at = goal.archived_at
        model.deleted_at = goal.deleted_at
        model.updated_at = goal.updated_at
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            if _GOAL_LOWER_NAME_INDEX in str(exc):
                raise GoalNameTaken(f"Goal name {goal.name!r} is already taken.") from exc
            raise
        return _goal_to_domain(model)


class SqlAlchemyGoalContributionRepository:
    """SQLAlchemy implementation of GoalContributionRepository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, contribution_id: UUID) -> GoalContribution | None:
        stmt = select(GoalContributionModel).where(GoalContributionModel.id == contribution_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return _contribution_to_domain(model) if model is not None else None

    async def list_for_goal(self, goal_id: UUID) -> list[GoalContribution]:
        stmt = (
            select(GoalContributionModel)
            .where(
                GoalContributionModel.goal_id == goal_id,
                GoalContributionModel.deleted_at.is_(None),
            )
            .order_by(
                GoalContributionModel.contributed_on.desc(),
                GoalContributionModel.created_at.desc(),
                GoalContributionModel.id.asc(),
            )
        )
        result = await self._session.execute(stmt)
        return [_contribution_to_domain(model) for model in result.scalars().all()]

    async def sum_saved_usd_by_goal_ids(
        self,
        goal_ids: Sequence[UUID],
    ) -> dict[UUID, Decimal]:
        if not goal_ids:
            return {}
        signed_usd = case(
            (
                GoalContributionModel.kind == ContributionKind.withdrawal.value,
                Decimal("-1"),
            ),
            else_=Decimal("1"),
        ) * case(
            (
                GoalContributionModel.currency == Currency.usd.value,
                GoalContributionModel.amount,
            ),
            else_=GoalContributionModel.amount / GoalContributionModel.bcv_rate_value,
        )
        stmt = (
            select(
                GoalContributionModel.goal_id,
                func.coalesce(func.sum(signed_usd), Decimal("0")),
            )
            .where(
                GoalContributionModel.deleted_at.is_(None),
                GoalContributionModel.goal_id.in_(goal_ids),
            )
            .group_by(GoalContributionModel.goal_id)
        )
        result = await self._session.execute(stmt)
        return {goal_id: Decimal(total) for goal_id, total in result.all()}

    async def insert_if_absent(
        self,
        contribution: GoalContribution,
    ) -> tuple[GoalContribution, bool]:
        stmt = (
            pg_insert(GoalContributionModel)
            .values(
                id=contribution.id,
                goal_id=contribution.goal_id,
                kind=contribution.kind.value,
                amount=contribution.amount,
                currency=contribution.currency.value,
                contributed_on=contribution.contributed_on,
                note=contribution.note,
                bcv_rate_value=contribution.bcv_rate_value,
                bcv_rate_date=contribution.bcv_rate_date,
                usdt_rate_value=contribution.usdt_rate_value,
                usdt_rate_date=contribution.usdt_rate_date,
                deleted_at=contribution.deleted_at,
                created_at=contribution.created_at,
                updated_at=contribution.updated_at,
            )
            .on_conflict_do_nothing(index_elements=[GoalContributionModel.id])
            .returning(GoalContributionModel.id)
        )
        result = await self._session.execute(stmt)
        inserted_id = result.scalar_one_or_none()
        await self._session.commit()
        if inserted_id is not None:
            return contribution, True

        existing = await self.get_by_id(contribution.id)
        assert existing is not None
        return existing, False

    async def update(self, contribution: GoalContribution) -> GoalContribution:
        model = await self._session.get(GoalContributionModel, contribution.id)
        if model is None:
            raise ContributionNotFound(f"Contribution {contribution.id} was not found.")
        model.goal_id = contribution.goal_id
        model.kind = contribution.kind.value
        model.amount = contribution.amount
        model.currency = contribution.currency.value
        model.contributed_on = contribution.contributed_on
        model.note = contribution.note
        model.bcv_rate_value = contribution.bcv_rate_value
        model.bcv_rate_date = contribution.bcv_rate_date
        model.usdt_rate_value = contribution.usdt_rate_value
        model.usdt_rate_date = contribution.usdt_rate_date
        model.deleted_at = contribution.deleted_at
        model.updated_at = contribution.updated_at
        await self._session.commit()
        return _contribution_to_domain(model)
