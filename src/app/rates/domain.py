from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

CARACAS_TZ = ZoneInfo("America/Caracas")


def caracas_date(ts: datetime) -> date:
    """Convert an aware timestamp to its calendar date in America/Caracas."""
    return ts.astimezone(CARACAS_TZ).date()


class RateKind(StrEnum):
    """Supported exchange rate kinds."""

    bcv = "bcv"
    usdt = "usdt"


class RateOrigin(StrEnum):
    """Origin that produced an exchange rate record."""

    bcv_html = "bcv_html"
    dolarapi = "dolarapi"
    binance_p2p = "binance_p2p"
    manual = "manual"


class RateStatus(StrEnum):
    """Verification status of an exchange rate record."""

    confirmed = "confirmed"
    in_review = "in_review"


@dataclass(frozen=True, slots=True)
class ExchangeRate:
    """Domain entity representing a stored exchange rate snapshot."""

    id: UUID
    kind: RateKind
    value: Decimal
    value_date: date
    origin: RateOrigin
    status: RateStatus
    fetched_at: datetime


@dataclass(frozen=True, slots=True)
class Quote:
    """Raw quote returned by an external rate source."""

    value: Decimal
    value_date: date


class QuoteSource(Protocol):
    """Port for fetching a single quote from an external source."""

    async def fetch(self) -> Quote: ...


@dataclass(frozen=True, slots=True)
class RateSources:
    """Grouped quote sources injected into RatesService."""

    bcv_html: QuoteSource
    dolarapi: QuoteSource
    binance: QuoteSource


class ExchangeRateRepository(Protocol):
    """Persistence port for exchange rate entities."""

    async def add(self, rate: ExchangeRate) -> ExchangeRate: ...

    async def get_by_id(self, rate_id: UUID) -> ExchangeRate | None: ...

    async def get_bcv_for_date(self, value_date: date) -> ExchangeRate | None: ...

    async def latest_bcv_in_force(self, today: date) -> ExchangeRate | None: ...

    async def latest_usdt_in_force(self, on: date) -> ExchangeRate | None: ...

    async def latest(self, kind: RateKind) -> ExchangeRate | None: ...

    async def history(
        self,
        kind: RateKind,
        from_date: date,
        to_date: date,
    ) -> list[ExchangeRate]: ...

    async def list_in_review(self) -> list[ExchangeRate]: ...

    async def update(self, rate: ExchangeRate) -> ExchangeRate: ...
