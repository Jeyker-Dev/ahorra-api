from datetime import date, datetime
from uuid import UUID

from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.products.domain import (
    Currency,
    PriceEntry,
    PriceSource,
    Product,
    PurchaseEntryRef,
    Unit,
)
from app.products.errors import ProductNameTaken, ProductNotFound
from app.products.models import ProductModel, ProductPriceModel

_PRODUCT_LOWER_NAME_INDEX = "uq_products_lower_name"


def _product_to_domain(model: ProductModel) -> Product:
    return Product(
        id=model.id,
        name=model.name,
        unit=Unit(model.unit),
        last_price=model.last_price,
        last_price_currency=(
            Currency(model.last_price_currency) if model.last_price_currency is not None else None
        ),
        archived_at=model.archived_at,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _price_entry_to_domain(model: ProductPriceModel) -> PriceEntry:
    return PriceEntry(
        id=model.id,
        product_id=model.product_id,
        price=model.price,
        currency=Currency(model.currency),
        recorded_on=model.recorded_on,
        source=PriceSource(model.source),
        list_item_id=model.list_item_id,
        bcv_rate_value=model.bcv_rate_value,
        bcv_rate_date=model.bcv_rate_date,
        usdt_rate_value=model.usdt_rate_value,
        usdt_rate_date=model.usdt_rate_date,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


class SqlAlchemyProductRepository:
    """SQLAlchemy implementation of ProductRepository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, product_id: UUID) -> Product | None:
        stmt = select(ProductModel).where(ProductModel.id == product_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return _product_to_domain(model) if model is not None else None

    async def get_by_lower_name(self, name: str) -> Product | None:
        stmt = select(ProductModel).where(func.lower(ProductModel.name) == name.lower())
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return _product_to_domain(model) if model is not None else None

    async def list_active(self) -> list[Product]:
        stmt = (
            select(ProductModel)
            .where(ProductModel.archived_at.is_(None))
            .order_by(ProductModel.name.asc(), ProductModel.id.asc())
        )
        result = await self._session.execute(stmt)
        return [_product_to_domain(model) for model in result.scalars().all()]

    async def list_all(self) -> list[Product]:
        stmt = select(ProductModel).order_by(
            ProductModel.archived_at.is_not(None).asc(),
            ProductModel.name.asc(),
            ProductModel.id.asc(),
        )
        result = await self._session.execute(stmt)
        return [_product_to_domain(model) for model in result.scalars().all()]

    async def insert_if_absent(self, product: Product) -> tuple[Product, bool]:
        stmt = (
            pg_insert(ProductModel)
            .values(
                id=product.id,
                name=product.name,
                unit=product.unit.value,
                last_price=product.last_price,
                last_price_currency=(
                    product.last_price_currency.value
                    if product.last_price_currency is not None
                    else None
                ),
                archived_at=product.archived_at,
                created_at=product.created_at,
                updated_at=product.updated_at,
            )
            .on_conflict_do_nothing(index_elements=[ProductModel.id])
            .returning(ProductModel.id)
        )
        try:
            result = await self._session.execute(stmt)
            inserted_id = result.scalar_one_or_none()
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            if _PRODUCT_LOWER_NAME_INDEX in str(exc):
                raise ProductNameTaken(f"Product name {product.name!r} is already taken.") from exc
            raise

        if inserted_id is not None:
            return product, True

        existing = await self.get_by_id(product.id)
        assert existing is not None
        return existing, False

    async def update(self, product: Product) -> Product:
        model = await self._session.get(ProductModel, product.id)
        if model is None:
            raise ProductNotFound(f"Product {product.id} was not found.")
        model.name = product.name
        model.unit = product.unit.value
        model.last_price = product.last_price
        model.last_price_currency = (
            product.last_price_currency.value if product.last_price_currency is not None else None
        )
        model.archived_at = product.archived_at
        model.updated_at = product.updated_at
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            if _PRODUCT_LOWER_NAME_INDEX in str(exc):
                raise ProductNameTaken(f"Product name {product.name!r} is already taken.") from exc
            raise
        return _product_to_domain(model)

    async def list_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[Product]:
        stmt = select(ProductModel).where(ProductModel.updated_at <= until)
        if since is not None:
            stmt = stmt.where(ProductModel.updated_at > since)
        stmt = stmt.order_by(ProductModel.updated_at.asc(), ProductModel.id.asc())
        result = await self._session.execute(stmt)
        return [_product_to_domain(model) for model in result.scalars().all()]


class SqlAlchemyProductPriceRepository:
    """SQLAlchemy implementation of ProductPriceRepository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, entry: PriceEntry) -> PriceEntry:
        model = ProductPriceModel(
            id=entry.id,
            product_id=entry.product_id,
            price=entry.price,
            currency=entry.currency.value,
            recorded_on=entry.recorded_on,
            source=entry.source.value,
            list_item_id=entry.list_item_id,
            bcv_rate_value=entry.bcv_rate_value,
            bcv_rate_date=entry.bcv_rate_date,
            usdt_rate_value=entry.usdt_rate_value,
            usdt_rate_date=entry.usdt_rate_date,
            created_at=entry.created_at,
            updated_at=entry.updated_at,
        )
        self._session.add(model)
        await self._session.commit()
        return _price_entry_to_domain(model)

    async def get_by_list_item_id(self, list_item_id: UUID) -> PriceEntry | None:
        stmt = select(ProductPriceModel).where(ProductPriceModel.list_item_id == list_item_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return _price_entry_to_domain(model) if model is not None else None

    async def insert_purchase_if_absent(self, entry: PriceEntry) -> tuple[PriceEntry, bool]:
        stmt = (
            pg_insert(ProductPriceModel)
            .values(
                id=entry.id,
                product_id=entry.product_id,
                price=entry.price,
                currency=entry.currency.value,
                recorded_on=entry.recorded_on,
                source=entry.source.value,
                list_item_id=entry.list_item_id,
                bcv_rate_value=entry.bcv_rate_value,
                bcv_rate_date=entry.bcv_rate_date,
                usdt_rate_value=entry.usdt_rate_value,
                usdt_rate_date=entry.usdt_rate_date,
                created_at=entry.created_at,
                updated_at=entry.updated_at,
            )
            .on_conflict_do_nothing(
                index_elements=[ProductPriceModel.list_item_id],
                index_where=text("list_item_id IS NOT NULL"),
            )
            .returning(ProductPriceModel.id)
        )
        result = await self._session.execute(stmt)
        inserted_id = result.scalar_one_or_none()
        await self._session.commit()
        if inserted_id is not None:
            return entry, True

        assert entry.list_item_id is not None
        existing = await self.get_by_list_item_id(entry.list_item_id)
        assert existing is not None
        return existing, False

    async def update_purchase(self, entry: PriceEntry) -> PriceEntry:
        model = await self._session.get(ProductPriceModel, entry.id)
        assert model is not None
        model.price = entry.price
        model.currency = entry.currency.value
        model.recorded_on = entry.recorded_on
        model.bcv_rate_value = entry.bcv_rate_value
        model.bcv_rate_date = entry.bcv_rate_date
        model.usdt_rate_value = entry.usdt_rate_value
        model.usdt_rate_date = entry.usdt_rate_date
        model.updated_at = entry.updated_at
        await self._session.commit()
        return _price_entry_to_domain(model)

    async def delete_by_list_item_id(self, list_item_id: UUID) -> bool:
        stmt = (
            delete(ProductPriceModel)
            .where(ProductPriceModel.list_item_id == list_item_id)
            .returning(ProductPriceModel.id)
        )
        result = await self._session.execute(stmt)
        deleted_id = result.scalar_one_or_none()
        await self._session.commit()
        return deleted_id is not None

    async def latest_for_product(self, product_id: UUID) -> PriceEntry | None:
        stmt = (
            select(ProductPriceModel)
            .where(ProductPriceModel.product_id == product_id)
            .order_by(
                ProductPriceModel.recorded_on.desc(),
                ProductPriceModel.created_at.desc(),
                ProductPriceModel.id.asc(),
            )
            .limit(1)
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return _price_entry_to_domain(model) if model is not None else None

    async def list_in_range(
        self,
        product_id: UUID,
        from_date: date,
        to_date: date,
    ) -> list[PriceEntry]:
        stmt = (
            select(ProductPriceModel)
            .where(
                ProductPriceModel.product_id == product_id,
                ProductPriceModel.recorded_on >= from_date,
                ProductPriceModel.recorded_on <= to_date,
            )
            .order_by(
                ProductPriceModel.recorded_on.desc(),
                ProductPriceModel.created_at.desc(),
                ProductPriceModel.id.asc(),
            )
        )
        result = await self._session.execute(stmt)
        return [_price_entry_to_domain(model) for model in result.scalars().all()]

    async def list_purchase_refs_for_active_products(self) -> list[PurchaseEntryRef]:
        stmt = (
            select(
                ProductPriceModel.product_id,
                ProductPriceModel.recorded_on,
                ProductPriceModel.list_item_id,
            )
            .join(ProductModel, ProductPriceModel.product_id == ProductModel.id)
            .where(
                ProductModel.archived_at.is_(None),
                ProductPriceModel.source == PriceSource.purchase.value,
                ProductPriceModel.list_item_id.is_not(None),
            )
        )
        result = await self._session.execute(stmt)
        return [
            PurchaseEntryRef(
                product_id=product_id,
                recorded_on=recorded_on,
                list_item_id=list_item_id,
            )
            for product_id, recorded_on, list_item_id in result.all()
            if list_item_id is not None
        ]

    async def list_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[PriceEntry]:
        stmt = select(ProductPriceModel).where(ProductPriceModel.updated_at <= until)
        if since is not None:
            stmt = stmt.where(ProductPriceModel.updated_at > since)
        stmt = stmt.order_by(ProductPriceModel.updated_at.asc(), ProductPriceModel.id.asc())
        result = await self._session.execute(stmt)
        return [_price_entry_to_domain(model) for model in result.scalars().all()]
