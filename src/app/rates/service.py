import logging
from dataclasses import replace
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from uuid import UUID, uuid4

from app.clock import Clock
from app.rates.domain import (
    ExchangeRate,
    ExchangeRateRepository,
    Quote,
    QuoteSource,
    RateKind,
    RateOrigin,
    RateSources,
    RateStatus,
    caracas_date,
)
from app.rates.errors import (
    InvalidRateValue,
    QuoteUnavailable,
    RateNotFound,
    RateNotInReview,
    RateUnavailable,
)

logger = logging.getLogger(__name__)

RATE_QUANTUM = Decimal("0.00000001")
PERCENT_SCALE = Decimal("100")

_STATUS_RANK: dict[RateStatus, int] = {
    RateStatus.in_review: 0,
    RateStatus.confirmed: 1,
}
_ORIGIN_RANK: dict[RateOrigin, int] = {
    RateOrigin.dolarapi: 0,
    RateOrigin.binance_p2p: 0,
    RateOrigin.bcv_html: 1,
    RateOrigin.manual: 2,
}


def _quantize_rate(value: Decimal) -> Decimal:
    return value.quantize(RATE_QUANTUM, rounding=ROUND_HALF_EVEN)


def _validate_positive(value: Decimal) -> Decimal:
    if not value.is_finite() or value <= Decimal("0"):
        raise InvalidRateValue("Exchange rate value must be greater than zero.")
    return _quantize_rate(value)


def _bcv_rank(status: RateStatus, origin: RateOrigin) -> tuple[int, int]:
    return (_STATUS_RANK[status], _ORIGIN_RANK[origin])


async def _try_fetch(source: QuoteSource, label: str) -> Quote | None:
    try:
        return await source.fetch()
    except QuoteUnavailable as exc:
        logger.warning("Quote source %s unavailable: %s", label, exc)
        return None


def _resolve_bcv_candidate(
    html_quote: Quote | None,
    api_quote: Quote | None,
    tolerance: Decimal,
) -> tuple[Decimal, date, RateOrigin, RateStatus]:
    if html_quote is None and api_quote is None:
        logger.error("All BCV rate sources failed.")
        raise RateUnavailable("All BCV rate sources are unavailable.")

    if html_quote is None:
        assert api_quote is not None
        logger.warning("BCV HTML unavailable; saving dolarapi quote for review.")
        return (api_quote.value, api_quote.value_date, RateOrigin.dolarapi, RateStatus.in_review)

    if api_quote is None or api_quote.value_date != html_quote.value_date:
        logger.warning("BCV cross-check unavailable or date mismatch; trusting BCV HTML.")
        return (html_quote.value, html_quote.value_date, RateOrigin.bcv_html, RateStatus.confirmed)

    diff_percent = abs(html_quote.value - api_quote.value) / html_quote.value * PERCENT_SCALE
    if diff_percent <= tolerance:
        return (html_quote.value, html_quote.value_date, RateOrigin.bcv_html, RateStatus.confirmed)

    logger.warning(
        "BCV HTML (%s) and dolarapi (%s) differ by %.4f%% (> %s%%); marking in_review.",
        html_quote.value,
        api_quote.value,
        diff_percent,
        tolerance,
    )
    return (html_quote.value, html_quote.value_date, RateOrigin.bcv_html, RateStatus.in_review)


class RatesService:
    """Application service managing exchange rate fetching, review, and queries."""

    def __init__(
        self,
        repository: ExchangeRateRepository,
        sources: RateSources,
        clock: Clock,
        tolerance: Decimal,
    ) -> None:
        self._repository = repository
        self._sources = sources
        self._clock = clock
        self._tolerance = tolerance

    async def fetch_bcv(self) -> ExchangeRate:
        """Fetch BCV quotes, cross-check against tolerance, and persist or upgrade the day's row."""
        html_quote = await _try_fetch(self._sources.bcv_html, "bcv_html")
        api_quote = await _try_fetch(self._sources.dolarapi, "dolarapi")
        raw_value, value_date, origin, status = _resolve_bcv_candidate(
            html_quote,
            api_quote,
            self._tolerance,
        )
        return await self._save_bcv_rate(
            value=_quantize_rate(raw_value),
            value_date=value_date,
            origin=origin,
            status=status,
        )

    async def fetch_usdt(self) -> ExchangeRate:
        """Fetch the Binance P2P median USDT/VES rate and persist it."""
        try:
            quote = await self._sources.binance.fetch()
        except QuoteUnavailable as exc:
            logger.error("Binance P2P source unavailable: %s", exc)
            raise RateUnavailable("USDT rate source is unavailable.") from exc

        rate = ExchangeRate(
            id=uuid4(),
            kind=RateKind.usdt,
            value=_quantize_rate(quote.value),
            value_date=quote.value_date,
            origin=RateOrigin.binance_p2p,
            status=RateStatus.confirmed,
            fetched_at=self._clock.now(),
        )
        return await self._repository.add(rate)

    async def set_manual(
        self,
        kind: RateKind,
        value: Decimal,
        value_date: date,
    ) -> ExchangeRate:
        """Persist a manually supplied confirmed rate, replacing any existing BCV row."""
        quantized = _validate_positive(value)
        now = self._clock.now()
        if kind == RateKind.bcv:
            existing = await self._repository.get_bcv_for_date(value_date)
            if existing is not None:
                updated = replace(
                    existing,
                    value=quantized,
                    origin=RateOrigin.manual,
                    status=RateStatus.confirmed,
                    fetched_at=now,
                )
                return await self._repository.update(updated)

        rate = ExchangeRate(
            id=uuid4(),
            kind=kind,
            value=quantized,
            value_date=value_date,
            origin=RateOrigin.manual,
            status=RateStatus.confirmed,
            fetched_at=now,
        )
        return await self._repository.add(rate)

    async def confirm(self, rate_id: UUID) -> ExchangeRate:
        """Confirm an in_review exchange rate."""
        existing = await self._require_in_review(rate_id)
        return await self._repository.update(replace(existing, status=RateStatus.confirmed))

    async def correct(self, rate_id: UUID, value: Decimal) -> ExchangeRate:
        """Correct an in_review exchange rate with a manual confirmed value."""
        quantized = _validate_positive(value)
        existing = await self._require_in_review(rate_id)
        updated = replace(
            existing,
            value=quantized,
            origin=RateOrigin.manual,
            status=RateStatus.confirmed,
        )
        return await self._repository.update(updated)

    async def get_latest(self) -> tuple[ExchangeRate | None, ExchangeRate | None]:
        """Return the BCV rate in force today in Caracas and the latest USDT rate."""
        today = caracas_date(self._clock.now())
        bcv = await self._repository.latest_bcv_in_force(today)
        usdt = await self._repository.latest(RateKind.usdt)
        return bcv, usdt

    async def get_history(
        self,
        kind: RateKind,
        from_date: date,
        to_date: date,
    ) -> list[ExchangeRate]:
        """Return exchange rates for a kind within an inclusive value_date range."""
        return await self._repository.history(kind=kind, from_date=from_date, to_date=to_date)

    async def list_in_review(self) -> list[ExchangeRate]:
        """Return all exchange rates currently awaiting review."""
        return await self._repository.list_in_review()

    async def _require_in_review(self, rate_id: UUID) -> ExchangeRate:
        existing = await self._repository.get_by_id(rate_id)
        if existing is None:
            raise RateNotFound(f"Exchange rate {rate_id} was not found.")
        if existing.status != RateStatus.in_review:
            raise RateNotInReview(f"Exchange rate {rate_id} is not in review.")
        return existing

    async def _save_bcv_rate(
        self,
        *,
        value: Decimal,
        value_date: date,
        origin: RateOrigin,
        status: RateStatus,
    ) -> ExchangeRate:
        now = self._clock.now()
        existing = await self._repository.get_bcv_for_date(value_date)
        if existing is None:
            return await self._repository.add(
                ExchangeRate(
                    id=uuid4(),
                    kind=RateKind.bcv,
                    value=value,
                    value_date=value_date,
                    origin=origin,
                    status=status,
                    fetched_at=now,
                )
            )

        if existing.status == RateStatus.confirmed and existing.origin in (
            RateOrigin.bcv_html,
            RateOrigin.manual,
        ):
            return existing

        if _bcv_rank(status, origin) > _bcv_rank(existing.status, existing.origin):
            updated = replace(
                existing,
                value=value,
                origin=origin,
                status=status,
                fetched_at=now,
            )
            return await self._repository.update(updated)

        return existing
