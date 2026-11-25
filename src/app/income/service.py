from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from uuid import UUID, uuid4

from app.clock import caracas_date
from app.income.domain import (
    MAX_RULE_NAME_LENGTH,
    MIN_RULE_NAME_LENGTH,
    Currency,
    IncomeCollaborators,
    IncomeOccurrence,
    IncomeRule,
    IncomeRuleInput,
    OccurrenceStatus,
    OccurrenceWithRule,
    UpsertRuleResult,
    add_months,
    normalize_days,
    occurrence_dates,
)
from app.income.errors import (
    IncomeRuleEnded,
    IncomeRuleNotFound,
    InvalidIncomeAmount,
    InvalidIncomeName,
    InvalidIncomePeriod,
    OccurrenceNotFound,
    OccurrenceNotReceived,
    RateNotAvailableForDate,
)

AMOUNT_QUANTUM = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class _NormalizedRuleData:
    name: str
    amount: Decimal
    currency: Currency
    days: tuple[int, ...]
    start_date: date
    end_date: date | None


def _quantize_amount(value: Decimal) -> Decimal:
    return value.quantize(AMOUNT_QUANTUM, rounding=ROUND_HALF_EVEN)


def _validate_name(raw_name: str) -> str:
    stripped = raw_name.strip()
    if len(stripped) < MIN_RULE_NAME_LENGTH or len(stripped) > MAX_RULE_NAME_LENGTH:
        raise InvalidIncomeName(
            f"Income rule name must be between {MIN_RULE_NAME_LENGTH} "
            f"and {MAX_RULE_NAME_LENGTH} characters."
        )
    return stripped


def _validate_positive_amount(value: Decimal) -> Decimal:
    if not value.is_finite() or value <= Decimal("0"):
        raise InvalidIncomeAmount("Income amount must be greater than zero.")
    return _quantize_amount(value)


def _normalize_rule_input(payload: IncomeRuleInput) -> _NormalizedRuleData:
    name = _validate_name(payload.name)
    amount = _validate_positive_amount(payload.amount)
    days = normalize_days(payload.days)
    if payload.end_date is not None and payload.end_date < payload.start_date:
        raise InvalidIncomePeriod("Income rule end_date must be on or after start_date.")
    return _NormalizedRuleData(
        name=name,
        amount=amount,
        currency=payload.currency,
        days=days,
        start_date=payload.start_date,
        end_date=payload.end_date,
    )


def _matches_normalized(existing: IncomeRule, normalized: _NormalizedRuleData) -> bool:
    return (
        existing.name == normalized.name
        and existing.amount == normalized.amount
        and existing.currency == normalized.currency
        and existing.days == normalized.days
        and existing.start_date == normalized.start_date
        and existing.end_date == normalized.end_date
    )


def _build_expected_occurrence(
    rule: IncomeRule,
    expected_date: date,
    now: datetime,
) -> IncomeOccurrence:
    return IncomeOccurrence(
        id=uuid4(),
        rule_id=rule.id,
        expected_date=expected_date,
        expected_amount=rule.amount,
        currency=rule.currency,
        status=OccurrenceStatus.expected,
        received_date=None,
        received_amount=None,
        rate_value=None,
        rate_date=None,
        received_at=None,
        updated_at=now,
    )


class IncomeService:
    """Application service managing recurring income rules and occurrences."""

    def __init__(
        self,
        collaborators: IncomeCollaborators,
        horizon_months: int,
    ) -> None:
        self._rules = collaborators.rules
        self._occurrences = collaborators.occurrences
        self._rates = collaborators.rates
        self._clock = collaborators.clock
        self._horizon_months = horizon_months

    async def upsert_rule(self, payload: IncomeRuleInput) -> UpsertRuleResult:
        """Create or idempotently update an income rule and generate expected occurrences."""
        normalized = _normalize_rule_input(payload)
        now = self._clock.now()
        today = caracas_date(now)
        horizon_end = add_months(today, self._horizon_months)

        existing = await self._rules.get_by_id(payload.id)
        if existing is not None:
            return await self._update_existing_rule(existing, normalized, now, horizon_end)

        candidate = IncomeRule(
            id=payload.id,
            name=normalized.name,
            amount=normalized.amount,
            currency=normalized.currency,
            days=normalized.days,
            start_date=normalized.start_date,
            end_date=normalized.end_date,
            ended_at=None,
            created_at=now,
            updated_at=now,
        )
        saved_rule, inserted = await self._rules.insert_if_absent(candidate)
        if not inserted:
            return await self._update_existing_rule(saved_rule, normalized, now, horizon_end)

        await self._generate_missing_for_rule(saved_rule, saved_rule.start_date, horizon_end, now)
        return UpsertRuleResult(rule=saved_rule, created=True)

    async def list_rules(self) -> list[IncomeRule]:
        """Return all income rules ordered active first, then ended, by name."""
        return await self._rules.list_all()

    async def list_rules_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[IncomeRule]:
        """Return income rules changed in (since, until] ordered by (updated_at, id)."""
        return await self._rules.list_changed_between(since, until)

    async def list_occurrences_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[IncomeOccurrence]:
        """Return income occurrences changed in (since, until] ordered by (updated_at, id)."""
        return await self._occurrences.list_changed_between(since, until)

    async def get_rule(self, rule_id: UUID) -> IncomeRule:
        """Return a single income rule or raise IncomeRuleNotFound."""
        rule = await self._rules.get_by_id(rule_id)
        if rule is None:
            raise IncomeRuleNotFound(f"Income rule {rule_id} was not found.")
        return rule

    async def end_rule(self, rule_id: UUID) -> IncomeRule:
        """End an income rule and remove future expected occurrences idempotently."""
        existing = await self.get_rule(rule_id)
        if existing.ended_at is not None:
            return existing

        now = self._clock.now()
        today = caracas_date(now)
        ended = replace(existing, ended_at=now, updated_at=now)
        saved = await self._rules.update(ended)
        await self._occurrences.delete_future_expected(saved.id, today)
        return saved

    async def generate_occurrences(self) -> int:
        """Extend expected occurrences for all active rules up to the configured horizon."""
        now = self._clock.now()
        today = caracas_date(now)
        horizon_end = add_months(today, self._horizon_months)
        active_rules = await self._rules.list_active(today)

        created_count = 0
        for rule in active_rules:
            created_count += await self._generate_missing_for_rule(
                rule,
                max(rule.start_date, today),
                horizon_end,
                now,
            )
        return created_count

    async def list_occurrences(
        self,
        from_date: date,
        to_date: date,
        status: OccurrenceStatus | None = None,
    ) -> list[OccurrenceWithRule]:
        """List occurrences within an inclusive expected_date range."""
        if from_date > to_date:
            raise InvalidIncomePeriod("'from' must be less than or equal to 'to'.")
        return await self._occurrences.list_in_range(from_date, to_date, status)

    async def receive(
        self,
        occurrence_id: UUID,
        received_amount: Decimal | None = None,
        received_date: date | None = None,
    ) -> IncomeOccurrence:
        """Mark or correct an occurrence as received using the BCV rate in force."""
        validated_amount = (
            _validate_positive_amount(received_amount) if received_amount is not None else None
        )
        existing = await self._occurrences.get_by_id(occurrence_id)
        if existing is None:
            raise OccurrenceNotFound(f"Income occurrence {occurrence_id} was not found.")

        target_amount = (
            validated_amount if validated_amount is not None else existing.expected_amount
        )
        target_date = received_date if received_date is not None else existing.expected_date
        rate = await self._rates.bcv_in_force(target_date)
        if rate is None:
            raise RateNotAvailableForDate(
                f"No BCV exchange rate is available for {target_date.isoformat()}."
            )

        now = self._clock.now()
        updated = replace(
            existing,
            status=OccurrenceStatus.received,
            received_date=target_date,
            received_amount=target_amount,
            rate_value=rate.value,
            rate_date=rate.value_date,
            received_at=now,
            updated_at=now,
        )
        return await self._occurrences.update(updated)

    async def unreceive(self, occurrence_id: UUID) -> IncomeOccurrence:
        """Revert a received occurrence back to expected status."""
        existing = await self._occurrences.get_by_id(occurrence_id)
        if existing is None:
            raise OccurrenceNotFound(f"Income occurrence {occurrence_id} was not found.")
        if existing.status != OccurrenceStatus.received:
            raise OccurrenceNotReceived(f"Income occurrence {occurrence_id} is not received.")

        updated = replace(
            existing,
            status=OccurrenceStatus.expected,
            received_date=None,
            received_amount=None,
            rate_value=None,
            rate_date=None,
            received_at=None,
            updated_at=self._clock.now(),
        )
        return await self._occurrences.update(updated)

    async def _update_existing_rule(
        self,
        existing: IncomeRule,
        normalized: _NormalizedRuleData,
        now: datetime,
        horizon_end: date,
    ) -> UpsertRuleResult:
        if existing.ended_at is not None:
            raise IncomeRuleEnded(f"Income rule {existing.id} has already ended.")
        if _matches_normalized(existing, normalized):
            return UpsertRuleResult(rule=existing, created=False)

        updated_rule = replace(
            existing,
            name=normalized.name,
            amount=normalized.amount,
            currency=normalized.currency,
            days=normalized.days,
            start_date=normalized.start_date,
            end_date=normalized.end_date,
            updated_at=now,
        )
        saved_rule = await self._rules.update(updated_rule)
        today = caracas_date(now)
        await self._occurrences.delete_future_expected(saved_rule.id, today)
        regen_start = max(today, saved_rule.start_date)
        await self._generate_missing_for_rule(saved_rule, regen_start, horizon_end, now)
        return UpsertRuleResult(rule=saved_rule, created=False)

    async def _generate_missing_for_rule(
        self,
        rule: IncomeRule,
        window_start: date,
        horizon_end: date,
        now: datetime,
    ) -> int:
        dates = occurrence_dates(rule, window_start, horizon_end)
        if not dates:
            return 0
        existing_dates = await self._occurrences.list_dates_for_rule(rule.id)
        missing_dates = [
            expected_date for expected_date in dates if expected_date not in existing_dates
        ]
        if not missing_dates:
            return 0
        new_occurrences = [
            _build_expected_occurrence(rule, expected_date, now) for expected_date in missing_dates
        ]
        return await self._occurrences.add_many_ignore_conflicts(new_occurrences)
