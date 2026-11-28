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
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from glowbug.db.base import Base
from glowbug.db.types import Money

if TYPE_CHECKING:
    from glowbug.models.account import Account
    from glowbug.models.category import Category


class IncomeSchedule(Base):
    """Recurring income paid into an asset account on a chosen day each month."""

    __tablename__ = "income_schedules"
    __table_args__ = (
        Index("ix_income_schedules_user", "user_id"),
        UniqueConstraint("user_id", "name", name="uq_income_schedules_user_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    currency_code: Mapped[str] = mapped_column(
        String(5),
        ForeignKey("currencies.code"),
        nullable=False,
    )
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("categories.id", ondelete="SET NULL"),
        nullable=True,
    )
    day_of_month: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_applied_on: Mapped[date | None] = mapped_column(Date, nullable=True)
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

    account: Mapped[Account] = relationship()
    category: Mapped[Category | None] = relationship()


class SavingsGoal(Base):
    """A virtual savings envelope earmarking part of the user's balance."""

    __tablename__ = "savings_goals"
    __table_args__ = (
        Index("ix_savings_goals_user", "user_id"),
        UniqueConstraint("user_id", "name", name="uq_savings_goals_user_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    target_amount: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    currency_code: Mapped[str] = mapped_column(
        String(5),
        ForeignKey("currencies.code"),
        nullable=False,
    )
    monthly_contribution: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    contribution_day: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    target_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    color: Mapped[str] = mapped_column(String(7), nullable=False, default="#2f6fed")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
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

    contributions: Mapped[list[SavingsContribution]] = relationship(
        back_populates="goal",
        cascade="all, delete-orphan",
    )


class SavingsContribution(Base):
    """Money earmarked into a savings goal, either monthly or manually."""

    __tablename__ = "savings_contributions"
    __table_args__ = (
        Index("ix_savings_contributions_goal_date", "goal_id", "contributed_on"),
        Index("ix_savings_contributions_user", "user_id"),
        # One automatic contribution per goal per month. Manual contributions
        # leave `period` empty so several can be recorded in the same month.
        Index(
            "uq_savings_contributions_goal_period",
            "goal_id",
            "period",
            unique=True,
            postgresql_where=text("period IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    goal_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("savings_goals.id", ondelete="CASCADE"),
        nullable=False,
    )
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    currency_code: Mapped[str] = mapped_column(
        String(5),
        ForeignKey("currencies.code"),
        nullable=False,
    )
    contributed_on: Mapped[date] = mapped_column(Date, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    period: Mapped[str | None] = mapped_column(String(7), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    goal: Mapped[SavingsGoal] = relationship(back_populates="contributions")
