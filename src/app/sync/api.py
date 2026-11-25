from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.dependencies import get_current_user
from app.clock import Clock
from app.expenses.api import get_expense_service
from app.expenses.service import ExpenseService
from app.goals.api import get_goals_service
from app.goals.service import GoalsService
from app.income.api import get_income_service
from app.income.service import IncomeService
from app.products.api import get_products_service
from app.products.service import ProductsService
from app.rates.api import get_rates_service
from app.shopping.api import get_shopping_service
from app.shopping.service import ShoppingService
from app.sync.domain import (
    SyncCollaborators,
    SyncExpense,
    SyncExpenseCategory,
    SyncGoal,
    SyncGoalContribution,
    SyncIncomeOccurrence,
    SyncIncomeRule,
    SyncProduct,
    SyncProductPrice,
    SyncShoppingList,
    SyncShoppingListItem,
)
from app.sync.repository import SqlAlchemySyncTombstoneRepository
from app.sync.schemas import SyncChangesOut, SyncChangesQuery
from app.sync.service import SyncService
from app.users.domain import User

router = APIRouter(prefix="/sync", tags=["sync"])


class _IncomeSyncAdapter:
    def __init__(self, service: IncomeService) -> None:
        self._service = service

    async def list_rules_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[SyncIncomeRule]:
        rows = await self._service.list_rules_changed_between(since, until)
        return [
            SyncIncomeRule(
                id=r.id,
                name=r.name,
                amount=r.amount,
                currency=r.currency.value,
                days=r.days,
                start_date=r.start_date,
                end_date=r.end_date,
                ended_at=r.ended_at,
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
            for r in rows
        ]

    async def list_occurrences_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[SyncIncomeOccurrence]:
        rows = await self._service.list_occurrences_changed_between(since, until)
        return [
            SyncIncomeOccurrence(
                id=r.id,
                rule_id=r.rule_id,
                expected_date=r.expected_date,
                expected_amount=r.expected_amount,
                currency=r.currency.value,
                status=r.status.value,
                received_date=r.received_date,
                received_amount=r.received_amount,
                rate_value=r.rate_value,
                rate_date=r.rate_date,
                received_at=r.received_at,
                updated_at=r.updated_at,
            )
            for r in rows
        ]


class _ExpensesSyncAdapter:
    def __init__(self, service: ExpenseService) -> None:
        self._service = service

    async def list_categories_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[SyncExpenseCategory]:
        rows = await self._service.list_categories_changed_between(since, until)
        return [
            SyncExpenseCategory(
                id=r.id,
                name=r.name,
                archived_at=r.archived_at,
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
            for r in rows
        ]

    async def list_expenses_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[SyncExpense]:
        rows = await self._service.list_expenses_changed_between(since, until)
        return [
            SyncExpense(
                id=r.id,
                category_id=r.category_id,
                amount=r.amount,
                currency=r.currency.value,
                spent_on=r.spent_on,
                note=r.note,
                bcv_rate_value=r.bcv_rate_value,
                bcv_rate_date=r.bcv_rate_date,
                usdt_rate_value=r.usdt_rate_value,
                usdt_rate_date=r.usdt_rate_date,
                deleted_at=r.deleted_at,
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
            for r in rows
        ]


class _ProductsSyncAdapter:
    def __init__(self, service: ProductsService) -> None:
        self._service = service

    async def list_products_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[SyncProduct]:
        rows = await self._service.list_products_changed_between(since, until)
        return [
            SyncProduct(
                id=r.id,
                name=r.name,
                unit=r.unit.value,
                last_price=r.last_price,
                last_price_currency=(
                    r.last_price_currency.value if r.last_price_currency is not None else None
                ),
                archived_at=r.archived_at,
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
            for r in rows
        ]

    async def list_prices_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[SyncProductPrice]:
        rows = await self._service.list_prices_changed_between(since, until)
        return [
            SyncProductPrice(
                id=r.id,
                product_id=r.product_id,
                price=r.price,
                currency=r.currency.value,
                recorded_on=r.recorded_on,
                source=r.source.value,
                list_item_id=r.list_item_id,
                bcv_rate_value=r.bcv_rate_value,
                bcv_rate_date=r.bcv_rate_date,
                usdt_rate_value=r.usdt_rate_value,
                usdt_rate_date=r.usdt_rate_date,
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
            for r in rows
        ]


class _ShoppingSyncAdapter:
    def __init__(self, service: ShoppingService) -> None:
        self._service = service

    async def list_lists_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[SyncShoppingList]:
        rows = await self._service.list_lists_changed_between(since, until)
        return [
            SyncShoppingList(
                id=r.id,
                name=r.name,
                status=r.status.value,
                closed_at=r.closed_at,
                expense_id=r.expense_id,
                deleted_at=r.deleted_at,
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
            for r in rows
        ]

    async def list_items_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[SyncShoppingListItem]:
        rows = await self._service.list_items_changed_between(since, until)
        return [
            SyncShoppingListItem(
                id=r.id,
                list_id=r.list_id,
                product_id=r.product_id,
                quantity=r.quantity,
                status=r.status.value,
                bought_price=r.bought_price,
                bought_currency=(
                    r.bought_currency.value if r.bought_currency is not None else None
                ),
                bought_at=r.bought_at,
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
            for r in rows
        ]


class _GoalsSyncAdapter:
    def __init__(self, service: GoalsService) -> None:
        self._service = service

    async def list_goals_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[SyncGoal]:
        rows = await self._service.list_goals_changed_between(since, until)
        return [
            SyncGoal(
                id=r.id,
                name=r.name,
                target_usd=r.target_usd,
                target_date=r.target_date,
                note=r.note,
                archived_at=r.archived_at,
                deleted_at=r.deleted_at,
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
            for r in rows
        ]

    async def list_contributions_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[SyncGoalContribution]:
        rows = await self._service.list_contributions_changed_between(since, until)
        return [
            SyncGoalContribution(
                id=r.id,
                goal_id=r.goal_id,
                kind=r.kind.value,
                amount=r.amount,
                currency=r.currency.value,
                contributed_on=r.contributed_on,
                note=r.note,
                bcv_rate_value=r.bcv_rate_value,
                bcv_rate_date=r.bcv_rate_date,
                usdt_rate_value=r.usdt_rate_value,
                usdt_rate_date=r.usdt_rate_date,
                deleted_at=r.deleted_at,
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
            for r in rows
        ]


async def get_sync_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield an isolated REPEATABLE READ session separate from get_current_user's session."""
    session_factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with session_factory() as session:
        await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        yield session


def get_sync_service(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_sync_session)],
) -> SyncService:
    """Build SyncService sharing one REPEATABLE READ session across all domain adapters."""
    clock: Clock = request.app.state.clock
    rates_service = get_rates_service(request, session)
    income_service = get_income_service(request, session, rates_service)
    expense_service = get_expense_service(request, session, rates_service)
    products_service = get_products_service(request, session, rates_service)
    shopping_service = get_shopping_service(
        request,
        session,
        products_service,
        expense_service,
        rates_service,
    )
    goals_service = get_goals_service(request, session, rates_service)

    return SyncService(
        collaborators=SyncCollaborators(
            income=_IncomeSyncAdapter(income_service),
            expenses=_ExpensesSyncAdapter(expense_service),
            products=_ProductsSyncAdapter(products_service),
            shopping=_ShoppingSyncAdapter(shopping_service),
            goals=_GoalsSyncAdapter(goals_service),
            tombstones=SqlAlchemySyncTombstoneRepository(session),
            clock=clock,
        )
    )


@router.get("/changes", response_model=SyncChangesOut)
async def get_sync_changes(
    query: Annotated[SyncChangesQuery, Query()],
    _current_user: Annotated[User, Depends(get_current_user)],
    sync_service: Annotated[SyncService, Depends(get_sync_service)],
) -> SyncChangesOut:
    """Return a consistent snapshot of all changes and tombstones in (since, until]."""
    result = await sync_service.changes(since=query.since)
    return SyncChangesOut.from_domain(result)
