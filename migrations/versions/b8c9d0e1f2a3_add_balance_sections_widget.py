"""add balance by section widget to existing dashboards

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-11-28 00:00:00.000000

"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8c9d0e1f2a3"
down_revision: str | None = "a7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    dashboards = list(bind.execute(sa.text("SELECT id FROM dashboards")).scalars())
    for dashboard_id in dashboards:
        exists = bind.execute(
            sa.text("SELECT 1 FROM dashboard_widgets WHERE dashboard_id = :dashboard_id AND type = 'balance_sections'"),
            {"dashboard_id": dashboard_id},
        ).first()
        if exists is not None:
            continue

        max_y = bind.execute(
            sa.text(
                "SELECT COALESCE(MAX(COALESCE((layout->>'y')::int, 0) + COALESCE((layout->>'h')::int, 1)), 0) "
                "FROM dashboard_widgets WHERE dashboard_id = :dashboard_id"
            ),
            {"dashboard_id": dashboard_id},
        ).scalar()
        max_position = bind.execute(
            sa.text("SELECT COALESCE(MAX(position), -1) FROM dashboard_widgets WHERE dashboard_id = :dashboard_id"),
            {"dashboard_id": dashboard_id},
        ).scalar()

        bind.execute(
            sa.text(
                "INSERT INTO dashboard_widgets "
                "(id, dashboard_id, type, title, config, layout, position) "
                "VALUES (CAST(:id AS uuid), CAST(:dashboard_id AS uuid), 'balance_sections', "
                "'Balance by section', CAST(:config AS json), CAST(:layout AS json), :position)"
            ),
            {
                "id": str(uuid.uuid4()),
                "dashboard_id": str(dashboard_id),
                "config": json.dumps({"visualization": "doughnut", "currency_mode": "primary"}),
                "layout": json.dumps({"x": 0, "y": int(max_y or 0), "w": 6, "h": 3}),
                "position": int(max_position or 0) + 1,
            },
        )


def downgrade() -> None:
    op.execute("DELETE FROM dashboard_widgets WHERE type = 'balance_sections'")
