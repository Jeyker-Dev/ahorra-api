from collections.abc import Sequence
from datetime import date
from uuid import UUID

from sqlalchemy import delete, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.income.domain import (
    Currency,
    IncomeOccurrence,
    IncomeRule,
    OccurrenceStatus,
    OccurrenceWithRule,
)
from app.income.errors import IncomeRuleNotFound, OccurrenceNotFound
from app.income.models import IncomeOccurrenceModel, IncomeRuleModel


def _rule_to_domain(model: IncomeRuleModel) -> IncomeRule:
    return IncomeRule(
        id=model.id,
        name=model.name,
        amount=model.amount,
        currency=Currency(model.currency),
        days=tuple(int(day) for day in model.days),
        start_date=model.start_date,
        end_date=model.end_date,
        ended_at=model.ended_at,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _occurrence_to_domain(model: IncomeOccurrenceModel) -> IncomeOccurrence:
    return IncomeOccurrence(
        id=model.id,
        rule_id=model.rule_id,
        expected_date=model.expected_date,
        expected_amount=model.expected_amount,
        currency=Currency(model.currency),
        status=OccurrenceStatus(model.status),
        received_date=model.received_date,
        received_amount=model.received_amount,
        rate_value=model.rate_value,
        rate_date=model.rate_date,
        received_at=model.received_at,
        updated_at=model.updated_at,
    )


class SqlAlchemyIncomeRuleRepository:
    """SQLAlchemy implementation of IncomeRuleRepository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, rule_id: UUID) -> IncomeRule | None:
        stmt = select(IncomeRuleModel).where(IncomeRuleModel.id == rule_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return _rule_to_domain(model) if model is not None else None

    async def list_all(self) -> list[IncomeRule]:
        stmt = select(IncomeRuleModel).order_by(
            IncomeRuleModel.ended_at.is_not(None).asc(),
            IncomeRuleModel.name.asc(),
            IncomeRuleModel.id.asc(),
        )
        result = await self._session.execute(stmt)
        return [_rule_to_domain(model) for model in result.scalars().all()]

    async def list_active(self, today: date) -> list[IncomeRule]:
        stmt = (
            select(IncomeRuleModel)
            .where(
                IncomeRuleModel.ended_at.is_(None),
                or_(
                    IncomeRuleModel.end_date.is_(None),
                    IncomeRuleModel.end_date >= today,
                ),
            )
            .order_by(IncomeRuleModel.name.asc(), IncomeRuleModel.id.asc())
        )
        result = await self._session.execute(stmt)
        return [_rule_to_domain(model) for model in result.scalars().all()]

    async def insert_if_absent(self, rule: IncomeRule) -> tuple[IncomeRule, bool]:
        stmt = (
            pg_insert(IncomeRuleModel)
            .values(
                id=rule.id,
                name=rule.name,
                amount=rule.amount,
                currency=rule.currency.value,
                days=list(rule.days),
                start_date=rule.start_date,
                end_date=rule.end_date,
                ended_at=rule.ended_at,
                created_at=rule.created_at,
                updated_at=rule.updated_at,
            )
            .on_conflict_do_nothing(index_elements=[IncomeRuleModel.id])
            .returning(IncomeRuleModel.id)
        )
        result = await self._session.execute(stmt)
        inserted_id = result.scalar_one_or_none()
        await self._session.commit()
        if inserted_id is not None:
            return rule, True

        existing = await self.get_by_id(rule.id)
        assert existing is not None
        return existing, False

    async def update(self, rule: IncomeRule) -> IncomeRule:
        model = await self._session.get(IncomeRuleModel, rule.id)
        if model is None:
            raise IncomeRuleNotFound(f"Income rule {rule.id} was not found.")
        model.name = rule.name
        model.amount = rule.amount
        model.currency = rule.currency.value
        model.days = list(rule.days)
        model.start_date = rule.start_date
        model.end_date = rule.end_date
        model.ended_at = rule.ended_at
        model.updated_at = rule.updated_at
        await self._session.commit()
        return _rule_to_domain(model)


class SqlAlchemyIncomeOccurrenceRepository:
    """SQLAlchemy implementation of IncomeOccurrenceRepository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, occurrence_id: UUID) -> IncomeOccurrence | None:
        stmt = select(IncomeOccurrenceModel).where(IncomeOccurrenceModel.id == occurrence_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return _occurrence_to_domain(model) if model is not None else None

    async def list_dates_for_rule(self, rule_id: UUID) -> set[date]:
        stmt = select(IncomeOccurrenceModel.expected_date).where(
            IncomeOccurrenceModel.rule_id == rule_id
        )
        result = await self._session.execute(stmt)
        return set(result.scalars().all())

    async def add_many_ignore_conflicts(
        self,
        occurrences: Sequence[IncomeOccurrence],
    ) -> int:
        if not occurrences:
            return 0
        rows = [
            {
                "id": occ.id,
                "rule_id": occ.rule_id,
                "expected_date": occ.expected_date,
                "expected_amount": occ.expected_amount,
                "currency": occ.currency.value,
                "status": occ.status.value,
                "received_date": occ.received_date,
                "received_amount": occ.received_amount,
                "rate_value": occ.rate_value,
                "rate_date": occ.rate_date,
                "received_at": occ.received_at,
                "updated_at": occ.updated_at,
            }
            for occ in occurrences
        ]
        stmt = (
            pg_insert(IncomeOccurrenceModel)
            .values(rows)
            .on_conflict_do_nothing(
                index_elements=[
                    IncomeOccurrenceModel.rule_id,
                    IncomeOccurrenceModel.expected_date,
                ]
            )
            .returning(IncomeOccurrenceModel.id)
        )
        result = await self._session.execute(stmt)
        inserted_ids = result.scalars().all()
        await self._session.commit()
        return len(inserted_ids)

    async def delete_future_expected(self, rule_id: UUID, from_date: date) -> None:
        stmt = delete(IncomeOccurrenceModel).where(
            IncomeOccurrenceModel.rule_id == rule_id,
            IncomeOccurrenceModel.status == OccurrenceStatus.expected.value,
            IncomeOccurrenceModel.expected_date >= from_date,
        )
        await self._session.execute(stmt)
        await self._session.commit()

    async def update(self, occurrence: IncomeOccurrence) -> IncomeOccurrence:
        model = await self._session.get(IncomeOccurrenceModel, occurrence.id)
        if model is None:
            raise OccurrenceNotFound(f"Income occurrence {occurrence.id} was not found.")
        model.expected_date = occurrence.expected_date
        model.expected_amount = occurrence.expected_amount
        model.currency = occurrence.currency.value
        model.status = occurrence.status.value
        model.received_date = occurrence.received_date
        model.received_amount = occurrence.received_amount
        model.rate_value = occurrence.rate_value
        model.rate_date = occurrence.rate_date
        model.received_at = occurrence.received_at
        model.updated_at = occurrence.updated_at
        await self._session.commit()
        return _occurrence_to_domain(model)

    async def list_in_range(
        self,
        from_date: date,
        to_date: date,
        status: OccurrenceStatus | None = None,
    ) -> list[OccurrenceWithRule]:
        stmt = (
            select(IncomeOccurrenceModel, IncomeRuleModel.name)
            .join(IncomeRuleModel, IncomeOccurrenceModel.rule_id == IncomeRuleModel.id)
            .where(
                IncomeOccurrenceModel.expected_date >= from_date,
                IncomeOccurrenceModel.expected_date <= to_date,
            )
        )
        if status is not None:
            stmt = stmt.where(IncomeOccurrenceModel.status == status.value)
        stmt = stmt.order_by(
            IncomeOccurrenceModel.expected_date.asc(),
            IncomeRuleModel.name.asc(),
            IncomeOccurrenceModel.id.asc(),
        )
        result = await self._session.execute(stmt)
        return [
            OccurrenceWithRule(
                occurrence=_occurrence_to_domain(occ_model),
                rule_name=rule_name,
            )
            for occ_model, rule_name in result.all()
        ]
