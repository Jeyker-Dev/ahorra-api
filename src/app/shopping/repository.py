from collections import defaultdict
from decimal import Decimal
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.shopping.domain import (
    Currency,
    ItemStatus,
    ListStatus,
    ShoppingList,
    ShoppingListItem,
)
from app.shopping.errors import DuplicateListItem, ListItemNotFound, ShoppingListNotFound
from app.shopping.models import ShoppingListItemModel, ShoppingListModel

_LIST_PRODUCT_UNIQUE_INDEX = "uq_shopping_list_items_list_product"


def _list_to_domain(model: ShoppingListModel) -> ShoppingList:
    return ShoppingList(
        id=model.id,
        name=model.name,
        status=ListStatus(model.status),
        closed_at=model.closed_at,
        expense_id=model.expense_id,
        deleted_at=model.deleted_at,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _item_to_domain(model: ShoppingListItemModel) -> ShoppingListItem:
    return ShoppingListItem(
        id=model.id,
        list_id=model.list_id,
        product_id=model.product_id,
        quantity=model.quantity,
        status=ItemStatus(model.status),
        bought_price=model.bought_price,
        bought_currency=(
            Currency(model.bought_currency) if model.bought_currency is not None else None
        ),
        bought_at=model.bought_at,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


class SqlAlchemyShoppingListRepository:
    """SQLAlchemy implementation of ShoppingListRepository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, list_id: UUID) -> ShoppingList | None:
        stmt = select(ShoppingListModel).where(ShoppingListModel.id == list_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return _list_to_domain(model) if model is not None else None

    async def list_non_deleted(self, status: ListStatus | None = None) -> list[ShoppingList]:
        stmt = select(ShoppingListModel).where(ShoppingListModel.deleted_at.is_(None))
        if status is not None:
            stmt = stmt.where(ShoppingListModel.status == status.value)
        stmt = stmt.order_by(ShoppingListModel.created_at.desc(), ShoppingListModel.id.asc())
        result = await self._session.execute(stmt)
        return [_list_to_domain(model) for model in result.scalars().all()]

    async def insert_if_absent(self, shopping_list: ShoppingList) -> tuple[ShoppingList, bool]:
        stmt = (
            pg_insert(ShoppingListModel)
            .values(
                id=shopping_list.id,
                name=shopping_list.name,
                status=shopping_list.status.value,
                closed_at=shopping_list.closed_at,
                expense_id=shopping_list.expense_id,
                deleted_at=shopping_list.deleted_at,
                created_at=shopping_list.created_at,
                updated_at=shopping_list.updated_at,
            )
            .on_conflict_do_nothing(index_elements=[ShoppingListModel.id])
            .returning(ShoppingListModel.id)
        )
        result = await self._session.execute(stmt)
        inserted_id = result.scalar_one_or_none()
        await self._session.commit()
        if inserted_id is not None:
            return shopping_list, True

        existing = await self.get_by_id(shopping_list.id)
        assert existing is not None
        return existing, False

    async def update(self, shopping_list: ShoppingList) -> ShoppingList:
        model = await self._session.get(ShoppingListModel, shopping_list.id)
        if model is None:
            raise ShoppingListNotFound(f"Shopping list {shopping_list.id} was not found.")
        model.name = shopping_list.name
        model.status = shopping_list.status.value
        model.closed_at = shopping_list.closed_at
        model.expense_id = shopping_list.expense_id
        model.deleted_at = shopping_list.deleted_at
        model.updated_at = shopping_list.updated_at
        await self._session.commit()
        return _list_to_domain(model)


class SqlAlchemyShoppingListItemRepository:
    """SQLAlchemy implementation of ShoppingListItemRepository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, item_id: UUID) -> ShoppingListItem | None:
        stmt = select(ShoppingListItemModel).where(ShoppingListItemModel.id == item_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return _item_to_domain(model) if model is not None else None

    async def get_by_list_and_product(
        self,
        list_id: UUID,
        product_id: UUID,
    ) -> ShoppingListItem | None:
        stmt = select(ShoppingListItemModel).where(
            ShoppingListItemModel.list_id == list_id,
            ShoppingListItemModel.product_id == product_id,
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return _item_to_domain(model) if model is not None else None

    async def list_for_list(self, list_id: UUID) -> list[ShoppingListItem]:
        stmt = (
            select(ShoppingListItemModel)
            .where(ShoppingListItemModel.list_id == list_id)
            .order_by(ShoppingListItemModel.created_at.asc(), ShoppingListItemModel.id.asc())
        )
        result = await self._session.execute(stmt)
        return [_item_to_domain(model) for model in result.scalars().all()]

    async def insert_if_absent(self, item: ShoppingListItem) -> tuple[ShoppingListItem, bool]:
        stmt = (
            pg_insert(ShoppingListItemModel)
            .values(
                id=item.id,
                list_id=item.list_id,
                product_id=item.product_id,
                quantity=item.quantity,
                status=item.status.value,
                bought_price=item.bought_price,
                bought_currency=(
                    item.bought_currency.value if item.bought_currency is not None else None
                ),
                bought_at=item.bought_at,
                created_at=item.created_at,
                updated_at=item.updated_at,
            )
            .on_conflict_do_nothing(index_elements=[ShoppingListItemModel.id])
            .returning(ShoppingListItemModel.id)
        )
        try:
            result = await self._session.execute(stmt)
            inserted_id = result.scalar_one_or_none()
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            if _LIST_PRODUCT_UNIQUE_INDEX in str(exc):
                raise DuplicateListItem(
                    f"Product {item.product_id} already exists on shopping list {item.list_id}."
                ) from exc
            raise

        if inserted_id is not None:
            return item, True

        existing = await self.get_by_id(item.id)
        assert existing is not None
        return existing, False

    async def update(self, item: ShoppingListItem) -> ShoppingListItem:
        model = await self._session.get(ShoppingListItemModel, item.id)
        if model is None:
            raise ListItemNotFound(f"Shopping list item {item.id} was not found.")
        model.product_id = item.product_id
        model.quantity = item.quantity
        model.status = item.status.value
        model.bought_price = item.bought_price
        model.bought_currency = (
            item.bought_currency.value if item.bought_currency is not None else None
        )
        model.bought_at = item.bought_at
        model.updated_at = item.updated_at
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            if _LIST_PRODUCT_UNIQUE_INDEX in str(exc):
                raise DuplicateListItem(
                    f"Product {item.product_id} already exists on shopping list {item.list_id}."
                ) from exc
            raise
        return _item_to_domain(model)

    async def delete_by_id(self, item_id: UUID) -> None:
        stmt = delete(ShoppingListItemModel).where(ShoppingListItemModel.id == item_id)
        await self._session.execute(stmt)
        await self._session.commit()

    async def list_non_deleted_item_ids(self) -> set[UUID]:
        stmt = (
            select(ShoppingListItemModel.id)
            .join(ShoppingListModel, ShoppingListItemModel.list_id == ShoppingListModel.id)
            .where(ShoppingListModel.deleted_at.is_(None))
        )
        result = await self._session.execute(stmt)
        return set(result.scalars().all())

    async def list_bought_quantities_by_product(self) -> dict[UUID, list[Decimal]]:
        stmt = (
            select(ShoppingListItemModel.product_id, ShoppingListItemModel.quantity)
            .join(ShoppingListModel, ShoppingListItemModel.list_id == ShoppingListModel.id)
            .where(
                ShoppingListModel.deleted_at.is_(None),
                ShoppingListItemModel.status == ItemStatus.bought.value,
            )
        )
        result = await self._session.execute(stmt)
        grouped: dict[UUID, list[Decimal]] = defaultdict(list)
        for product_id, quantity in result.all():
            grouped[product_id].append(quantity)
        return dict(grouped)
