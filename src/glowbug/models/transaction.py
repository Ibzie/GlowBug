from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from glowbug.db.base import Base
from glowbug.db.types import Money

if TYPE_CHECKING:
    from glowbug.models.account import Account
    from glowbug.models.currency import Currency
    from glowbug.models.user import User


class TransactionJournal(Base):
    __tablename__ = "transaction_journals"
    __table_args__ = (
        Index("ix_transaction_journals_user_date", "user_id", "date"),
        Index("ix_transaction_journals_user_type_date", "user_id", "type", "date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str] = mapped_column(String(512), nullable=False)
    currency_code: Mapped[str] = mapped_column(
        String(5),
        ForeignKey("currencies.code"),
        nullable=False,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    user: Mapped[User] = relationship()
    currency: Mapped[Currency] = relationship(foreign_keys=[currency_code])
    transactions: Mapped[list[Transaction]] = relationship(
        back_populates="journal",
        cascade="all, delete-orphan",
    )


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        Index("ix_transactions_journal", "journal_id"),
        Index("ix_transactions_account", "account_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    journal_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("transaction_journals.id", ondelete="CASCADE"),
        nullable=False,
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    currency_code: Mapped[str] = mapped_column(
        String(5),
        ForeignKey("currencies.code"),
        nullable=False,
    )
    foreign_amount: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    foreign_currency_code: Mapped[str | None] = mapped_column(
        String(5),
        ForeignKey("currencies.code"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    journal: Mapped[TransactionJournal] = relationship(back_populates="transactions")
    account: Mapped[Account] = relationship(back_populates="transactions")
    currency: Mapped[Currency] = relationship(foreign_keys=[currency_code])
    foreign_currency: Mapped[Currency | None] = relationship(foreign_keys=[foreign_currency_code])
