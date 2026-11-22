from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.clock import Clock
from app.db import get_session
from app.expenses.api import get_expense_service
from app.expenses.domain import Currency as ExpenseCurrency
from app.expenses.domain import ExpenseInput
from app.expenses.service import ExpenseService
from app.products.api import get_products_service
from app.products.domain import Currency as ProductCurrency
from app.products.domain import Product, PurchasePriceInput
from app.products.domain import RateSnapshot as ProductRateSnapshot
from app.products.errors import ProductNotFound as CatalogProductNotFound
from app.products.service import ProductsService
from app.rates.api import get_rates_service
from app.rates.service import RatesService
from app.shopping.domain import (
    BuyItemInput,
    CloseListInput,
    Currency,
    ProductSummary,
    PurchaseHistoryRef,
    RateSnapshot,
    RecordExpenseInput,
    RecordPurchaseInput,
    ShoppingCollaborators,
    Unit,
    UpsertItemInput,
)
from app.shopping.repository import (
    SqlAlchemyShoppingListItemRepository,
    SqlAlchemyShoppingListRepository,
)
from app.shopping.schemas import (
    BuyItemIn,
    CloseListIn,
    ListItemUpsertIn,
    ListTotalsOut,
    RecommendationsOut,
    RecommendationsQuery,
    ShoppingListDetailOut,
    ShoppingListItemOut,
    ShoppingListOut,
    ShoppingListQuery,
    ShoppingListUpsertIn,
)
from app.shopping.service import ShoppingService
from app.users.domain import User

router = APIRouter(prefix="/shopping", tags=["shopping"])


def _to_product_summary(product: Product) -> ProductSummary:
    return ProductSummary(
        id=product.id,
        name=product.name,
        unit=Unit(product.unit.value),
        last_price=product.last_price,
        last_price_currency=(
            Currency(product.last_price_currency.value)
            if product.last_price_currency is not None
            else None
        ),
        archived_at=product.archived_at,
    )


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


class _ProductsServiceCatalogAdapter:
    """Adapter exposing ProductsService operations through ProductCatalog."""

    def __init__(self, products_service: ProductsService) -> None:
        self._products_service = products_service

    async def get_product(self, product_id: UUID) -> ProductSummary | None:
        try:
            product = await self._products_service.get_product(product_id)
        except CatalogProductNotFound:
            return None
        return _to_product_summary(product)

    async def list_all_products(self) -> list[ProductSummary]:
        products = await self._products_service.list_all_products()
        return [_to_product_summary(product) for product in products]

    async def record_purchase_price(self, payload: RecordPurchaseInput) -> ProductSummary:
        updated = await self._products_service.record_purchase_price(
            PurchasePriceInput(
                product_id=payload.product_id,
                list_item_id=payload.list_item_id,
                price=payload.price,
                currency=ProductCurrency(payload.currency.value),
                recorded_on=payload.recorded_on,
                bcv_rate=ProductRateSnapshot(
                    value=payload.bcv_rate.value,
                    value_date=payload.bcv_rate.value_date,
                ),
                usdt_rate=ProductRateSnapshot(
                    value=payload.usdt_rate.value,
                    value_date=payload.usdt_rate.value_date,
                ),
            )
        )
        return _to_product_summary(updated)

    async def delete_purchase_price_and_restore(
        self,
        product_id: UUID,
        list_item_id: UUID,
    ) -> ProductSummary:
        updated = await self._products_service.delete_purchase_price_and_restore(
            product_id=product_id,
            list_item_id=list_item_id,
        )
        return _to_product_summary(updated)

    async def list_purchase_refs_for_active_products(self) -> list[PurchaseHistoryRef]:
        refs = await self._products_service.list_purchase_refs_for_active_products()
        return [
            PurchaseHistoryRef(
                product_id=ref.product_id,
                recorded_on=ref.recorded_on,
                list_item_id=ref.list_item_id,
            )
            for ref in refs
        ]


class _ExpenseServiceRecorderAdapter:
    """Adapter exposing ExpenseService.upsert_expense through ExpenseRecorder."""

    def __init__(self, expense_service: ExpenseService) -> None:
        self._expense_service = expense_service

    async def upsert_expense(self, payload: RecordExpenseInput) -> UUID:
        result = await self._expense_service.upsert_expense(
            ExpenseInput(
                id=payload.expense_id,
                category_id=payload.category_id,
                amount=payload.amount,
                currency=ExpenseCurrency(payload.currency.value),
                spent_on=payload.spent_on,
                note=payload.note,
            )
        )
        return result.item.expense.id


def get_shopping_service(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    products_service: Annotated[ProductsService, Depends(get_products_service)],
    expense_service: Annotated[ExpenseService, Depends(get_expense_service)],
    rates_service: Annotated[RatesService, Depends(get_rates_service)],
) -> ShoppingService:
    """Build ShoppingService with request-scoped repositories and cross-domain service adapters."""
    clock: Clock = request.app.state.clock
    return ShoppingService(
        collaborators=ShoppingCollaborators(
            lists=SqlAlchemyShoppingListRepository(session),
            items=SqlAlchemyShoppingListItemRepository(session),
            products=_ProductsServiceCatalogAdapter(products_service),
            expenses=_ExpenseServiceRecorderAdapter(expense_service),
            rates=_RatesServiceLookupAdapter(rates_service),
            clock=clock,
        )
    )


@router.get("/recommendations", response_model=RecommendationsOut)
async def get_recommendations(
    query: Annotated[RecommendationsQuery, Query()],
    _current_user: Annotated[User, Depends(get_current_user)],
    shopping_service: Annotated[ShoppingService, Depends(get_shopping_service)],
) -> RecommendationsOut:
    """Return recommended products due for repurchase and their estimated totals."""
    result = await shopping_service.recommendations(days_ahead=query.days_ahead)
    return RecommendationsOut.from_domain(result)


@router.get("/lists", response_model=list[ShoppingListOut])
async def list_shopping_lists(
    query: Annotated[ShoppingListQuery, Query()],
    _current_user: Annotated[User, Depends(get_current_user)],
    shopping_service: Annotated[ShoppingService, Depends(get_shopping_service)],
) -> list[ShoppingListOut]:
    """List non-deleted shopping lists optionally filtered by status."""
    lists = await shopping_service.list_lists(status=query.status)
    return [ShoppingListOut.from_domain(lst) for lst in lists]


@router.put("/lists/{list_id}", response_model=ShoppingListOut)
async def upsert_shopping_list(
    list_id: UUID,
    body: ShoppingListUpsertIn,
    response: Response,
    _current_user: Annotated[User, Depends(get_current_user)],
    shopping_service: Annotated[ShoppingService, Depends(get_shopping_service)],
) -> ShoppingListOut:
    """Create or idempotently rename an open shopping list."""
    result = await shopping_service.upsert_list(list_id, body.name)
    response.status_code = status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
    return ShoppingListOut.from_domain(result.shopping_list)


@router.get("/lists/{list_id}", response_model=ShoppingListDetailOut)
async def get_shopping_list(
    list_id: UUID,
    _current_user: Annotated[User, Depends(get_current_user)],
    shopping_service: Annotated[ShoppingService, Depends(get_shopping_service)],
) -> ShoppingListDetailOut:
    """Return a non-deleted shopping list with its items."""
    detail = await shopping_service.get_list(list_id)
    return ShoppingListDetailOut.from_domain(detail)


@router.delete("/lists/{list_id}", response_model=ShoppingListOut)
async def delete_shopping_list(
    list_id: UUID,
    _current_user: Annotated[User, Depends(get_current_user)],
    shopping_service: Annotated[ShoppingService, Depends(get_shopping_service)],
) -> ShoppingListOut:
    """Soft-delete a shopping list idempotently."""
    shopping_list = await shopping_service.delete_list(list_id)
    return ShoppingListOut.from_domain(shopping_list)


@router.get("/lists/{list_id}/totals", response_model=ListTotalsOut)
async def get_shopping_list_totals(
    list_id: UUID,
    _current_user: Annotated[User, Depends(get_current_user)],
    shopping_service: Annotated[ShoppingService, Depends(get_shopping_service)],
) -> ListTotalsOut:
    """Return estimated and bought totals for a shopping list using today's rates."""
    totals = await shopping_service.list_totals(list_id)
    return ListTotalsOut.from_domain(totals)


@router.post("/lists/{list_id}/close", response_model=ShoppingListOut)
async def close_shopping_list(
    list_id: UUID,
    body: CloseListIn,
    _current_user: Annotated[User, Depends(get_current_user)],
    shopping_service: Annotated[ShoppingService, Depends(get_shopping_service)],
) -> ShoppingListOut:
    """Close an open shopping list and record an expense for its bought items."""
    shopping_list = await shopping_service.close_list(
        CloseListInput(
            list_id=list_id,
            category_id=body.category_id,
            spent_on=body.spent_on,
        )
    )
    return ShoppingListOut.from_domain(shopping_list)


@router.put("/lists/{list_id}/items/{item_id}", response_model=ShoppingListItemOut)
async def upsert_shopping_list_item(
    list_id: UUID,
    item_id: UUID,
    body: ListItemUpsertIn,
    response: Response,
    _current_user: Annotated[User, Depends(get_current_user)],
    shopping_service: Annotated[ShoppingService, Depends(get_shopping_service)],
) -> ShoppingListItemOut:
    """Add or update an item on an open shopping list."""
    result = await shopping_service.upsert_item(
        UpsertItemInput(
            list_id=list_id,
            item_id=item_id,
            product_id=body.product_id,
            quantity=body.quantity,
        )
    )
    response.status_code = status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
    return ShoppingListItemOut.from_detail(result.item)


@router.delete("/lists/{list_id}/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_shopping_list_item(
    list_id: UUID,
    item_id: UUID,
    _current_user: Annotated[User, Depends(get_current_user)],
    shopping_service: Annotated[ShoppingService, Depends(get_shopping_service)],
) -> Response:
    """Hard-delete a pending item from an open shopping list."""
    await shopping_service.delete_item(list_id, item_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/lists/{list_id}/items/{item_id}/buy", response_model=ShoppingListItemOut)
async def buy_shopping_list_item(
    list_id: UUID,
    item_id: UUID,
    _current_user: Annotated[User, Depends(get_current_user)],
    shopping_service: Annotated[ShoppingService, Depends(get_shopping_service)],
    body: Annotated[BuyItemIn | None, Body()] = None,
) -> ShoppingListItemOut:
    """Mark an item bought (or correct a bought item) and record its purchase price."""
    payload = body if body is not None else BuyItemIn()
    item = await shopping_service.buy_item(
        BuyItemInput(
            list_id=list_id,
            item_id=item_id,
            price=payload.price,
            currency=payload.currency,
        )
    )
    return ShoppingListItemOut.from_detail(item)


@router.post("/lists/{list_id}/items/{item_id}/unbuy", response_model=ShoppingListItemOut)
async def unbuy_shopping_list_item(
    list_id: UUID,
    item_id: UUID,
    _current_user: Annotated[User, Depends(get_current_user)],
    shopping_service: Annotated[ShoppingService, Depends(get_shopping_service)],
) -> ShoppingListItemOut:
    """Revert a bought item back to pending and restore the product's last price."""
    item = await shopping_service.unbuy_item(list_id, item_id)
    return ShoppingListItemOut.from_detail(item)
