from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from app.sync.domain import (
    SYNC_SAFETY_LAG,
    SyncChangesResult,
    SyncCollaborators,
    SyncEntity,
    Tombstone,
)
from app.sync.errors import InvalidSyncCursor


class _SyncRow(Protocol):
    @property
    def id(self) -> UUID: ...

    @property
    def updated_at(self) -> datetime: ...


def _parse_utc_cursor(raw: str) -> datetime:
    if not raw or raw != raw.strip():
        raise InvalidSyncCursor("Cursor 'since' must be a valid ISO-8601 UTC timestamp.")
    if ("T" not in raw and "t" not in raw) or not raw.endswith(("Z", "z", "+00:00")):
        raise InvalidSyncCursor("Cursor 'since' must be a valid ISO-8601 UTC timestamp.")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise InvalidSyncCursor("Cursor 'since' must be a valid ISO-8601 UTC timestamp.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise InvalidSyncCursor("Cursor 'since' must include an explicit UTC offset.")
    return parsed.astimezone(UTC)


def _filter_rows_against_tombstones[T: _SyncRow](
    entity: SyncEntity,
    rows: list[T],
    tombstones_by_key: dict[tuple[SyncEntity, UUID], Tombstone],
    superseded_tombstones: set[tuple[SyncEntity, UUID]],
) -> list[T]:
    kept: list[T] = []
    for row in rows:
        key = (entity, row.id)
        tombstone = tombstones_by_key.get(key)
        if tombstone is not None:
            if tombstone.deleted_at > row.updated_at:
                continue
            superseded_tombstones.add(key)
        kept.append(row)
    return kept


class SyncService:
    """Application service producing consistent incremental sync snapshots."""

    def __init__(self, collaborators: SyncCollaborators) -> None:
        self._income = collaborators.income
        self._expenses = collaborators.expenses
        self._products = collaborators.products
        self._shopping = collaborators.shopping
        self._goals = collaborators.goals
        self._tombstones = collaborators.tombstones
        self._clock = collaborators.clock

    async def changes(self, since: str | None = None) -> SyncChangesResult:
        """Return all entity changes and tombstones in (since, until] with a safety lag."""
        until = self._clock.now() - SYNC_SAFETY_LAG
        parsed_since: datetime | None = None
        if since is not None:
            parsed_since = _parse_utc_cursor(since)
            if parsed_since > until:
                raise InvalidSyncCursor("Cursor 'since' cannot be later than the sync window end.")

        income_rules = await self._income.list_rules_changed_between(parsed_since, until)
        income_occurrences = await self._income.list_occurrences_changed_between(
            parsed_since,
            until,
        )
        expense_categories = await self._expenses.list_categories_changed_between(
            parsed_since,
            until,
        )
        expenses = await self._expenses.list_expenses_changed_between(parsed_since, until)
        products = await self._products.list_products_changed_between(parsed_since, until)
        product_prices = await self._products.list_prices_changed_between(parsed_since, until)
        shopping_lists = await self._shopping.list_lists_changed_between(parsed_since, until)
        shopping_list_items = await self._shopping.list_items_changed_between(parsed_since, until)
        goals = await self._goals.list_goals_changed_between(parsed_since, until)
        goal_contributions = await self._goals.list_contributions_changed_between(
            parsed_since,
            until,
        )
        raw_tombstones = await self._tombstones.list_changed_between(parsed_since, until)

        tombstones_by_key = {(t.entity, t.id): t for t in raw_tombstones}
        superseded: set[tuple[SyncEntity, UUID]] = set()

        return SyncChangesResult(
            next_cursor=until,
            income_rules=_filter_rows_against_tombstones(
                SyncEntity.income_rules,
                income_rules,
                tombstones_by_key,
                superseded,
            ),
            income_occurrences=_filter_rows_against_tombstones(
                SyncEntity.income_occurrences,
                income_occurrences,
                tombstones_by_key,
                superseded,
            ),
            expense_categories=_filter_rows_against_tombstones(
                SyncEntity.expense_categories,
                expense_categories,
                tombstones_by_key,
                superseded,
            ),
            expenses=_filter_rows_against_tombstones(
                SyncEntity.expenses,
                expenses,
                tombstones_by_key,
                superseded,
            ),
            products=_filter_rows_against_tombstones(
                SyncEntity.products,
                products,
                tombstones_by_key,
                superseded,
            ),
            product_prices=_filter_rows_against_tombstones(
                SyncEntity.product_prices,
                product_prices,
                tombstones_by_key,
                superseded,
            ),
            shopping_lists=_filter_rows_against_tombstones(
                SyncEntity.shopping_lists,
                shopping_lists,
                tombstones_by_key,
                superseded,
            ),
            shopping_list_items=_filter_rows_against_tombstones(
                SyncEntity.shopping_list_items,
                shopping_list_items,
                tombstones_by_key,
                superseded,
            ),
            goals=_filter_rows_against_tombstones(
                SyncEntity.goals,
                goals,
                tombstones_by_key,
                superseded,
            ),
            goal_contributions=_filter_rows_against_tombstones(
                SyncEntity.goal_contributions,
                goal_contributions,
                tombstones_by_key,
                superseded,
            ),
            tombstones=[t for t in raw_tombstones if (t.entity, t.id) not in superseded],
        )
