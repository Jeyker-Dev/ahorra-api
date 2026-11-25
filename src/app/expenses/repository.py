from datetime import date, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.expenses.domain import (
    Currency,
    Expense,
    ExpenseCategory,
    ExpenseWithCategory,
)
from app.expenses.errors import CategoryNameTaken, CategoryNotFound, ExpenseNotFound
from app.expenses.models import ExpenseCategoryModel, ExpenseModel

_CATEGORY_LOWER_NAME_INDEX = "uq_expense_categories_lower_name"


def _category_to_domain(model: ExpenseCategoryModel) -> ExpenseCategory:
    return ExpenseCategory(
        id=model.id,
        name=model.name,
        archived_at=model.archived_at,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _expense_to_domain(model: ExpenseModel) -> Expense:
    return Expense(
        id=model.id,
        category_id=model.category_id,
        amount=model.amount,
        currency=Currency(model.currency),
        spent_on=model.spent_on,
        note=model.note,
        bcv_rate_value=model.bcv_rate_value,
        bcv_rate_date=model.bcv_rate_date,
        usdt_rate_value=model.usdt_rate_value,
        usdt_rate_date=model.usdt_rate_date,
        deleted_at=model.deleted_at,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


class SqlAlchemyExpenseCategoryRepository:
    """SQLAlchemy implementation of ExpenseCategoryRepository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, category_id: UUID) -> ExpenseCategory | None:
        stmt = select(ExpenseCategoryModel).where(ExpenseCategoryModel.id == category_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return _category_to_domain(model) if model is not None else None

    async def get_by_lower_name(self, name: str) -> ExpenseCategory | None:
        stmt = select(ExpenseCategoryModel).where(
            func.lower(ExpenseCategoryModel.name) == name.lower()
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return _category_to_domain(model) if model is not None else None

    async def list_active(self) -> list[ExpenseCategory]:
        stmt = (
            select(ExpenseCategoryModel)
            .where(ExpenseCategoryModel.archived_at.is_(None))
            .order_by(ExpenseCategoryModel.name.asc(), ExpenseCategoryModel.id.asc())
        )
        result = await self._session.execute(stmt)
        return [_category_to_domain(model) for model in result.scalars().all()]

    async def list_all(self) -> list[ExpenseCategory]:
        stmt = select(ExpenseCategoryModel).order_by(
            ExpenseCategoryModel.archived_at.is_not(None).asc(),
            ExpenseCategoryModel.name.asc(),
            ExpenseCategoryModel.id.asc(),
        )
        result = await self._session.execute(stmt)
        return [_category_to_domain(model) for model in result.scalars().all()]

    async def insert_if_absent(
        self,
        category: ExpenseCategory,
    ) -> tuple[ExpenseCategory, bool]:
        stmt = (
            pg_insert(ExpenseCategoryModel)
            .values(
                id=category.id,
                name=category.name,
                archived_at=category.archived_at,
                created_at=category.created_at,
                updated_at=category.updated_at,
            )
            .on_conflict_do_nothing(index_elements=[ExpenseCategoryModel.id])
            .returning(ExpenseCategoryModel.id)
        )
        try:
            result = await self._session.execute(stmt)
            inserted_id = result.scalar_one_or_none()
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            if _CATEGORY_LOWER_NAME_INDEX in str(exc):
                raise CategoryNameTaken(
                    f"Category name {category.name!r} is already taken."
                ) from exc
            raise

        if inserted_id is not None:
            return category, True

        existing = await self.get_by_id(category.id)
        assert existing is not None
        return existing, False

    async def update(self, category: ExpenseCategory) -> ExpenseCategory:
        model = await self._session.get(ExpenseCategoryModel, category.id)
        if model is None:
            raise CategoryNotFound(f"Expense category {category.id} was not found.")
        model.name = category.name
        model.archived_at = category.archived_at
        model.updated_at = category.updated_at
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            if _CATEGORY_LOWER_NAME_INDEX in str(exc):
                raise CategoryNameTaken(
                    f"Category name {category.name!r} is already taken."
                ) from exc
            raise
        return _category_to_domain(model)

    async def list_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[ExpenseCategory]:
        stmt = select(ExpenseCategoryModel).where(ExpenseCategoryModel.updated_at <= until)
        if since is not None:
            stmt = stmt.where(ExpenseCategoryModel.updated_at > since)
        stmt = stmt.order_by(ExpenseCategoryModel.updated_at.asc(), ExpenseCategoryModel.id.asc())
        result = await self._session.execute(stmt)
        return [_category_to_domain(model) for model in result.scalars().all()]


class SqlAlchemyExpenseRepository:
    """SQLAlchemy implementation of ExpenseRepository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, expense_id: UUID) -> Expense | None:
        stmt = select(ExpenseModel).where(ExpenseModel.id == expense_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return _expense_to_domain(model) if model is not None else None

    async def insert_if_absent(self, expense: Expense) -> tuple[Expense, bool]:
        stmt = (
            pg_insert(ExpenseModel)
            .values(
                id=expense.id,
                category_id=expense.category_id,
                amount=expense.amount,
                currency=expense.currency.value,
                spent_on=expense.spent_on,
                note=expense.note,
                bcv_rate_value=expense.bcv_rate_value,
                bcv_rate_date=expense.bcv_rate_date,
                usdt_rate_value=expense.usdt_rate_value,
                usdt_rate_date=expense.usdt_rate_date,
                deleted_at=expense.deleted_at,
                created_at=expense.created_at,
                updated_at=expense.updated_at,
            )
            .on_conflict_do_nothing(index_elements=[ExpenseModel.id])
            .returning(ExpenseModel.id)
        )
        result = await self._session.execute(stmt)
        inserted_id = result.scalar_one_or_none()
        await self._session.commit()
        if inserted_id is not None:
            return expense, True

        existing = await self.get_by_id(expense.id)
        assert existing is not None
        return existing, False

    async def update(self, expense: Expense) -> Expense:
        model = await self._session.get(ExpenseModel, expense.id)
        if model is None:
            raise ExpenseNotFound(f"Expense {expense.id} was not found.")
        model.category_id = expense.category_id
        model.amount = expense.amount
        model.currency = expense.currency.value
        model.spent_on = expense.spent_on
        model.note = expense.note
        model.bcv_rate_value = expense.bcv_rate_value
        model.bcv_rate_date = expense.bcv_rate_date
        model.usdt_rate_value = expense.usdt_rate_value
        model.usdt_rate_date = expense.usdt_rate_date
        model.deleted_at = expense.deleted_at
        model.updated_at = expense.updated_at
        await self._session.commit()
        return _expense_to_domain(model)

    async def list_in_range(
        self,
        from_date: date,
        to_date: date,
        category_id: UUID | None = None,
    ) -> list[ExpenseWithCategory]:
        stmt = (
            select(ExpenseModel, ExpenseCategoryModel.name)
            .join(ExpenseCategoryModel, ExpenseModel.category_id == ExpenseCategoryModel.id)
            .where(
                ExpenseModel.deleted_at.is_(None),
                ExpenseModel.spent_on >= from_date,
                ExpenseModel.spent_on <= to_date,
            )
        )
        if category_id is not None:
            stmt = stmt.where(ExpenseModel.category_id == category_id)
        stmt = stmt.order_by(
            ExpenseModel.spent_on.desc(),
            ExpenseModel.created_at.desc(),
            ExpenseModel.id.asc(),
        )
        result = await self._session.execute(stmt)
        return [
            ExpenseWithCategory(
                expense=_expense_to_domain(exp_model),
                category_name=cat_name,
            )
            for exp_model, cat_name in result.all()
        ]

    async def list_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[Expense]:
        stmt = select(ExpenseModel).where(ExpenseModel.updated_at <= until)
        if since is not None:
            stmt = stmt.where(ExpenseModel.updated_at > since)
        stmt = stmt.order_by(ExpenseModel.updated_at.asc(), ExpenseModel.id.asc())
        result = await self._session.execute(stmt)
        return [_expense_to_domain(model) for model in result.scalars().all()]
