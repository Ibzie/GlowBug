from __future__ import annotations

import logging

from sqlalchemy import select

from glowbug.db.session import async_session_factory
from glowbug.jobs.broker import broker
from glowbug.models.user import User
from glowbug.services.dates import user_today
from glowbug.services.income import apply_due_income
from glowbug.services.savings import apply_due_contributions

logger = logging.getLogger(__name__)


@broker.task(schedule=[{"cron": "0 7 * * *", "args": []}])
async def apply_recurring_items() -> str:
    """Apply monthly income and savings contributions. Runs daily at 07:00 UTC."""
    income_count = 0
    savings_count = 0

    async with async_session_factory() as session:
        users = list(await session.scalars(select(User)))
        for user in users:
            try:
                today = user_today(user)
                income_count += len(await apply_due_income(session, user, today))
                savings_count += len(await apply_due_contributions(session, user, today))
                await session.commit()
            except Exception:
                await session.rollback()
                logger.exception("Could not apply recurring items for user %s", user.id)

    return f"income_journals={income_count} savings_contributions={savings_count}"
