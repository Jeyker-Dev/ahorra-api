from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.clock import Clock
from app.db import get_session
from app.expenses.domain import ExpenseCollaborators, ExpenseInput, RateSnapshot
from app.expenses.repository import (
    SqlAlchemyExpenseCategoryRepository,
    SqlAlchemyExpenseRepository,
)
from app.expenses.schemas import (
    CategoryListQuery,
    CategoryOut,
    CategoryUpsertIn,
    ExpenseListQuery,
    ExpenseOut,
    ExpenseSummaryOut,
    ExpenseSummaryQuery,
    ExpenseUpsertIn,
)
from app.expenses.service import ExpenseService
from app.rates.api import get_rates_service
from app.rates.service import RatesService
from app.users.domain import User

router = APIRouter(prefix="/expenses", tags=["expenses"])


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


def get_expense_service(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    rates_service: Annotated[RatesService, Depends(get_rates_service)],
) -> ExpenseService:
    """Build ExpenseService with request-scoped repositories and app singletons."""
    clock: Clock = request.app.state.clock
    return ExpenseService(
        collaborators=ExpenseCollaborators(
            categories=SqlAlchemyExpenseCategoryRepository(session),
            expenses=SqlAlchemyExpenseRepository(session),
            rates=_RatesServiceLookupAdapter(rates_service),
            clock=clock,
        )
    )


@router.put("/categories/{category_id}", response_model=CategoryOut)
async def upsert_expense_category(
    category_id: UUID,
    body: CategoryUpsertIn,
    response: Response,
    _current_user: Annotated[User, Depends(get_current_user)],
    expense_service: Annotated[ExpenseService, Depends(get_expense_service)],
) -> CategoryOut:
    """Create or idempotently rename an expense category."""
    result = await expense_service.upsert_category(category_id, body.name)
    response.status_code = status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
    return CategoryOut.from_domain(result.category)


@router.get("/categories", response_model=list[CategoryOut])
async def list_expense_categories(
    query: Annotated[CategoryListQuery, Query()],
    _current_user: Annotated[User, Depends(get_current_user)],
    expense_service: Annotated[ExpenseService, Depends(get_expense_service)],
) -> list[CategoryOut]:
    """List expense categories, active only by default or all with active first."""
    categories = (
        await expense_service.list_all_categories()
        if query.include_archived
        else await expense_service.list_active_categories()
    )
    return [CategoryOut.from_domain(cat) for cat in categories]


@router.post("/categories/{category_id}/archive", response_model=CategoryOut)
async def archive_expense_category(
    category_id: UUID,
    _current_user: Annotated[User, Depends(get_current_user)],
    expense_service: Annotated[ExpenseService, Depends(get_expense_service)],
) -> CategoryOut:
    """Archive an expense category idempotently."""
    category = await expense_service.archive_category(category_id)
    return CategoryOut.from_domain(category)


@router.post("/categories/{category_id}/unarchive", response_model=CategoryOut)
async def unarchive_expense_category(
    category_id: UUID,
    _current_user: Annotated[User, Depends(get_current_user)],
    expense_service: Annotated[ExpenseService, Depends(get_expense_service)],
) -> CategoryOut:
    """Unarchive an expense category idempotently."""
    category = await expense_service.unarchive_category(category_id)
    return CategoryOut.from_domain(category)


@router.get("/summary", response_model=list[ExpenseSummaryOut])
async def summarize_expenses(
    query: Annotated[ExpenseSummaryQuery, Query()],
    _current_user: Annotated[User, Depends(get_current_user)],
    expense_service: Annotated[ExpenseService, Depends(get_expense_service)],
) -> list[ExpenseSummaryOut]:
    """Summarize non-deleted expenses in USD per category over a date range."""
    items = await expense_service.summarize(query.from_date, query.to_date)
    return [ExpenseSummaryOut.from_domain(item) for item in items]


@router.get("", response_model=list[ExpenseOut])
async def list_expenses(
    query: Annotated[ExpenseListQuery, Query()],
    _current_user: Annotated[User, Depends(get_current_user)],
    expense_service: Annotated[ExpenseService, Depends(get_expense_service)],
) -> list[ExpenseOut]:
    """List non-deleted expenses within an inclusive spent_on range."""
    items = await expense_service.list_expenses(
        from_date=query.from_date,
        to_date=query.to_date,
        category_id=query.category_id,
    )
    return [ExpenseOut.from_with_category(item) for item in items]


@router.put("/{expense_id}", response_model=ExpenseOut)
async def upsert_expense(
    expense_id: UUID,
    body: ExpenseUpsertIn,
    response: Response,
    _current_user: Annotated[User, Depends(get_current_user)],
    expense_service: Annotated[ExpenseService, Depends(get_expense_service)],
) -> ExpenseOut:
    """Create or idempotently update an expense."""
    result = await expense_service.upsert_expense(
        ExpenseInput(
            id=expense_id,
            category_id=body.category_id,
            amount=body.amount,
            currency=body.currency,
            spent_on=body.spent_on,
            note=body.note,
        )
    )
    response.status_code = status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
    return ExpenseOut.from_with_category(result.item)


@router.get("/{expense_id}", response_model=ExpenseOut)
async def get_expense(
    expense_id: UUID,
    _current_user: Annotated[User, Depends(get_current_user)],
    expense_service: Annotated[ExpenseService, Depends(get_expense_service)],
) -> ExpenseOut:
    """Return a single non-deleted expense by ID."""
    item = await expense_service.get_expense(expense_id)
    return ExpenseOut.from_with_category(item)


@router.delete("/{expense_id}", response_model=ExpenseOut)
async def delete_expense(
    expense_id: UUID,
    _current_user: Annotated[User, Depends(get_current_user)],
    expense_service: Annotated[ExpenseService, Depends(get_expense_service)],
) -> ExpenseOut:
    """Soft-delete an expense idempotently."""
    item = await expense_service.delete_expense(expense_id)
    return ExpenseOut.from_with_category(item)
