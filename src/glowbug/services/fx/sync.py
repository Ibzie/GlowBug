from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from glowbug.models.currency import Currency
from glowbug.models.exchange_rate import ExchangeRate
from glowbug.services.fx.frankfurter import FrankfurterError, fetch_rates
from glowbug.services.settings import get_setting, set_setting

LAST_SYNC_KEY = "fx.last_sync_at"


@dataclass(frozen=True)
class SyncResult:
    base: str
    inserted: int
    skipped: int
    finished_at: datetime


async def sync_frankfurter_rates(session: AsyncSession, base: str = "USD") -> SyncResult:
    """Fetch Frankfurter once and store missing rates. No fallback provider."""
    base = base.upper()
    now = datetime.now(UTC)
    active_codes = set(
        await session.scalars(
            select(Currency.code).where(
                Currency.is_active.is_(True),
                Currency.is_iso.is_(True),
            )
        )
    )
    try:
        fetched = await fetch_rates(base=base)
    except FrankfurterError as exc:
        await set_setting(session, "fx.last_status", "failed")
        await set_setting(session, "fx.last_error", str(exc))
        await session.commit()
        raise

    existing = {
        (row[0], row[1], row[2])
        for row in (
            await session.execute(
                select(
                    ExchangeRate.base_code,
                    ExchangeRate.quote_code,
                    ExchangeRate.as_of_date,
                ).where(ExchangeRate.base_code == base)
            )
        ).all()
    }

    inserted = 0
    skipped = 0
    for quote in fetched:
        if quote.quote not in active_codes:
            continue
        key = (quote.base, quote.quote, quote.as_of_date)
        if key in existing:
            skipped += 1
            continue

        session.add(
            ExchangeRate(
                base_code=quote.base,
                quote_code=quote.quote,
                as_of_date=quote.as_of_date,
                rate=quote.rate,
                source="frankfurter",
                fetched_at=now,
            )
        )
        existing.add(key)
        inserted += 1

    await set_setting(session, "fx.last_status", "success")
    await set_setting(session, "fx.last_error", "")
    await set_setting(session, "fx.last_inserted", str(inserted))
    await set_setting(session, "fx.last_skipped", str(skipped))
    await set_setting(session, LAST_SYNC_KEY, now.isoformat())
    await session.commit()

    return SyncResult(base=base, inserted=inserted, skipped=skipped, finished_at=now)


async def get_last_sync(session: AsyncSession) -> datetime | None:
    value = await get_setting(session, LAST_SYNC_KEY)
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
