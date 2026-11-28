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
    String,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from glowbug.db.base import Base
from glowbug.db.types import Money

if TYPE_CHECKING:
    from glowbug.models.currency import Currency
    from glowbug.models.transaction import Transaction
    from glowbug.models.user import User


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        Index("ix_accounts_user_type", "user_id", "type"),
        Index("ix_accounts_user_archived", "user_id", "archived"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    currency_code: Mapped[str] = mapped_column(
        String(5),
        ForeignKey("currencies.code"),
        nullable=False,
    )
    opening_balance: Mapped[Decimal] = mapped_column(
        Money,
        nullable=False,
        default=Decimal("0"),
    )
    opening_balance_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    include_in_net_worth: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    user: Mapped[User] = relationship(back_populates="accounts")
    currency: Mapped[Currency] = relationship()
    transactions: Mapped[list[Transaction]] = relationship(back_populates="account")


class AccountDailyBalance(Base):
    __tablename__ = "account_daily_balances"
    __table_args__ = (Index("ix_account_daily_balances_account_date", "account_id", "as_of_date"),)

    account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("accounts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    as_of_date: Mapped[date] = mapped_column(Date, primary_key=True)
    currency_code: Mapped[str] = mapped_column(
        String(5),
        ForeignKey("currencies.code"),
        primary_key=True,
    )
    balance: Mapped[Decimal] = mapped_column(Money, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
