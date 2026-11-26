from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from glowbug.db.base import Base

if TYPE_CHECKING:
    from glowbug.models.account import Account
    from glowbug.models.currency import Currency


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    locale: Mapped[str] = mapped_column(String(16), nullable=False, default="en_US")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    currencies: Mapped[list[UserCurrency]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    accounts: Mapped[list[Account]] = relationship(back_populates="user")


class UserCurrency(Base):
    __tablename__ = "user_currencies"
    __table_args__ = (
        Index(
            "uq_user_currencies_primary",
            "user_id",
            unique=True,
            postgresql_where=text("is_primary"),
            sqlite_where=text("is_primary = 1"),
        ),
        Index(
            "uq_user_currencies_secondary",
            "user_id",
            unique=True,
            postgresql_where=text("is_secondary"),
            sqlite_where=text("is_secondary = 1"),
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    currency_code: Mapped[str] = mapped_column(
        String(5),
        ForeignKey("currencies.code", ondelete="CASCADE"),
        primary_key=True,
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_secondary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    user: Mapped[User] = relationship(back_populates="currencies")
    currency: Mapped[Currency] = relationship()
