from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from uuid import UUID

from app.expenses.domain import (
    MAX_CATEGORY_NAME_LENGTH,
    MAX_EXPENSE_NOTE_LENGTH,
    MIN_CATEGORY_NAME_LENGTH,
    Currency,
    Expense,
    ExpenseCategory,
    ExpenseCollaborators,
    ExpenseInput,
    ExpenseSummaryItem,
    ExpenseWithCategory,
    RateSnapshot,
    UpsertCategoryResult,
    UpsertExpenseResult,
)
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
    RateNotAvailableForDate,
)

AMOUNT_QUANTUM = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class _NormalizedExpenseData:
    category_id: UUID
    amount: Decimal
    currency: Currency
    spent_on: date
    note: str | None


@dataclass(frozen=True, slots=True)
class _ExpenseRates:
    bcv: RateSnapshot
    usdt: RateSnapshot


def _quantize_amount(value: Decimal) -> Decimal:
    return value.quantize(AMOUNT_QUANTUM, rounding=ROUND_HALF_EVEN)


def _validate_category_name(raw_name: str) -> str:
    stripped = raw_name.strip()
    if len(stripped) < MIN_CATEGORY_NAME_LENGTH or len(stripped) > MAX_CATEGORY_NAME_LENGTH:
        raise InvalidCategoryName(
            f"Category name must be between {MIN_CATEGORY_NAME_LENGTH} "
            f"and {MAX_CATEGORY_NAME_LENGTH} characters."
        )
    return stripped


def _validate_expense_amount(value: Decimal) -> Decimal:
    if not value.is_finite() or value <= Decimal("0"):
        raise InvalidExpenseAmount("Expense amount must be greater than zero.")
    return _quantize_amount(value)


def _normalize_expense_note(raw_note: str | None) -> str | None:
    if raw_note is None:
        return None
    stripped = raw_note.strip()
    if not stripped:
        return None
    if len(stripped) > MAX_EXPENSE_NOTE_LENGTH:
        raise InvalidExpenseNote(
            f"Expense note must be at most {MAX_EXPENSE_NOTE_LENGTH} characters."
        )
    return stripped


def _normalize_expense_input(payload: ExpenseInput) -> _NormalizedExpenseData:
    return _NormalizedExpenseData(
        category_id=payload.category_id,
        amount=_validate_expense_amount(payload.amount),
        currency=payload.currency,
        spent_on=payload.spent_on,
        note=_normalize_expense_note(payload.note),
    )


def _matches_normalized_expense(
    existing: Expense,
    normalized: _NormalizedExpenseData,
) -> bool:
    return (
        existing.category_id == normalized.category_id
        and existing.amount == normalized.amount
        and existing.currency == normalized.currency
        and existing.spent_on == normalized.spent_on
        and existing.note == normalized.note
    )


def _validate_date_range(from_date: date, to_date: date) -> None:
    if from_date > to_date:
        raise InvalidExpensePeriod("'from' must be less than or equal to 'to'.")


class ExpenseService:
    """Application service managing expense categories, expenses, and summaries."""

    def __init__(self, collaborators: ExpenseCollaborators) -> None:
        self._categories = collaborators.categories
        self._expenses = collaborators.expenses
        self._rates = collaborators.rates
        self._clock = collaborators.clock

    async def upsert_category(self, category_id: UUID, name: str) -> UpsertCategoryResult:
        """Create or idempotently rename an expense category."""
        normalized_name = _validate_category_name(name)
        now = self._clock.now()

        existing = await self._categories.get_by_id(category_id)
        if existing is not None:
            return await self._update_existing_category(existing, normalized_name, now)

        await self._ensure_name_available(normalized_name, exclude_id=None)
        candidate = ExpenseCategory(
            id=category_id,
            name=normalized_name,
            archived_at=None,
            created_at=now,
            updated_at=now,
        )
        saved, inserted = await self._categories.insert_if_absent(candidate)
        if not inserted:
            return await self._update_existing_category(saved, normalized_name, now)
        return UpsertCategoryResult(category=saved, created=True)

    async def list_active_categories(self) -> list[ExpenseCategory]:
        """Return active categories ordered by name, then id."""
        return await self._categories.list_active()

    async def list_all_categories(self) -> list[ExpenseCategory]:
        """Return all categories, active first, then archived, ordered by name and id."""
        return await self._categories.list_all()

    async def list_categories_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[ExpenseCategory]:
        """Return expense categories changed in (since, until] ordered by (updated_at, id)."""
        return await self._categories.list_changed_between(since, until)

    async def list_expenses_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[Expense]:
        """Return raw expenses changed in (since, until] ordered by (updated_at, id)."""
        return await self._expenses.list_changed_between(since, until)

    async def archive_category(self, category_id: UUID) -> ExpenseCategory:
        """Archive a category idempotently."""
        existing = await self._require_category(category_id)
        if existing.archived_at is not None:
            return existing
        now = self._clock.now()
        return await self._categories.update(replace(existing, archived_at=now, updated_at=now))

    async def unarchive_category(self, category_id: UUID) -> ExpenseCategory:
        """Unarchive a category idempotently."""
        existing = await self._require_category(category_id)
        if existing.archived_at is None:
            return existing
        now = self._clock.now()
        return await self._categories.update(replace(existing, archived_at=None, updated_at=now))

    async def upsert_expense(self, payload: ExpenseInput) -> UpsertExpenseResult:
        """Create or idempotently update an expense with historical exchange rates."""
        normalized = _normalize_expense_input(payload)
        existing = await self._expenses.get_by_id(payload.id)
        if existing is not None and existing.deleted_at is not None:
            raise ExpenseDeleted(f"Expense {payload.id} has been deleted.")

        category = await self._require_category(normalized.category_id)
        now = self._clock.now()

        if existing is not None:
            return await self._update_existing_expense(existing, normalized, category, now)

        if category.archived_at is not None:
            raise CategoryArchived(f"Category {category.id} is archived.")

        rates = await self._lookup_rates(normalized.spent_on)
        candidate = Expense(
            id=payload.id,
            category_id=normalized.category_id,
            amount=normalized.amount,
            currency=normalized.currency,
            spent_on=normalized.spent_on,
            note=normalized.note,
            bcv_rate_value=rates.bcv.value,
            bcv_rate_date=rates.bcv.value_date,
            usdt_rate_value=rates.usdt.value,
            usdt_rate_date=rates.usdt.value_date,
            deleted_at=None,
            created_at=now,
            updated_at=now,
        )
        saved, inserted = await self._expenses.insert_if_absent(candidate)
        if not inserted:
            return await self._update_existing_expense(saved, normalized, category, now)
        return UpsertExpenseResult(
            item=ExpenseWithCategory(expense=saved, category_name=category.name),
            created=True,
        )

    async def get_expense(self, expense_id: UUID) -> ExpenseWithCategory:
        """Return a non-deleted expense with its category name or raise ExpenseNotFound."""
        expense = await self._expenses.get_by_id(expense_id)
        if expense is None or expense.deleted_at is not None:
            raise ExpenseNotFound(f"Expense {expense_id} was not found.")
        category = await self._require_category(expense.category_id)
        return ExpenseWithCategory(expense=expense, category_name=category.name)

    async def delete_expense(self, expense_id: UUID) -> ExpenseWithCategory:
        """Soft-delete an expense idempotently."""
        expense = await self._expenses.get_by_id(expense_id)
        if expense is None:
            raise ExpenseNotFound(f"Expense {expense_id} was not found.")
        category = await self._require_category(expense.category_id)
        if expense.deleted_at is not None:
            return ExpenseWithCategory(expense=expense, category_name=category.name)

        now = self._clock.now()
        saved = await self._expenses.update(replace(expense, deleted_at=now, updated_at=now))
        return ExpenseWithCategory(expense=saved, category_name=category.name)

    async def list_expenses(
        self,
        from_date: date,
        to_date: date,
        category_id: UUID | None = None,
    ) -> list[ExpenseWithCategory]:
        """List non-deleted expenses within an inclusive spent_on range."""
        _validate_date_range(from_date, to_date)
        return await self._expenses.list_in_range(from_date, to_date, category_id)

    async def summarize(self, from_date: date, to_date: date) -> list[ExpenseSummaryItem]:
        """Aggregate non-deleted expenses in USD per category over an inclusive date range."""
        _validate_date_range(from_date, to_date)
        items = await self._expenses.list_in_range(from_date, to_date)
        totals: dict[UUID, Decimal] = defaultdict(lambda: Decimal("0"))
        counts: dict[UUID, int] = defaultdict(int)
        names: dict[UUID, str] = {}

        for entry in items:
            exp = entry.expense
            usd_amount = (
                exp.amount if exp.currency == Currency.usd else exp.amount / exp.bcv_rate_value
            )
            totals[exp.category_id] += usd_amount
            counts[exp.category_id] += 1
            names[exp.category_id] = entry.category_name

        summary = [
            ExpenseSummaryItem(
                category_id=cat_id,
                category_name=names[cat_id],
                count=counts[cat_id],
                total_usd=_quantize_amount(raw_total),
            )
            for cat_id, raw_total in totals.items()
        ]
        summary.sort(key=lambda row: (-row.total_usd, row.category_name))
        return summary

    async def _require_category(self, category_id: UUID) -> ExpenseCategory:
        category = await self._categories.get_by_id(category_id)
        if category is None:
            raise CategoryNotFound(f"Expense category {category_id} was not found.")
        return category

    async def _ensure_name_available(self, name: str, *, exclude_id: UUID | None) -> None:
        existing = await self._categories.get_by_lower_name(name)
        if existing is not None and existing.id != exclude_id:
            raise CategoryNameTaken(f"Category name {name!r} is already taken.")

    async def _update_existing_category(
        self,
        existing: ExpenseCategory,
        normalized_name: str,
        now: datetime,
    ) -> UpsertCategoryResult:
        if existing.name == normalized_name:
            return UpsertCategoryResult(category=existing, created=False)
        await self._ensure_name_available(normalized_name, exclude_id=existing.id)
        updated = replace(existing, name=normalized_name, updated_at=now)
        saved = await self._categories.update(updated)
        return UpsertCategoryResult(category=saved, created=False)

    async def _update_existing_expense(
        self,
        existing: Expense,
        normalized: _NormalizedExpenseData,
        category: ExpenseCategory,
        now: datetime,
    ) -> UpsertExpenseResult:
        if existing.deleted_at is not None:
            raise ExpenseDeleted(f"Expense {existing.id} has been deleted.")
        if _matches_normalized_expense(existing, normalized):
            return UpsertExpenseResult(
                item=ExpenseWithCategory(expense=existing, category_name=category.name),
                created=False,
            )
        if existing.category_id != normalized.category_id and category.archived_at is not None:
            raise CategoryArchived(f"Category {category.id} is archived.")

        rates = (
            await self._lookup_rates(normalized.spent_on)
            if existing.spent_on != normalized.spent_on
            else _ExpenseRates(
                bcv=RateSnapshot(existing.bcv_rate_value, existing.bcv_rate_date),
                usdt=RateSnapshot(existing.usdt_rate_value, existing.usdt_rate_date),
            )
        )
        updated = replace(
            existing,
            category_id=normalized.category_id,
            amount=normalized.amount,
            currency=normalized.currency,
            spent_on=normalized.spent_on,
            note=normalized.note,
            bcv_rate_value=rates.bcv.value,
            bcv_rate_date=rates.bcv.value_date,
            usdt_rate_value=rates.usdt.value,
            usdt_rate_date=rates.usdt.value_date,
            updated_at=now,
        )
        saved = await self._expenses.update(updated)
        return UpsertExpenseResult(
            item=ExpenseWithCategory(expense=saved, category_name=category.name),
            created=False,
        )

    async def _lookup_rates(self, spent_on: date) -> _ExpenseRates:
        bcv = await self._rates.bcv_in_force(spent_on)
        if bcv is None:
            raise RateNotAvailableForDate(
                f"No BCV exchange rate is available for {spent_on.isoformat()}."
            )
        usdt = await self._rates.usdt_in_force(spent_on)
        if usdt is None:
            raise RateNotAvailableForDate(
                f"No USDT exchange rate is available for {spent_on.isoformat()}."
            )
        return _ExpenseRates(bcv=bcv, usdt=usdt)
