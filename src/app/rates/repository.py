from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.rates.domain import ExchangeRate, RateKind, RateOrigin, RateStatus
from app.rates.errors import RateNotFound
from app.rates.models import ExchangeRateModel


def _to_domain(model: ExchangeRateModel) -> ExchangeRate:
    return ExchangeRate(
        id=model.id,
        kind=RateKind(model.kind),
        value=model.value,
        value_date=model.value_date,
        origin=RateOrigin(model.origin),
        status=RateStatus(model.status),
        fetched_at=model.fetched_at,
    )


def _to_model(rate: ExchangeRate) -> ExchangeRateModel:
    return ExchangeRateModel(
        id=rate.id,
        kind=rate.kind.value,
        value=rate.value,
        value_date=rate.value_date,
        origin=rate.origin.value,
        status=rate.status.value,
        fetched_at=rate.fetched_at,
    )


class SqlAlchemyExchangeRateRepository:
    """SQLAlchemy implementation of ExchangeRateRepository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, rate: ExchangeRate) -> ExchangeRate:
        model = _to_model(rate)
        self._session.add(model)
        await self._session.commit()
        return _to_domain(model)

    async def get_by_id(self, rate_id: UUID) -> ExchangeRate | None:
        stmt = select(ExchangeRateModel).where(ExchangeRateModel.id == rate_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return _to_domain(model) if model is not None else None

    async def get_bcv_for_date(self, value_date: date) -> ExchangeRate | None:
        stmt = select(ExchangeRateModel).where(
            ExchangeRateModel.kind == RateKind.bcv.value,
            ExchangeRateModel.value_date == value_date,
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return _to_domain(model) if model is not None else None

    async def _latest_in_force(self, kind: RateKind, on: date) -> ExchangeRate | None:
        stmt = (
            select(ExchangeRateModel)
            .where(
                ExchangeRateModel.kind == kind.value,
                ExchangeRateModel.value_date <= on,
            )
            .order_by(ExchangeRateModel.value_date.desc(), ExchangeRateModel.fetched_at.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return _to_domain(model) if model is not None else None

    async def latest_bcv_in_force(self, today: date) -> ExchangeRate | None:
        return await self._latest_in_force(RateKind.bcv, today)

    async def latest_usdt_in_force(self, on: date) -> ExchangeRate | None:
        return await self._latest_in_force(RateKind.usdt, on)

    async def latest(self, kind: RateKind) -> ExchangeRate | None:
        stmt = (
            select(ExchangeRateModel)
            .where(ExchangeRateModel.kind == kind.value)
            .order_by(ExchangeRateModel.fetched_at.desc(), ExchangeRateModel.value_date.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return _to_domain(model) if model is not None else None

    async def history(
        self,
        kind: RateKind,
        from_date: date,
        to_date: date,
    ) -> list[ExchangeRate]:
        stmt = (
            select(ExchangeRateModel)
            .where(
                ExchangeRateModel.kind == kind.value,
                ExchangeRateModel.value_date >= from_date,
                ExchangeRateModel.value_date <= to_date,
            )
            .order_by(ExchangeRateModel.value_date.asc(), ExchangeRateModel.fetched_at.asc())
        )
        result = await self._session.execute(stmt)
        return [_to_domain(model) for model in result.scalars().all()]

    async def list_in_review(self) -> list[ExchangeRate]:
        stmt = (
            select(ExchangeRateModel)
            .where(ExchangeRateModel.status == RateStatus.in_review.value)
            .order_by(ExchangeRateModel.value_date.desc(), ExchangeRateModel.fetched_at.desc())
        )
        result = await self._session.execute(stmt)
        return [_to_domain(model) for model in result.scalars().all()]

    async def update(self, rate: ExchangeRate) -> ExchangeRate:
        model = await self._session.get(ExchangeRateModel, rate.id)
        if model is None:
            raise RateNotFound(f"Exchange rate {rate.id} not found.")
        model.kind = rate.kind.value
        model.value = rate.value
        model.value_date = rate.value_date
        model.origin = rate.origin.value
        model.status = rate.status.value
        model.fetched_at = rate.fetched_at
        await self._session.commit()
        return _to_domain(model)
