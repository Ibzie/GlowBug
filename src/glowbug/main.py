from fastapi import FastAPI

from glowbug import __version__
from glowbug.api.currencies import router as currencies_router
from glowbug.api.fx import router as fx_router
from glowbug.api.health import router as health_router
from glowbug.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version=__version__)
    app.include_router(health_router)
    app.include_router(currencies_router)
    app.include_router(fx_router)

    @app.get("/", tags=["system"])
    async def root() -> dict[str, str]:
        return {
            "name": settings.app_name,
            "version": __version__,
            "status": "ok",
        }

    return app


app = create_app()
