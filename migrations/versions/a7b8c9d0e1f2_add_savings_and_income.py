"""add savings goals and recurring income

Revision ID: a7b8c9d0e1f2
Revises: f5a6b7c8d9e0
Create Date: 2026-11-28 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import glowbug.db.types

revision: str = "a7b8c9d0e1f2"
down_revision: str | None = "f5a6b7c8d9e0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "income_schedules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("amount", glowbug.db.types.Money(precision=38, scale=18), nullable=False),
        sa.Column("currency_code", sa.String(length=5), nullable=False),
        sa.Column("category_id", sa.Uuid(), nullable=True),
        sa.Column("day_of_month", sa.Integer(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("last_applied_on", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["currency_code"], ["currencies.code"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "name", name="uq_income_schedules_user_name"),
    )
    op.create_index("ix_income_schedules_user", "income_schedules", ["user_id"], unique=False)

    op.create_table(
        "savings_goals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("target_amount", glowbug.db.types.Money(precision=38, scale=18), nullable=False),
        sa.Column("currency_code", sa.String(length=5), nullable=False),
        sa.Column(
            "monthly_contribution",
            glowbug.db.types.Money(precision=38, scale=18),
            nullable=False,
        ),
        sa.Column("contribution_day", sa.Integer(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=True),
        sa.Column("color", sa.String(length=7), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["currency_code"], ["currencies.code"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "name", name="uq_savings_goals_user_name"),
    )
    op.create_index("ix_savings_goals_user", "savings_goals", ["user_id"], unique=False)

    op.create_table(
        "savings_contributions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("goal_id", sa.Uuid(), nullable=False),
        sa.Column("amount", glowbug.db.types.Money(precision=38, scale=18), nullable=False),
        sa.Column("currency_code", sa.String(length=5), nullable=False),
        sa.Column("contributed_on", sa.Date(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["currency_code"], ["currencies.code"]),
        sa.ForeignKeyConstraint(["goal_id"], ["savings_goals.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_savings_contributions_goal_date",
        "savings_contributions",
        ["goal_id", "contributed_on"],
        unique=False,
    )
    op.create_index("ix_savings_contributions_user", "savings_contributions", ["user_id"], unique=False)
    op.create_index(
        "uq_savings_contributions_goal_period",
        "savings_contributions",
        ["goal_id", "period"],
        unique=True,
        postgresql_where=sa.text("period IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_savings_contributions_goal_period", table_name="savings_contributions")
    op.drop_index("ix_savings_contributions_user", table_name="savings_contributions")
    op.drop_index("ix_savings_contributions_goal_date", table_name="savings_contributions")
    op.drop_table("savings_contributions")
    op.drop_index("ix_savings_goals_user", table_name="savings_goals")
    op.drop_table("savings_goals")
    op.drop_index("ix_income_schedules_user", table_name="income_schedules")
    op.drop_table("income_schedules")
