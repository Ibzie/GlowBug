from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from glowbug.db.base import Base
from glowbug.db.types import Money

if TYPE_CHECKING:
    from glowbug.models.currency import Currency


class ExchangeRate(Base):
    __tablename__ = "exchange_rates"
    __table_args__ = (
        UniqueConstraint("base_code", "quote_code", "as_of_date", name="uq_exchange_rates_pair_date"),
        CheckConstraint("rate > 0", name="ck_exchange_rates_rate_positive"),
        Index("ix_exchange_rates_pair_date", "base_code", "quote_code", "as_of_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    base_code: Mapped[str] = mapped_column(
        String(5),
        ForeignKey("currencies.code", ondelete="CASCADE"),
        nullable=False,
    )
    quote_code: Mapped[str] = mapped_column(
        String(5),
        ForeignKey("currencies.code", ondelete="CASCADE"),
        nullable=False,
    )
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    rate: Mapped[Decimal] = mapped_column(Money, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    base_currency: Mapped[Currency] = relationship(foreign_keys=[base_code])
    quote_currency: Mapped[Currency] = relationship(foreign_keys=[quote_code])
