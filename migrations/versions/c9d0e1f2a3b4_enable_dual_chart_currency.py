"""enable dual currency on the main chart widgets

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-11-28 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c9d0e1f2a3b4"
down_revision: str | None = "b8c9d0e1f2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE dashboard_widgets
        SET config = jsonb_set(config::jsonb, '{currency_mode}', '"dual"')::json
        WHERE type IN ('account_balances', 'income_expense', 'cash_flow')
          AND COALESCE(config->>'currency_mode', 'primary') = 'primary'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE dashboard_widgets
        SET config = jsonb_set(config::jsonb, '{currency_mode}', '"primary"')::json
        WHERE type IN ('account_balances', 'income_expense', 'cash_flow')
        """
    )
