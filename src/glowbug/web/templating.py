from pathlib import Path

from fastapi.templating import Jinja2Templates

from glowbug.services.formatting import format_money

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.globals["format_money"] = format_money
