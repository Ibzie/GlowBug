"""add categories and merchant memory

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-11-28 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d3e4f5a6b7c8"
down_revision: str | None = "c2d3e4f5a6b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "categories",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("normalized_name", sa.String(length=80), nullable=False),
        sa.Column("color", sa.String(length=16), nullable=False),
        sa.Column("is_system", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "normalized_name", name="uq_categories_user_name"),
    )
    op.create_index("ix_categories_user", "categories", ["user_id"], unique=False)

    op.create_table(
        "merchant_memories",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("merchant_key", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("category_id", sa.Uuid(), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("hit_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "merchant_key", name="uq_merchant_memories_user_key"),
    )
    op.create_index("ix_merchant_memories_user", "merchant_memories", ["user_id"], unique=False)

    op.add_column("accounts", sa.Column("is_system", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.alter_column("accounts", "is_system", server_default=None)

    op.add_column("transaction_journals", sa.Column("category_id", sa.Uuid(), nullable=True))
    op.add_column("transaction_journals", sa.Column("category_source", sa.String(length=16), nullable=True))
    op.add_column(
        "transaction_journals", sa.Column("category_confidence", sa.Numeric(precision=5, scale=4), nullable=True)
    )
    op.add_column("transaction_journals", sa.Column("import_fingerprint", sa.String(length=64), nullable=True))
    op.add_column("transaction_journals", sa.Column("import_batch_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_transaction_journals_category",
        "transaction_journals",
        "categories",
        ["category_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_transaction_journals_category", "transaction_journals", ["category_id"], unique=False)
    op.create_unique_constraint(
        "uq_transaction_journals_user_import",
        "transaction_journals",
        ["user_id", "import_fingerprint"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_transaction_journals_user_import", "transaction_journals", type_="unique")
    op.drop_index("ix_transaction_journals_category", table_name="transaction_journals")
    op.drop_constraint("fk_transaction_journals_category", "transaction_journals", type_="foreignkey")
    op.drop_column("transaction_journals", "import_batch_id")
    op.drop_column("transaction_journals", "import_fingerprint")
    op.drop_column("transaction_journals", "category_confidence")
    op.drop_column("transaction_journals", "category_source")
    op.drop_column("transaction_journals", "category_id")

    op.drop_column("accounts", "is_system")

    op.drop_index("ix_merchant_memories_user", table_name="merchant_memories")
    op.drop_table("merchant_memories")
    op.drop_index("ix_categories_user", table_name="categories")
    op.drop_table("categories")
