from pathlib import Path

from fastapi.templating import Jinja2Templates

from glowbug.config import get_settings
from glowbug.services.formatting import format_money

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.globals["format_money"] = format_money

_settings = get_settings()
templates.env.globals["ocr_page_limit"] = _settings.ocr_page_limit
templates.env.globals["ocr_batch_pages"] = _settings.ocr_batch_pages
