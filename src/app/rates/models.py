from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import CheckConstraint, Date, DateTime, Index, Numeric, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ExchangeRateModel(Base):
    """SQLAlchemy model for the exchange_rates table."""

    __tablename__ = "exchange_rates"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    value: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    value_date: Mapped[date] = mapped_column(Date)
    origin: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("kind IN ('bcv', 'usdt')", name="ck_exchange_rates_kind"),
        CheckConstraint(
            "origin IN ('bcv_html', 'dolarapi', 'binance_p2p', 'manual')",
            name="ck_exchange_rates_origin",
        ),
        CheckConstraint("status IN ('confirmed', 'in_review')", name="ck_exchange_rates_status"),
        Index(
            "uq_exchange_rates_bcv_value_date",
            "value_date",
            unique=True,
            postgresql_where=text("kind = 'bcv'"),
        ),
        Index(
            "ix_exchange_rates_kind_fetched_at",
            kind,
            fetched_at.desc(),
        ),
    )
