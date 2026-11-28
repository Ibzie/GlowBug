from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from glowbug.models.user import User


def user_today(user: User) -> date:
    """Return the current local date for a user, falling back to UTC."""
    try:
        return datetime.now(ZoneInfo(user.timezone)).date()
    except ZoneInfoNotFoundError, ValueError:
        return datetime.now(ZoneInfo("UTC")).date()
