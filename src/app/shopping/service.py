from collections import defaultdict
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from uuid import UUID, uuid5

from app.clock import caracas_date
from app.shopping.domain import (
    DEFAULT_RECOMMENDATION_DAYS_AHEAD,
    MAX_LIST_NAME_LENGTH,
    MAX_RECOMMENDATION_DAYS_AHEAD,
    MIN_LIST_NAME_LENGTH,
    MIN_RECOMMENDATION_DAYS_AHEAD,
    MIN_RECOMMENDATION_PURCHASE_DATES,
    SHOPPING_EXPENSE_NAMESPACE,
    BoughtTotals,
    BuyItemInput,
    CloseListInput,
    Currency,
    EstimatedTotals,
    ItemStatus,
    ListStatus,
    ListTotals,
    ProductSummary,
    PurchaseHistoryRef,
    RateSnapshot,
    RecommendationItem,
    RecommendationsResult,
    RecommendationTotals,
    RecordExpenseInput,
    RecordPurchaseInput,
    ShoppingCollaborators,
    ShoppingList,
    ShoppingListItem,
    ShoppingListItemDetail,
    ShoppingListWithItems,
    UpsertItemInput,
    UpsertItemResult,
    UpsertListResult,
)
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
    ProductNotFound,
    RateNotAvailableForDate,
    ShoppingListNotFound,
)

MONEY_QUANTUM = Decimal("0.01")
QUANTITY_QUANTUM = Decimal("0.001")
INTERVAL_QUANTUM = Decimal("0.1")
DAYS_QUANTUM = Decimal("1")


def _quantize_money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_EVEN)


def _quantize_quantity(value: Decimal) -> Decimal:
    return value.quantize(QUANTITY_QUANTUM, rounding=ROUND_HALF_EVEN)


def _validate_list_name(raw_name: str) -> str:
    stripped = raw_name.strip()
    if len(stripped) < MIN_LIST_NAME_LENGTH or len(stripped) > MAX_LIST_NAME_LENGTH:
        raise InvalidListName(
            f"Shopping list name must be between {MIN_LIST_NAME_LENGTH} "
            f"and {MAX_LIST_NAME_LENGTH} characters."
        )
    return stripped


def _validate_quantity(value: Decimal) -> Decimal:
    if not value.is_finite() or value <= Decimal("0"):
        raise InvalidItemQuantity("Item quantity must be greater than zero.")
    return _quantize_quantity(value)


def _validate_buy_price(price: Decimal | None, currency: Currency | None) -> Decimal | None:
    if (price is None) != (currency is None):
        raise InvalidItemPrice("Both price and currency must be set together.")
    if price is None:
        return None
    if not price.is_finite() or price <= Decimal("0"):
        raise InvalidItemPrice("Item price must be greater than zero.")
    return _quantize_money(price)


def _validate_days_ahead(days_ahead: int) -> int:
    if days_ahead < MIN_RECOMMENDATION_DAYS_AHEAD or days_ahead > MAX_RECOMMENDATION_DAYS_AHEAD:
        raise InvalidDaysAhead(
            f"days_ahead must be between {MIN_RECOMMENDATION_DAYS_AHEAD} "
            f"and {MAX_RECOMMENDATION_DAYS_AHEAD}."
        )
    return days_ahead


def _item_detail(item: ShoppingListItem, product: ProductSummary) -> ShoppingListItemDetail:
    return ShoppingListItemDetail(
        item=item,
        product_name=product.name,
        product_unit=product.unit,
        product_last_price=product.last_price,
        product_last_price_currency=product.last_price_currency,
    )


def _to_ves(price: Decimal, currency: Currency, quantity: Decimal, bcv_rate: Decimal) -> Decimal:
    line = price * quantity
    return line * bcv_rate if currency == Currency.usd else line


def _compute_estimated_totals(
    priced_rows: list[tuple[Decimal | None, Currency | None, Decimal]],
    bcv: RateSnapshot,
    usdt: RateSnapshot,
) -> EstimatedTotals:
    raw_ves = Decimal("0")
    unpriced_items = 0
    for price, currency, quantity in priced_rows:
        if price is None or currency is None:
            unpriced_items += 1
            continue
        raw_ves += _to_ves(price, currency, quantity, bcv.value)
    return EstimatedTotals(
        total_ves=_quantize_money(raw_ves),
        total_usd=_quantize_money(raw_ves / bcv.value),
        usdt_to_sell=_quantize_money(raw_ves / usdt.value),
        unpriced_items=unpriced_items,
    )


def _compute_bought_totals(
    items: list[ShoppingListItem],
    bcv: RateSnapshot,
    usdt: RateSnapshot,
) -> BoughtTotals:
    raw_ves = Decimal("0")
    for item in items:
        if (
            item.status != ItemStatus.bought
            or item.bought_price is None
            or item.bought_currency is None
        ):
            continue
        raw_ves += _to_ves(item.bought_price, item.bought_currency, item.quantity, bcv.value)
    return BoughtTotals(
        total_ves=_quantize_money(raw_ves),
        total_usd=_quantize_money(raw_ves / bcv.value),
        usdt_to_sell=_quantize_money(raw_ves / usdt.value),
    )


def _compute_avg_interval(distinct_dates: list[date]) -> Decimal:
    gaps = [
        Decimal((distinct_dates[idx] - distinct_dates[idx - 1]).days)
        for idx in range(1, len(distinct_dates))
    ]
    return sum(gaps, Decimal("0")) / Decimal(len(gaps))


class ShoppingService:
    """Application service managing shopping lists, items, totals, closing, and recommendations."""

    def __init__(self, collaborators: ShoppingCollaborators) -> None:
        self._lists = collaborators.lists
        self._items = collaborators.items
        self._products = collaborators.products
        self._expenses = collaborators.expenses
        self._rates = collaborators.rates
        self._clock = collaborators.clock

    async def upsert_list(self, list_id: UUID, name: str) -> UpsertListResult:
        """Create or idempotently rename an open shopping list."""
        normalized_name = _validate_list_name(name)
        now = self._clock.now()

        existing = await self._lists.get_by_id(list_id)
        if existing is not None:
            return await self._update_existing_list(existing, normalized_name, now)

        candidate = ShoppingList(
            id=list_id,
            name=normalized_name,
            status=ListStatus.open,
            closed_at=None,
            expense_id=None,
            deleted_at=None,
            created_at=now,
            updated_at=now,
        )
        saved, inserted = await self._lists.insert_if_absent(candidate)
        if not inserted:
            return await self._update_existing_list(saved, normalized_name, now)
        return UpsertListResult(shopping_list=saved, created=True)

    async def list_lists(self, status: ListStatus | None = None) -> list[ShoppingList]:
        """Return non-deleted shopping lists optionally filtered by status."""
        return await self._lists.list_non_deleted(status)

    async def list_lists_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[ShoppingList]:
        """Return shopping lists changed in (since, until] ordered by (updated_at, id)."""
        return await self._lists.list_changed_between(since, until)

    async def list_items_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[ShoppingListItem]:
        """Return shopping list items changed in (since, until] ordered by (updated_at, id)."""
        return await self._items.list_changed_between(since, until)

    async def get_list(self, list_id: UUID) -> ShoppingListWithItems:
        """Return a non-deleted shopping list with its items enriched with product data."""
        shopping_list = await self._require_non_deleted_list(list_id)
        items = await self._items.list_for_list(list_id)
        if not items:
            return ShoppingListWithItems(shopping_list=shopping_list, items=[])
        products_by_id = {p.id: p for p in await self._products.list_all_products()}
        details = [_item_detail(item, products_by_id[item.product_id]) for item in items]
        return ShoppingListWithItems(shopping_list=shopping_list, items=details)

    async def delete_list(self, list_id: UUID) -> ShoppingList:
        """Soft-delete a shopping list idempotently."""
        existing = await self._require_list_record(list_id)
        if existing.deleted_at is not None:
            return existing
        now = self._clock.now()
        return await self._lists.update(replace(existing, deleted_at=now, updated_at=now))

    async def upsert_item(self, payload: UpsertItemInput) -> UpsertItemResult:
        """Add or update an item on an open shopping list."""
        quantity = _validate_quantity(payload.quantity)
        await self._require_editable_list(payload.list_id)
        product = await self._require_product(payload.product_id)

        existing = await self._items.get_by_id(payload.item_id)
        if existing is not None:
            return await self._update_existing_item(existing, payload, quantity, product)

        if product.archived_at is not None:
            raise ProductArchived(f"Product {product.id} is archived.")
        await self._ensure_product_not_in_list(payload.list_id, payload.product_id)

        now = self._clock.now()
        candidate = ShoppingListItem(
            id=payload.item_id,
            list_id=payload.list_id,
            product_id=payload.product_id,
            quantity=quantity,
            status=ItemStatus.pending,
            bought_price=None,
            bought_currency=None,
            bought_at=None,
            created_at=now,
            updated_at=now,
        )
        saved, inserted = await self._items.insert_if_absent(candidate)
        if not inserted:
            return await self._update_existing_item(saved, payload, quantity, product)
        return UpsertItemResult(item=_item_detail(saved, product), created=True)

    async def delete_item(self, list_id: UUID, item_id: UUID) -> None:
        """Hard-delete a pending item from an open shopping list."""
        await self._require_editable_list(list_id)
        item = await self._require_list_item(list_id, item_id)
        if item.status == ItemStatus.bought:
            raise ItemAlreadyBought(f"Item {item_id} has already been bought.")
        await self._items.delete_by_id(item_id)

    async def buy_item(self, payload: BuyItemInput) -> ShoppingListItemDetail:
        """Mark a list item bought (or correct a bought item) and sync product price history."""
        explicit_price = _validate_buy_price(payload.price, payload.currency)
        await self._require_editable_list(payload.list_id)
        item = await self._require_list_item(payload.list_id, payload.item_id)
        product = await self._require_product(item.product_id)

        target_price, target_currency = self._resolve_buy_price(
            explicit_price,
            payload.currency,
            product,
        )
        now = self._clock.now()
        today = caracas_date(now)
        bcv, usdt = await self._lookup_rates(today)

        recorded_on = caracas_date(item.bought_at) if item.bought_at is not None else today
        updated_product = await self._products.record_purchase_price(
            RecordPurchaseInput(
                product_id=product.id,
                list_item_id=item.id,
                price=target_price,
                currency=target_currency,
                recorded_on=recorded_on,
                bcv_rate=bcv,
                usdt_rate=usdt,
            )
        )
        bought_at = item.bought_at if item.bought_at is not None else now
        saved_item = await self._items.update(
            replace(
                item,
                status=ItemStatus.bought,
                bought_price=target_price,
                bought_currency=target_currency,
                bought_at=bought_at,
                updated_at=now,
            )
        )
        return _item_detail(saved_item, updated_product)

    async def unbuy_item(self, list_id: UUID, item_id: UUID) -> ShoppingListItemDetail:
        """Revert a bought item back to pending and restore the product's last price."""
        await self._require_editable_list(list_id)
        item = await self._require_list_item(list_id, item_id)

        updated_product = await self._products.delete_purchase_price_and_restore(
            item.product_id,
            item.id,
        )
        if item.status == ItemStatus.pending:
            return _item_detail(item, updated_product)

        saved_item = await self._items.update(
            replace(
                item,
                status=ItemStatus.pending,
                bought_price=None,
                bought_currency=None,
                bought_at=None,
                updated_at=self._clock.now(),
            )
        )
        return _item_detail(saved_item, updated_product)

    async def list_totals(self, list_id: UUID) -> ListTotals:
        """Calculate estimated and bought totals for a shopping list using today's rates."""
        await self._require_non_deleted_list(list_id)
        today = caracas_date(self._clock.now())
        bcv, usdt = await self._lookup_rates(today)
        items = await self._items.list_for_list(list_id)
        products_by_id = (
            {p.id: p for p in await self._products.list_all_products()} if items else {}
        )
        estimated_rows = [
            (
                products_by_id[item.product_id].last_price,
                products_by_id[item.product_id].last_price_currency,
                item.quantity,
            )
            for item in items
        ]
        return ListTotals(
            estimated=_compute_estimated_totals(estimated_rows, bcv, usdt),
            bought=_compute_bought_totals(items, bcv, usdt),
            bcv_rate=bcv,
            usdt_rate=usdt,
        )

    async def close_list(self, payload: CloseListInput) -> ShoppingList:
        """Close an open list and idempotently record an expense for its bought items."""
        existing = await self._require_list_record(payload.list_id)
        if existing.deleted_at is not None:
            raise ListDeleted(f"Shopping list {payload.list_id} has been deleted.")
        if existing.status == ListStatus.closed:
            return existing

        items = await self._items.list_for_list(payload.list_id)
        bought_items = [item for item in items if item.status == ItemStatus.bought]
        if not bought_items:
            raise NothingBought("Cannot close a shopping list with no bought items.")

        now = self._clock.now()
        spent_on = payload.spent_on if payload.spent_on is not None else caracas_date(now)
        amount, currency = await self._compute_close_expense_amount(bought_items, spent_on)
        expense_id = uuid5(SHOPPING_EXPENSE_NAMESPACE, str(existing.id))

        await self._expenses.upsert_expense(
            RecordExpenseInput(
                expense_id=expense_id,
                category_id=payload.category_id,
                amount=amount,
                currency=currency,
                spent_on=spent_on,
                note=f"Shopping list: {existing.name}",
            )
        )
        return await self._lists.update(
            replace(
                existing,
                status=ListStatus.closed,
                closed_at=now,
                expense_id=expense_id,
                updated_at=now,
            )
        )

    async def recommendations(
        self,
        days_ahead: int = DEFAULT_RECOMMENDATION_DAYS_AHEAD,
    ) -> RecommendationsResult:
        """Recommend non-archived products due for purchase within days_ahead."""
        validated_days = _validate_days_ahead(days_ahead)
        today = caracas_date(self._clock.now())
        bcv, usdt = await self._lookup_rates(today)
        cutoff = today + timedelta(days=validated_days)

        purchase_refs = await self._products.list_purchase_refs_for_active_products()
        non_deleted_item_ids = await self._items.list_non_deleted_item_ids()
        dates_by_product = self._group_purchase_dates(purchase_refs, non_deleted_item_ids)
        bought_quantities = await self._items.list_bought_quantities_by_product()
        products_by_id = {p.id: p for p in await self._products.list_all_products()}

        items: list[RecommendationItem] = []
        for product_id, distinct_dates in dates_by_product.items():
            rec = self._build_recommendation(
                product=products_by_id[product_id],
                distinct_dates=distinct_dates,
                quantities=bought_quantities[product_id],
                cutoff=cutoff,
            )
            if rec is not None:
                items.append(rec)

        items.sort(key=lambda rec: (rec.next_due, rec.product_name, rec.product_id))
        estimated = _compute_estimated_totals(
            [(rec.last_price, rec.last_price_currency, rec.suggested_quantity) for rec in items],
            bcv,
            usdt,
        )
        return RecommendationsResult(
            items=items,
            totals=RecommendationTotals(
                total_ves=estimated.total_ves,
                total_usd=estimated.total_usd,
                usdt_to_sell=estimated.usdt_to_sell,
                unpriced_items=estimated.unpriced_items,
                bcv_rate=bcv,
                usdt_rate=usdt,
            ),
        )

    async def _require_list_record(self, list_id: UUID) -> ShoppingList:
        shopping_list = await self._lists.get_by_id(list_id)
        if shopping_list is None:
            raise ShoppingListNotFound(f"Shopping list {list_id} was not found.")
        return shopping_list

    async def _require_non_deleted_list(self, list_id: UUID) -> ShoppingList:
        shopping_list = await self._require_list_record(list_id)
        if shopping_list.deleted_at is not None:
            raise ShoppingListNotFound(f"Shopping list {list_id} was not found.")
        return shopping_list

    async def _require_editable_list(self, list_id: UUID) -> ShoppingList:
        shopping_list = await self._require_list_record(list_id)
        if shopping_list.deleted_at is not None:
            raise ListDeleted(f"Shopping list {list_id} has been deleted.")
        if shopping_list.status == ListStatus.closed:
            raise ListClosed(f"Shopping list {list_id} is closed.")
        return shopping_list

    async def _require_product(self, product_id: UUID) -> ProductSummary:
        product = await self._products.get_product(product_id)
        if product is None:
            raise ProductNotFound(f"Product {product_id} was not found.")
        return product

    async def _require_list_item(self, list_id: UUID, item_id: UUID) -> ShoppingListItem:
        item = await self._items.get_by_id(item_id)
        if item is None or item.list_id != list_id:
            raise ListItemNotFound(f"Item {item_id} was not found on list {list_id}.")
        return item

    async def _ensure_product_not_in_list(self, list_id: UUID, product_id: UUID) -> None:
        existing = await self._items.get_by_list_and_product(list_id, product_id)
        if existing is not None:
            raise DuplicateListItem(
                f"Product {product_id} already exists on shopping list {list_id}."
            )

    async def _update_existing_list(
        self,
        existing: ShoppingList,
        normalized_name: str,
        now: datetime,
    ) -> UpsertListResult:
        if existing.deleted_at is not None:
            raise ListDeleted(f"Shopping list {existing.id} has been deleted.")
        if existing.status == ListStatus.closed:
            raise ListClosed(f"Shopping list {existing.id} is closed.")
        if existing.name == normalized_name:
            return UpsertListResult(shopping_list=existing, created=False)
        saved = await self._lists.update(replace(existing, name=normalized_name, updated_at=now))
        return UpsertListResult(shopping_list=saved, created=False)

    async def _update_existing_item(
        self,
        existing: ShoppingListItem,
        payload: UpsertItemInput,
        quantity: Decimal,
        product: ProductSummary,
    ) -> UpsertItemResult:
        if existing.list_id != payload.list_id:
            raise ListItemNotFound(
                f"Item {payload.item_id} was not found on list {payload.list_id}."
            )
        if existing.status == ItemStatus.bought and existing.product_id != payload.product_id:
            raise ItemAlreadyBought(f"Cannot change product of bought item {existing.id}.")
        if existing.product_id == payload.product_id and existing.quantity == quantity:
            return UpsertItemResult(item=_item_detail(existing, product), created=False)

        if existing.product_id != payload.product_id:
            if product.archived_at is not None:
                raise ProductArchived(f"Product {product.id} is archived.")
            await self._ensure_product_not_in_list(payload.list_id, payload.product_id)

        saved = await self._items.update(
            replace(
                existing,
                product_id=payload.product_id,
                quantity=quantity,
                updated_at=self._clock.now(),
            )
        )
        return UpsertItemResult(item=_item_detail(saved, product), created=False)

    def _resolve_buy_price(
        self,
        explicit_price: Decimal | None,
        explicit_currency: Currency | None,
        product: ProductSummary,
    ) -> tuple[Decimal, Currency]:
        if explicit_price is not None and explicit_currency is not None:
            return explicit_price, explicit_currency
        if product.last_price is None or product.last_price_currency is None:
            raise PriceRequired(f"Product {product.id} has no last_price; price is required.")
        return product.last_price, product.last_price_currency

    async def _lookup_rates(self, on: date) -> tuple[RateSnapshot, RateSnapshot]:
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
        return bcv, usdt

    async def _compute_close_expense_amount(
        self,
        bought_items: list[ShoppingListItem],
        spent_on: date,
    ) -> tuple[Decimal, Currency]:
        currencies = {
            item.bought_currency for item in bought_items if item.bought_currency is not None
        }
        if len(currencies) == 1:
            currency = next(iter(currencies))
            raw_total = sum(
                (
                    item.bought_price * item.quantity
                    for item in bought_items
                    if item.bought_price is not None
                ),
                Decimal("0"),
            )
            return _quantize_money(raw_total), currency

        bcv = await self._rates.bcv_in_force(spent_on)
        if bcv is None:
            raise RateNotAvailableForDate(
                f"No BCV exchange rate is available for {spent_on.isoformat()}."
            )
        raw_ves = Decimal("0")
        for item in bought_items:
            if item.bought_price is None or item.bought_currency is None:
                continue
            raw_ves += _to_ves(item.bought_price, item.bought_currency, item.quantity, bcv.value)
        return _quantize_money(raw_ves), Currency.ves

    def _group_purchase_dates(
        self,
        purchase_refs: list[PurchaseHistoryRef],
        non_deleted_item_ids: set[UUID],
    ) -> dict[UUID, list[date]]:
        grouped: dict[UUID, set[date]] = defaultdict(set)
        for ref in purchase_refs:
            if ref.list_item_id in non_deleted_item_ids:
                grouped[ref.product_id].add(ref.recorded_on)
        return {
            product_id: sorted(dates)
            for product_id, dates in grouped.items()
            if len(dates) >= MIN_RECOMMENDATION_PURCHASE_DATES
        }

    def _build_recommendation(
        self,
        *,
        product: ProductSummary,
        distinct_dates: list[date],
        quantities: list[Decimal],
        cutoff: date,
    ) -> RecommendationItem | None:
        if product.archived_at is not None or not quantities:
            return None
        raw_avg = _compute_avg_interval(distinct_dates)
        rounded_days = int(raw_avg.quantize(DAYS_QUANTUM, rounding=ROUND_HALF_EVEN))
        last_purchased_on = distinct_dates[-1]
        next_due = last_purchased_on + timedelta(days=rounded_days)
        if next_due > cutoff:
            return None

        suggested_quantity = _quantize_quantity(
            sum(quantities, Decimal("0")) / Decimal(len(quantities))
        )
        return RecommendationItem(
            product_id=product.id,
            product_name=product.name,
            unit=product.unit,
            last_price=product.last_price,
            last_price_currency=product.last_price_currency,
            avg_interval_days=raw_avg.quantize(INTERVAL_QUANTUM, rounding=ROUND_HALF_EVEN),
            last_purchased_on=last_purchased_on,
            next_due=next_due,
            suggested_quantity=suggested_quantity,
        )
