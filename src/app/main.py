from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.auth.api import router as auth_router
from app.auth.errors import InvalidCredentials, InvalidRefreshToken, RefreshTokenReused
from app.auth.passwords import PasswordHasher
from app.clock import Clock, SystemClock
from app.config import Settings, get_settings
from app.db import create_engine, create_session_factory
from app.expenses.api import router as expenses_router
from app.expenses.errors import (
    CategoryArchived,
    CategoryNameTaken,
    CategoryNotFound,
    ExpenseDeleted,
    ExpenseNotFound,
    InvalidCategoryName,
    InvalidExpenseAmount,
    InvalidExpenseNote,
    InvalidExpensePeriod,
)
from app.expenses.errors import (
    RateNotAvailableForDate as ExpenseRateNotAvailableForDate,
)
from app.health.api import router as health_router
from app.income.api import router as income_router
from app.income.errors import (
    IncomeRuleEnded,
    IncomeRuleNotFound,
    InvalidIncomeAmount,
    InvalidIncomeDays,
    InvalidIncomeName,
    InvalidIncomePeriod,
    OccurrenceNotFound,
    OccurrenceNotReceived,
)
from app.income.errors import (
    RateNotAvailableForDate as IncomeRateNotAvailableForDate,
)
from app.products.api import router as products_router
from app.products.errors import (
    InvalidPricePeriod,
    InvalidProductName,
    InvalidProductPrice,
    ProductNameTaken,
    ProductNotFound,
)
from app.products.errors import (
    RateNotAvailableForDate as ProductRateNotAvailableForDate,
)
from app.rates.api import router as rates_router
from app.rates.domain import RateSources
from app.rates.sources import (
    BcvHtmlSource,
    BinanceP2PSource,
    DolarApiSource,
    build_bcv_http_client,
    build_default_http_client,
)
from app.shopping.api import router as shopping_router
from app.shopping.errors import (
    DuplicateListItem,
    InvalidDaysAhead,
    InvalidItemPrice,
    InvalidItemQuantity,
    InvalidListName,
    ItemAlreadyBought,
    ListClosed,
    ListDeleted,
    ListItemNotFound,
    NothingBought,
    PriceRequired,
    ProductArchived,
    ShoppingListNotFound,
)
from app.shopping.errors import (
    ProductNotFound as ShoppingProductNotFound,
)
from app.shopping.errors import (
    RateNotAvailableForDate as ShoppingRateNotAvailableForDate,
)
from app.users.api import router as users_router


async def _handle_invalid_credentials(_request: Request, _exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content={"detail": "Incorrect email or password"},
        headers={"WWW-Authenticate": "Bearer"},
    )


async def _handle_invalid_refresh_token(_request: Request, _exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content={"detail": "Invalid refresh token"},
    )


def _make_detail_handler(
    status_code: int,
) -> Callable[[Request, Exception], Awaitable[JSONResponse]]:
    async def _handler(_request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=status_code, content={"detail": str(exc)})

    return _handler


def _register_income_error_handlers(application: FastAPI) -> None:
    mappings: tuple[tuple[type[Exception], int], ...] = (
        (IncomeRuleNotFound, status.HTTP_404_NOT_FOUND),
        (OccurrenceNotFound, status.HTTP_404_NOT_FOUND),
        (IncomeRuleEnded, status.HTTP_409_CONFLICT),
        (OccurrenceNotReceived, status.HTTP_409_CONFLICT),
        (IncomeRateNotAvailableForDate, status.HTTP_409_CONFLICT),
        (InvalidIncomeName, status.HTTP_422_UNPROCESSABLE_CONTENT),
        (InvalidIncomeAmount, status.HTTP_422_UNPROCESSABLE_CONTENT),
        (InvalidIncomeDays, status.HTTP_422_UNPROCESSABLE_CONTENT),
        (InvalidIncomePeriod, status.HTTP_422_UNPROCESSABLE_CONTENT),
    )
    for exc_type, status_code in mappings:
        application.add_exception_handler(exc_type, _make_detail_handler(status_code))


def _register_expense_error_handlers(application: FastAPI) -> None:
    mappings: tuple[tuple[type[Exception], int], ...] = (
        (CategoryNotFound, status.HTTP_404_NOT_FOUND),
        (ExpenseNotFound, status.HTTP_404_NOT_FOUND),
        (CategoryNameTaken, status.HTTP_409_CONFLICT),
        (CategoryArchived, status.HTTP_409_CONFLICT),
        (ExpenseDeleted, status.HTTP_409_CONFLICT),
        (ExpenseRateNotAvailableForDate, status.HTTP_409_CONFLICT),
        (InvalidCategoryName, status.HTTP_422_UNPROCESSABLE_CONTENT),
        (InvalidExpenseAmount, status.HTTP_422_UNPROCESSABLE_CONTENT),
        (InvalidExpenseNote, status.HTTP_422_UNPROCESSABLE_CONTENT),
        (InvalidExpensePeriod, status.HTTP_422_UNPROCESSABLE_CONTENT),
    )
    for exc_type, status_code in mappings:
        application.add_exception_handler(exc_type, _make_detail_handler(status_code))


def _register_product_error_handlers(application: FastAPI) -> None:
    mappings: tuple[tuple[type[Exception], int], ...] = (
        (ProductNotFound, status.HTTP_404_NOT_FOUND),
        (ProductNameTaken, status.HTTP_409_CONFLICT),
        (ProductRateNotAvailableForDate, status.HTTP_409_CONFLICT),
        (InvalidProductName, status.HTTP_422_UNPROCESSABLE_CONTENT),
        (InvalidProductPrice, status.HTTP_422_UNPROCESSABLE_CONTENT),
        (InvalidPricePeriod, status.HTTP_422_UNPROCESSABLE_CONTENT),
    )
    for exc_type, status_code in mappings:
        application.add_exception_handler(exc_type, _make_detail_handler(status_code))


def _register_shopping_error_handlers(application: FastAPI) -> None:
    mappings: tuple[tuple[type[Exception], int], ...] = (
        (ShoppingListNotFound, status.HTTP_404_NOT_FOUND),
        (ListItemNotFound, status.HTTP_404_NOT_FOUND),
        (ShoppingProductNotFound, status.HTTP_404_NOT_FOUND),
        (ProductArchived, status.HTTP_409_CONFLICT),
        (DuplicateListItem, status.HTTP_409_CONFLICT),
        (ItemAlreadyBought, status.HTTP_409_CONFLICT),
        (ListClosed, status.HTTP_409_CONFLICT),
        (ListDeleted, status.HTTP_409_CONFLICT),
        (ShoppingRateNotAvailableForDate, status.HTTP_409_CONFLICT),
        (InvalidListName, status.HTTP_422_UNPROCESSABLE_CONTENT),
        (InvalidItemQuantity, status.HTTP_422_UNPROCESSABLE_CONTENT),
        (InvalidItemPrice, status.HTTP_422_UNPROCESSABLE_CONTENT),
        (InvalidDaysAhead, status.HTTP_422_UNPROCESSABLE_CONTENT),
        (PriceRequired, status.HTTP_422_UNPROCESSABLE_CONTENT),
        (NothingBought, status.HTTP_422_UNPROCESSABLE_CONTENT),
    )
    for exc_type, status_code in mappings:
        application.add_exception_handler(exc_type, _make_detail_handler(status_code))


def create_app(
    settings: Settings | None = None,
    clock: Clock | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application instance."""

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        resolved_settings = settings or get_settings()
        resolved_clock = clock or SystemClock()
        engine = create_engine(resolved_settings.database_url)
        session_factory = create_session_factory(engine)
        bcv_client = build_bcv_http_client()
        default_client = build_default_http_client()

        application.state.settings = resolved_settings
        application.state.clock = resolved_clock
        application.state.password_hasher = PasswordHasher()
        application.state.engine = engine
        application.state.session_factory = session_factory
        application.state.rate_sources = RateSources(
            bcv_html=BcvHtmlSource(bcv_client),
            dolarapi=DolarApiSource(default_client),
            binance=BinanceP2PSource(default_client, resolved_clock),
        )

        try:
            yield
        finally:
            await bcv_client.aclose()
            await default_client.aclose()
            await engine.dispose()

    application = FastAPI(title="Ahorra API", lifespan=lifespan)
    application.add_exception_handler(InvalidCredentials, _handle_invalid_credentials)
    application.add_exception_handler(InvalidRefreshToken, _handle_invalid_refresh_token)
    application.add_exception_handler(RefreshTokenReused, _handle_invalid_refresh_token)
    _register_income_error_handlers(application)
    _register_expense_error_handlers(application)
    _register_product_error_handlers(application)
    _register_shopping_error_handlers(application)
    application.include_router(health_router)
    application.include_router(auth_router)
    application.include_router(users_router)
    application.include_router(rates_router)
    application.include_router(income_router)
    application.include_router(expenses_router)
    application.include_router(products_router)
    application.include_router(shopping_router)
    return application


app = create_app()
