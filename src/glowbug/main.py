from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from glowbug import __version__
from glowbug.api.currencies import router as currencies_router
from glowbug.api.dashboards import router as dashboards_router
from glowbug.api.fx import router as fx_router
from glowbug.api.health import router as health_router
from glowbug.config import get_settings
from glowbug.web.routes import router as web_router


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version=__version__)

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        same_site="lax",
        https_only=settings.app_env == "production",
    )

    static_directory = Path(__file__).resolve().parent / "web" / "static"
    app.mount("/static", StaticFiles(directory=str(static_directory)), name="static")

    app.include_router(health_router)
    app.include_router(currencies_router)
    app.include_router(fx_router)
    app.include_router(dashboards_router)
    app.include_router(web_router)

    return app


app = create_app()
