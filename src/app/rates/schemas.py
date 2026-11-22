from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, Field, PlainSerializer, model_validator

from app.rates.domain import ExchangeRate, RateKind, RateOrigin, RateStatus

DecimalStr = Annotated[
    Decimal,
    PlainSerializer(str, return_type=str, when_used="json"),
]


class RateOut(BaseModel):
    """Public HTTP representation of an exchange rate."""

    id: UUID
    kind: RateKind
    value: DecimalStr
    value_date: date
    origin: RateOrigin
    status: RateStatus
    fetched_at: datetime

    @classmethod
    def from_domain(cls, rate: ExchangeRate) -> Self:
        """Build an HTTP response model from a domain ExchangeRate."""
        return cls(
            id=rate.id,
            kind=rate.kind,
            value=rate.value,
            value_date=rate.value_date,
            origin=rate.origin,
            status=rate.status,
            fetched_at=rate.fetched_at,
        )


class LatestRatesOut(BaseModel):
    """Latest known BCV and USDT exchange rates."""

    bcv: RateOut | None
    usdt: RateOut | None


class RateHistoryQuery(BaseModel):
    """Query parameters for filtering historical exchange rates."""

    kind: RateKind
    from_date: date | None = Field(default=None, alias="from")
    to_date: date | None = Field(default=None, alias="to")

    @model_validator(mode="after")
    def check_date_range(self) -> Self:
        """Reject requests where the start date is after the end date."""
        if (
            self.from_date is not None
            and self.to_date is not None
            and self.from_date > self.to_date
        ):
            raise ValueError("'from' must be less than or equal to 'to'.")
        return self
