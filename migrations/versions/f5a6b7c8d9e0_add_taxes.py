"""add taxes

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
Create Date: 2026-11-28 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import glowbug.db.types

revision: str = "f5a6b7c8d9e0"
down_revision: str | None = "e4f5a6b7c8d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tax_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("rate", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("currency_code", sa.String(length=5), nullable=False),
        sa.Column("tax_year", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["currency_code"], ["currencies.code"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "name", "tax_year", name="uq_tax_profiles_user_name_year"),
    )
    op.create_index("ix_tax_profiles_user_year", "tax_profiles", ["user_id", "tax_year"], unique=False)

    op.create_table(
        "tax_profile_categories",
        sa.Column("tax_profile_id", sa.Uuid(), nullable=False),
        sa.Column("category_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tax_profile_id"], ["tax_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tax_profile_id", "category_id"),
    )

    op.create_table(
        "tax_payments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("tax_profile_id", sa.Uuid(), nullable=False),
        sa.Column("amount", glowbug.db.types.Money(precision=38, scale=18), nullable=False),
        sa.Column("currency_code", sa.String(length=5), nullable=False),
        sa.Column("paid_on", sa.Date(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["currency_code"], ["currencies.code"]),
        sa.ForeignKeyConstraint(["tax_profile_id"], ["tax_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tax_payments_profile", "tax_payments", ["tax_profile_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_tax_payments_profile", table_name="tax_payments")
    op.drop_table("tax_payments")
    op.drop_table("tax_profile_categories")
    op.drop_index("ix_tax_profiles_user_year", table_name="tax_profiles")
    op.drop_table("tax_profiles")
