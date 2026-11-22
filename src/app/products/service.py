from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from uuid import UUID, uuid4

from app.clock import caracas_date
from app.products.domain import (
    MAX_PRODUCT_NAME_LENGTH,
    MIN_PRODUCT_NAME_LENGTH,
    Currency,
    PriceEntry,
    PriceSource,
    Product,
    ProductCollaborators,
    ProductInput,
    PurchaseEntryRef,
    PurchasePriceInput,
    RateSnapshot,
    Unit,
    UpsertProductResult,
)
from app.products.errors import (
    InvalidPricePeriod,
    InvalidProductName,
    InvalidProductPrice,
    ProductNameTaken,
    ProductNotFound,
    RateNotAvailableForDate,
)

PRICE_QUANTUM = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class _NormalizedProductData:
    name: str
    unit: Unit
    last_price: Decimal | None
    last_price_currency: Currency | None


@dataclass(frozen=True, slots=True)
class _ProductRates:
    bcv: RateSnapshot
    usdt: RateSnapshot


def _quantize_price(value: Decimal) -> Decimal:
    return value.quantize(PRICE_QUANTUM, rounding=ROUND_HALF_EVEN)


def _validate_product_name(raw_name: str) -> str:
    stripped = raw_name.strip()
    if len(stripped) < MIN_PRODUCT_NAME_LENGTH or len(stripped) > MAX_PRODUCT_NAME_LENGTH:
        raise InvalidProductName(
            f"Product name must be between {MIN_PRODUCT_NAME_LENGTH} "
            f"and {MAX_PRODUCT_NAME_LENGTH} characters."
        )
    return stripped


def _validate_positive_price(value: Decimal) -> Decimal:
    if not value.is_finite() or value <= Decimal("0"):
        raise InvalidProductPrice("Product price must be greater than zero.")
    return _quantize_price(value)


def _normalize_product_input(payload: ProductInput) -> _NormalizedProductData:
    name = _validate_product_name(payload.name)
    has_price = payload.last_price is not None
    has_currency = payload.last_price_currency is not None
    if has_price != has_currency:
        raise InvalidProductPrice("Both last_price and last_price_currency must be set together.")
    price = _validate_positive_price(payload.last_price) if payload.last_price is not None else None
    return _NormalizedProductData(
        name=name,
        unit=payload.unit,
        last_price=price,
        last_price_currency=payload.last_price_currency,
    )


def _validate_date_range(from_date: date, to_date: date) -> None:
    if from_date > to_date:
        raise InvalidPricePeriod("'from' must be less than or equal to 'to'.")


class ProductsService:
    """Application service managing the product catalog and historical prices."""

    def __init__(self, collaborators: ProductCollaborators) -> None:
        self._products = collaborators.products
        self._prices = collaborators.prices
        self._rates = collaborators.rates
        self._clock = collaborators.clock

    async def upsert_product(self, payload: ProductInput) -> UpsertProductResult:
        """Create or idempotently update a product, appending manual price history on change."""
        normalized = _normalize_product_input(payload)
        now = self._clock.now()
        today = caracas_date(now)

        existing = await self._products.get_by_id(payload.id)
        if existing is not None:
            return await self._update_existing_product(existing, normalized, now, today)

        await self._ensure_name_available(normalized.name, exclude_id=None)
        rates = await self._lookup_rates(today) if normalized.last_price is not None else None
        candidate = Product(
            id=payload.id,
            name=normalized.name,
            unit=normalized.unit,
            last_price=normalized.last_price,
            last_price_currency=normalized.last_price_currency,
            archived_at=None,
            created_at=now,
            updated_at=now,
        )
        saved, inserted = await self._products.insert_if_absent(candidate)
        if not inserted:
            return await self._update_existing_product(saved, normalized, now, today)

        if (
            rates is not None
            and saved.last_price is not None
            and saved.last_price_currency is not None
        ):
            await self._prices.add(
                PriceEntry(
                    id=uuid4(),
                    product_id=saved.id,
                    price=saved.last_price,
                    currency=saved.last_price_currency,
                    recorded_on=today,
                    source=PriceSource.manual,
                    list_item_id=None,
                    bcv_rate_value=rates.bcv.value,
                    bcv_rate_date=rates.bcv.value_date,
                    usdt_rate_value=rates.usdt.value,
                    usdt_rate_date=rates.usdt.value_date,
                    created_at=now,
                )
            )
        return UpsertProductResult(product=saved, created=True)

    async def get_product(self, product_id: UUID) -> Product:
        """Return a product by ID or raise ProductNotFound."""
        return await self._require_product(product_id)

    async def list_active_products(self) -> list[Product]:
        """Return active products ordered by name, then id."""
        return await self._products.list_active()

    async def list_all_products(self) -> list[Product]:
        """Return all products, active first, then archived, ordered by name and id."""
        return await self._products.list_all()

    async def archive_product(self, product_id: UUID) -> Product:
        """Archive a product idempotently."""
        existing = await self._require_product(product_id)
        if existing.archived_at is not None:
            return existing
        now = self._clock.now()
        return await self._products.update(replace(existing, archived_at=now, updated_at=now))

    async def unarchive_product(self, product_id: UUID) -> Product:
        """Unarchive a product idempotently."""
        existing = await self._require_product(product_id)
        if existing.archived_at is None:
            return existing
        now = self._clock.now()
        return await self._products.update(replace(existing, archived_at=None, updated_at=now))

    async def price_history(
        self,
        product_id: UUID,
        from_date: date,
        to_date: date,
    ) -> list[PriceEntry]:
        """Return historical price entries for a product within an inclusive date range."""
        _validate_date_range(from_date, to_date)
        await self._require_product(product_id)
        return await self._prices.list_in_range(product_id, from_date, to_date)

    async def record_purchase_price(self, payload: PurchasePriceInput) -> Product:
        """Upsert a purchase price entry by list_item_id and sync the product's last price."""
        price = _validate_positive_price(payload.price)
        product = await self._require_product(payload.product_id)
        now = self._clock.now()

        existing_entry = await self._prices.get_by_list_item_id(payload.list_item_id)
        if existing_entry is not None:
            await self._update_purchase_entry(existing_entry, payload, price)
        else:
            candidate = PriceEntry(
                id=uuid4(),
                product_id=product.id,
                price=price,
                currency=payload.currency,
                recorded_on=payload.recorded_on,
                source=PriceSource.purchase,
                list_item_id=payload.list_item_id,
                bcv_rate_value=payload.bcv_rate.value,
                bcv_rate_date=payload.bcv_rate.value_date,
                usdt_rate_value=payload.usdt_rate.value,
                usdt_rate_date=payload.usdt_rate.value_date,
                created_at=now,
            )
            saved_entry, inserted = await self._prices.insert_purchase_if_absent(candidate)
            if not inserted:
                await self._update_purchase_entry(saved_entry, payload, price)

        return await self._sync_last_price_from_latest(product, now)

    async def delete_purchase_price_and_restore(
        self,
        product_id: UUID,
        list_item_id: UUID,
    ) -> Product:
        """Delete the purchase entry for list_item_id and restore the product's last price."""
        product = await self._require_product(product_id)
        await self._prices.delete_by_list_item_id(list_item_id)
        return await self._sync_last_price_from_latest(product, self._clock.now())

    async def _update_purchase_entry(
        self,
        entry: PriceEntry,
        payload: PurchasePriceInput,
        price: Decimal,
    ) -> PriceEntry:
        return await self._prices.update_purchase(
            replace(
                entry,
                price=price,
                currency=payload.currency,
                recorded_on=payload.recorded_on,
                bcv_rate_value=payload.bcv_rate.value,
                bcv_rate_date=payload.bcv_rate.value_date,
                usdt_rate_value=payload.usdt_rate.value,
                usdt_rate_date=payload.usdt_rate.value_date,
            )
        )

    async def _sync_last_price_from_latest(self, product: Product, now: datetime) -> Product:
        latest = await self._prices.latest_for_product(product.id)
        target_price = latest.price if latest is not None else None
        target_currency = latest.currency if latest is not None else None
        if product.last_price == target_price and product.last_price_currency == target_currency:
            return product
        return await self._products.update(
            replace(
                product,
                last_price=target_price,
                last_price_currency=target_currency,
                updated_at=now,
            )
        )

    async def list_purchase_refs_for_active_products(self) -> list[PurchaseEntryRef]:
        """Return purchase price entry references for all non-archived products."""
        return await self._prices.list_purchase_refs_for_active_products()

    async def _require_product(self, product_id: UUID) -> Product:
        product = await self._products.get_by_id(product_id)
        if product is None:
            raise ProductNotFound(f"Product {product_id} was not found.")
        return product

    async def _ensure_name_available(self, name: str, *, exclude_id: UUID | None) -> None:
        existing = await self._products.get_by_lower_name(name)
        if existing is not None and existing.id != exclude_id:
            raise ProductNameTaken(f"Product name {name!r} is already taken.")

    async def _update_existing_product(
        self,
        existing: Product,
        normalized: _NormalizedProductData,
        now: datetime,
        today: date,
    ) -> UpsertProductResult:
        target_price = (
            normalized.last_price if normalized.last_price is not None else existing.last_price
        )
        target_currency = (
            normalized.last_price_currency
            if normalized.last_price_currency is not None
            else existing.last_price_currency
        )
        price_changed = (target_price, target_currency) != (
            existing.last_price,
            existing.last_price_currency,
        )
        if (
            existing.name == normalized.name
            and existing.unit == normalized.unit
            and not price_changed
        ):
            return UpsertProductResult(product=existing, created=False)

        await self._ensure_name_available(normalized.name, exclude_id=existing.id)
        rates = await self._lookup_rates(today) if price_changed else None

        saved = await self._products.update(
            replace(
                existing,
                name=normalized.name,
                unit=normalized.unit,
                last_price=target_price,
                last_price_currency=target_currency,
                updated_at=now,
            )
        )
        if rates is not None and target_price is not None and target_currency is not None:
            await self._prices.add(
                PriceEntry(
                    id=uuid4(),
                    product_id=saved.id,
                    price=target_price,
                    currency=target_currency,
                    recorded_on=today,
                    source=PriceSource.manual,
                    list_item_id=None,
                    bcv_rate_value=rates.bcv.value,
                    bcv_rate_date=rates.bcv.value_date,
                    usdt_rate_value=rates.usdt.value,
                    usdt_rate_date=rates.usdt.value_date,
                    created_at=now,
                )
            )
        return UpsertProductResult(product=saved, created=False)

    async def _lookup_rates(self, on: date) -> _ProductRates:
        bcv = await self._rates.bcv_in_force(on)
        if bcv is None:
            raise RateNotAvailableForDate(
                f"No BCV exchange rate is available for {on.isoformat()}."
            )
        usdt = await self._rates.usdt_in_force(on)
        if usdt is None:
            raise RateNotAvailableForDate(
                f"No USDT exchange rate is available for {on.isoformat()}."
            )
        return _ProductRates(bcv=bcv, usdt=usdt)
