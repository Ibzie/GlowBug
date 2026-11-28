from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from glowbug.db.base import Base
from glowbug.db.types import Money

if TYPE_CHECKING:
    from glowbug.models.category import Category


class TaxProfile(Base):
    __tablename__ = "tax_profiles"
    __table_args__ = (
        Index("ix_tax_profiles_user_year", "user_id", "tax_year"),
        UniqueConstraint("user_id", "name", "tax_year", name="uq_tax_profiles_user_name_year"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(5, 2, asdecimal=True), nullable=False)
    currency_code: Mapped[str] = mapped_column(
        String(5),
        ForeignKey("currencies.code"),
        nullable=False,
    )
    tax_year: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    categories: Mapped[list[Category]] = relationship(
        secondary="tax_profile_categories",
        lazy="selectin",
    )
    payments: Mapped[list[TaxPayment]] = relationship(
        back_populates="profile",
        cascade="all, delete-orphan",
    )


class TaxProfileCategory(Base):
    __tablename__ = "tax_profile_categories"

    tax_profile_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("tax_profiles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    category_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("categories.id", ondelete="CASCADE"),
        primary_key=True,
    )


class TaxPayment(Base):
    __tablename__ = "tax_payments"
    __table_args__ = (Index("ix_tax_payments_profile", "tax_profile_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    tax_profile_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("tax_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    currency_code: Mapped[str] = mapped_column(
        String(5),
        ForeignKey("currencies.code"),
        nullable=False,
    )
    paid_on: Mapped[date] = mapped_column(Date, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    profile: Mapped[TaxProfile] = relationship(back_populates="payments")
