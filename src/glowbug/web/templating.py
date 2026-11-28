import contextlib
from pathlib import Path

from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from glowbug.config import get_settings
from glowbug.services.formatting import format_money

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.globals["format_money"] = format_money

ICONS = {
    "dashboard": '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
    "transactions": '<path d="m8 3-4 4 4 4"/><path d="M4 7h16"/><path d="m16 21 4-4-4-4"/><path d="M20 17H4"/>',
    "savings": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.2" fill="currentColor" stroke="none"/>',
    "income": '<path d="m22 7-8.5 8.5-5-5L2 17"/><path d="M16 7h6v6"/>',
    "subscriptions": '<path d="m17 2 4 4-4 4"/><path d="M3 11v-1a4 4 0 0 1 4-4h14"/><path d="m7 22-4-4 4-4"/><path d="M21 13v1a4 4 0 0 1-4 4H3"/>',
    "accounts": '<path d="M21 12V7H5a2 2 0 0 1 0-4h14v4"/><path d="M3 5v14a2 2 0 0 0 2 2h16v-5"/><path d="M18 12a2 2 0 0 0 0 4h4v-4Z"/>',
    "categories": '<path d="M20.59 13.41 12 22l-9.3-9.3A2 2 0 0 1 2 11.18V4a2 2 0 0 1 2-2h7.18a2 2 0 0 1 1.41.59l8 8a2 2 0 0 1 0 2.82Z"/><circle cx="7" cy="7" r="1.2" fill="currentColor" stroke="none"/>',
    "taxes": '<path d="M4 2v20l2-1 2 1 2-1 2 1 2-1 2 1 2-1 2 1V2l-2 1-2-1-2 1-2-1-2 1-2-1-2 1Z"/><path d="M8 8h8"/><path d="M8 12h8"/><path d="M8 16h5"/>',
    "currencies": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3a15.3 15.3 0 0 1 0 18"/><path d="M12 3a15.3 15.3 0 0 0 0 18"/>',
    "logout": '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5"/><path d="M21 12H9"/>',
    "plus": '<path d="M12 5v14"/><path d="M5 12h14"/>',
    "upload": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m17 8-5-5-5 5"/><path d="M12 3v12"/>',
    "x": '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "check-circle": '<circle cx="12" cy="12" r="9"/><path d="m8.5 12 2.5 2.5 5-5"/>',
    "pencil": '<path d="M17 3a2.83 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/>',
    "trash": '<path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
    "alert": '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
    "calendar": '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4"/><path d="M8 2v4"/><path d="M3 10h18"/>',
    "sparkles": '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8Z"/><path d="M19 15l.9 2.1L22 18l-2.1.9L19 21l-.9-2.1L16 18l2.1-.9Z"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>',
    "arrow-right": '<path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 16v-4"/><path d="M12 8h.01"/>',
    "filter": '<path d="M22 3H2l8 9.5V19l4 2v-8.5Z"/>',
    "wallet-add": '<path d="M21 12V7H5a2 2 0 0 1 0-4h14v4"/><path d="M3 5v14a2 2 0 0 0 2 2h16v-5"/><path d="M18 12a2 2 0 0 0 0 4h4v-4Z"/>',
}


def icon(name: str, cls: str = "") -> Markup:
    path = ICONS.get(name, "")
    class_attr = f"icon {cls}".strip()
    return Markup(
        f'<svg class="{class_attr}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{path}</svg>'
    )


templates.env.globals["icon"] = icon

STATIC_DIR = BASE_DIR / "static"


def static_version() -> str:
    latest = 0
    for name in ("app.css", "app.js", "build/dashboard.js", "build/dashboard.css"):
        with contextlib.suppress(OSError):
            latest = max(latest, int((STATIC_DIR / name).stat().st_mtime))
    return str(latest)


templates.env.globals["static_version"] = static_version()

_settings = get_settings()
templates.env.globals["ocr_page_limit"] = _settings.ocr_page_limit
templates.env.globals["ocr_batch_pages"] = _settings.ocr_batch_pages
