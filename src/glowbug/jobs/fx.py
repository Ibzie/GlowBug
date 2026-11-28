from glowbug.db.session import async_session_factory
from glowbug.jobs.broker import broker
from glowbug.services.fx.sync import sync_frankfurter_rates


@broker.task(schedule=[{"cron": "0 6 * * 1", "args": []}])
async def sync_fx_rates() -> str:
    """Weekly Frankfurter sync. Runs Monday at 06:00 UTC."""
    async with async_session_factory() as session:
        result = await sync_frankfurter_rates(session)

    return f"inserted={result.inserted} skipped={result.skipped}"
