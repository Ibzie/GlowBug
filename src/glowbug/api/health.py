import sys

from fastapi import APIRouter

from glowbug import __version__

router = APIRouter(tags=["system"])


def gil_enabled() -> bool:
    """Return True when the interpreter is running with the GIL enabled."""
    return bool(getattr(sys, "_is_gil_enabled", lambda: True)())


@router.get("/healthz")
async def healthz() -> dict[str, object]:
    return {
        "status": "ok",
        "version": __version__,
        "gil_enabled": gil_enabled(),
    }
