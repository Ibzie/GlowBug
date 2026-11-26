from glowbug.jobs.broker import broker


@broker.task
async def ping() -> str:
    """Small task used to verify that workers are connected."""
    return "pong"
