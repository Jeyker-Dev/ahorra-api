from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.clock import Clock
from app.db import get_session
from app.products.domain import ProductCollaborators, ProductInput, RateSnapshot
from app.products.repository import SqlAlchemyProductPriceRepository, SqlAlchemyProductRepository
from app.products.schemas import (
    PriceEntryOut,
    ProductListQuery,
    ProductOut,
    ProductPriceHistoryQuery,
    ProductUpsertIn,
)
from app.products.service import ProductsService
from app.rates.api import get_rates_service
from app.rates.service import RatesService
from app.users.domain import User

router = APIRouter(prefix="/products", tags=["products"])


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


def get_products_service(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    rates_service: Annotated[RatesService, Depends(get_rates_service)],
) -> ProductsService:
    """Build ProductsService with request-scoped repositories and app singletons."""
    clock: Clock = request.app.state.clock
    return ProductsService(
        collaborators=ProductCollaborators(
            products=SqlAlchemyProductRepository(session),
            prices=SqlAlchemyProductPriceRepository(session),
            rates=_RatesServiceLookupAdapter(rates_service),
            clock=clock,
        )
    )


@router.get("", response_model=list[ProductOut])
async def list_products(
    query: Annotated[ProductListQuery, Query()],
    _current_user: Annotated[User, Depends(get_current_user)],
    products_service: Annotated[ProductsService, Depends(get_products_service)],
) -> list[ProductOut]:
    """List products, active only by default or all with active first."""
    products = (
        await products_service.list_all_products()
        if query.include_archived
        else await products_service.list_active_products()
    )
    return [ProductOut.from_domain(product) for product in products]


@router.put("/{product_id}", response_model=ProductOut)
async def upsert_product(
    product_id: UUID,
    body: ProductUpsertIn,
    response: Response,
    _current_user: Annotated[User, Depends(get_current_user)],
    products_service: Annotated[ProductsService, Depends(get_products_service)],
) -> ProductOut:
    """Create or idempotently update a product."""
    result = await products_service.upsert_product(
        ProductInput(
            id=product_id,
            name=body.name,
            unit=body.unit,
            last_price=body.last_price,
            last_price_currency=body.currency,
        )
    )
    response.status_code = status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
    return ProductOut.from_domain(result.product)


@router.get("/{product_id}", response_model=ProductOut)
async def get_product(
    product_id: UUID,
    _current_user: Annotated[User, Depends(get_current_user)],
    products_service: Annotated[ProductsService, Depends(get_products_service)],
) -> ProductOut:
    """Return a single product by ID."""
    product = await products_service.get_product(product_id)
    return ProductOut.from_domain(product)


@router.post("/{product_id}/archive", response_model=ProductOut)
async def archive_product(
    product_id: UUID,
    _current_user: Annotated[User, Depends(get_current_user)],
    products_service: Annotated[ProductsService, Depends(get_products_service)],
) -> ProductOut:
    """Archive a product idempotently."""
    product = await products_service.archive_product(product_id)
    return ProductOut.from_domain(product)


@router.post("/{product_id}/unarchive", response_model=ProductOut)
async def unarchive_product(
    product_id: UUID,
    _current_user: Annotated[User, Depends(get_current_user)],
    products_service: Annotated[ProductsService, Depends(get_products_service)],
) -> ProductOut:
    """Unarchive a product idempotently."""
    product = await products_service.unarchive_product(product_id)
    return ProductOut.from_domain(product)


@router.get("/{product_id}/prices", response_model=list[PriceEntryOut])
async def list_product_prices(
    product_id: UUID,
    query: Annotated[ProductPriceHistoryQuery, Query()],
    _current_user: Annotated[User, Depends(get_current_user)],
    products_service: Annotated[ProductsService, Depends(get_products_service)],
) -> list[PriceEntryOut]:
    """List historical price entries for a product within an inclusive date range."""
    entries = await products_service.price_history(
        product_id=product_id,
        from_date=query.from_date,
        to_date=query.to_date,
    )
    return [PriceEntryOut.from_domain(entry) for entry in entries]
