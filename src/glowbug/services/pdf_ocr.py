"""OCR pipeline for PDF statements.

Every PDF is rasterised and read with Tesseract. Statements are frequently
scans, and many bank-generated PDFs have no usable text layer at all, so OCR is
the only extraction path rather than a fallback.

The pipeline:

1. render each page with pypdfium2 at a configurable scale,
2. erase long horizontal/vertical rules (table borders) that confuse Tesseract,
3. read word boxes with Tesseract,
4. group words into lines and infer column boundaries from whitespace,
5. fall back to per-line token heuristics when columns cannot be inferred.
"""

from __future__ import annotations

import bisect
import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from itertools import pairwise
from pathlib import Path

from PIL import Image, ImageOps

from glowbug.config import get_settings

logger = logging.getLogger(__name__)

DATE_TOKEN = re.compile(r"^\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}$")
COMPACT_DATE_TOKEN = re.compile(r"^\d{8}$")
AMOUNT_TOKEN = re.compile(r"^[(\-+]?[$€£¥]?\d[\d.,]*\)?-?$")
TOKEN_NOISE = "|'`^*_~\\/\u2018\u2019\u201c\u201d"
DEFAULT_PSM = 6


@dataclass(slots=True)
class OcrWord:
    text: str
    left: int
    top: int
    width: int
    height: int
    confidence: float

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def centre(self) -> float:
        return self.left + self.width / 2


def page_ranges(page_count: int, batch_pages: int) -> list[tuple[int, int]]:
    """Split a document into half-open page ranges for batched OCR."""
    size = max(1, batch_pages)
    return [(start, min(start + size, page_count)) for start in range(0, page_count, size)]


def _render_pages(
    path: Path,
    scale: int,
    page_limit: int,
    batch_pages: int,
) -> Iterator[Image.Image]:
    import pypdfium2 as pdfium

    try:
        document = pdfium.PdfDocument(str(path))
    except Exception as exc:
        raise ValueError(f"Could not open the PDF file: {exc}") from exc

    try:
        page_count = len(document)
        if page_limit and page_count > page_limit:
            raise ValueError(
                f"This PDF has {page_count} pages; the limit is {page_limit}. "
                "Split the statement into smaller files and import them separately."
            )
        for start, end in page_ranges(page_count, batch_pages):
            logger.info("OCR reading pages %d-%d of %d", start + 1, end, page_count)
            for index in range(start, end):
                page = document[index]
                try:
                    bitmap = page.render(scale=scale)
                    try:
                        yield bitmap.to_pil()
                    finally:
                        bitmap.close()
                finally:
                    page.close()
    finally:
        document.close()


def _remove_rules(image: Image.Image) -> Image.Image:
    """Erase long runs of dark pixels, which are almost always table rules."""
    import numpy as np

    gray = np.asarray(ImageOps.autocontrast(image.convert("L")))
    dark = gray < 180
    if not dark.any():
        return Image.fromarray(gray)

    height, width = dark.shape
    min_horizontal = max(24, int(width * 0.05))
    min_vertical = max(24, int(height * 0.05))

    horizontal: list[tuple[int, int, int]] = []
    for y in np.flatnonzero(dark.any(axis=1)):
        padded = np.concatenate(([False], dark[y], [False]))
        changes = np.diff(padded.astype(np.int8))
        for start, end in zip(np.flatnonzero(changes == 1), np.flatnonzero(changes == -1), strict=False):
            if end - start >= min_horizontal:
                horizontal.append((int(y), int(start), int(end)))

    vertical: list[tuple[int, int, int]] = []
    for x in np.flatnonzero(dark.any(axis=0)):
        padded = np.concatenate(([False], dark[:, x], [False]))
        changes = np.diff(padded.astype(np.int8))
        for start, end in zip(np.flatnonzero(changes == 1), np.flatnonzero(changes == -1), strict=False):
            if end - start >= min_vertical:
                vertical.append((int(x), int(start), int(end)))

    for y, start, end in horizontal:
        dark[y, start:end] = False
    for x, start, end in vertical:
        dark[start:end, x] = False

    return Image.fromarray(np.where(dark, 0, 255).astype(np.uint8))


def _clean_token(value: str) -> str:
    token = value.strip().replace("\u2212", "-").replace("\u2013", "-")
    token = token.replace("|", "")
    return token.strip(TOKEN_NOISE)


def _extract_words(image: Image.Image, psm: int) -> list[OcrWord]:
    import pytesseract

    settings = get_settings()
    pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd
    try:
        data = pytesseract.image_to_data(
            image,
            lang=settings.ocr_language,
            config=f"--psm {psm}",
            output_type=pytesseract.Output.DICT,
        )
    except pytesseract.TesseractNotFoundError as exc:
        raise ValueError(
            "PDF import needs Tesseract OCR. Install `tesseract-ocr` and the language pack "
            f"for {settings.ocr_language!r} (for example `tesseract-ocr-eng`)."
        ) from exc
    except pytesseract.TesseractError as exc:
        raise ValueError(f"Tesseract could not read the PDF: {exc}") from exc

    words: list[OcrWord] = []
    for index, raw in enumerate(data["text"]):
        token = _clean_token(str(raw))
        if not token or not any(character.isalnum() for character in token):
            continue
        try:
            confidence = float(data["conf"][index])
        except TypeError, ValueError:
            confidence = -1.0
        if confidence < settings.ocr_min_confidence:
            continue
        width = int(data["width"][index] or 0)
        height = int(data["height"][index] or 0)
        if width <= 0 or height <= 0:
            continue
        words.append(
            OcrWord(
                text=token,
                left=int(data["left"][index] or 0),
                top=int(data["top"][index] or 0),
                width=width,
                height=height,
                confidence=confidence,
            )
        )
    return words


def _group_lines(words: list[OcrWord]) -> list[list[OcrWord]]:
    if not words:
        return []
    heights = sorted(word.height for word in words)
    median_height = heights[len(heights) // 2] or 10
    tolerance = max(4, int(median_height * 0.6))

    lines: list[list[OcrWord]] = []
    for word in sorted(words, key=lambda item: (item.top + item.height / 2, item.left)):
        centre = word.top + word.height / 2
        if lines:
            current = lines[-1]
            current_centre = sum(item.top + item.height / 2 for item in current) / len(current)
            if abs(current_centre - centre) <= tolerance:
                current.append(word)
                continue
        lines.append([word])

    for line in lines:
        line.sort(key=lambda item: item.left)
    return lines


def _column_boundaries(lines: list[list[OcrWord]], width: int) -> list[int]:
    import numpy as np

    if len(lines) < 2 or width <= 0:
        return []

    coverage = np.zeros(width, dtype=np.int32)
    for line in lines:
        marks = np.zeros(width, dtype=np.int32)
        for word in line:
            marks[max(0, word.left) : min(width, word.right)] = 1
        coverage += marks

    ink = np.flatnonzero(coverage > 0)
    if ink.size == 0:
        return []

    free = coverage <= max(1, int(len(lines) * 0.4))
    first_ink, last_ink = int(ink[0]), int(ink[-1])
    min_gap = max(8, int(width * 0.01))

    boundaries: list[int] = []
    run_start: int | None = None
    for x in range(first_ink, last_ink + 1):
        if free[x]:
            if run_start is None:
                run_start = x
        elif run_start is not None:
            if x - run_start >= min_gap:
                boundaries.append((run_start + x) // 2)
            run_start = None
    return boundaries


def _heuristic_cells(line: list[OcrWord]) -> list[str] | None:
    tokens = [word.text for word in line]
    date_index = next(
        (index for index, token in enumerate(tokens) if DATE_TOKEN.match(token) or COMPACT_DATE_TOKEN.match(token)),
        None,
    )
    if date_index is None:
        return None

    amount_index: int | None = None
    for index in range(len(tokens) - 1, date_index, -1):
        candidate = tokens[index].strip()
        if AMOUNT_TOKEN.match(candidate):
            amount_index = index
            break
    if amount_index is None or amount_index <= date_index:
        return None

    while amount_index - 1 > date_index and tokens[amount_index - 1] in {"$", "€", "£", "¥"}:
        amount_index -= 1

    description = " ".join(tokens[date_index + 1 : amount_index]).strip()
    return [tokens[date_index], description, " ".join(tokens[amount_index:])]


def _line_cells(line: list[OcrWord], boundaries: list[int]) -> list[str]:
    cells = [""] * (len(boundaries) + 1)
    for word in line:
        index = bisect.bisect_right(boundaries, word.centre)
        cells[index] = f"{cells[index]} {word.text}".strip()

    if sum(1 for cell in cells if cell) >= 3:
        return cells

    heuristic = _heuristic_cells(line)
    if heuristic is not None:
        return heuristic
    return cells


@dataclass(frozen=True)
class TableColumn:
    name: str
    left: int
    right: int


HEADER_ALIASES: tuple[tuple[str, str], ...] = (
    ("descript", "DESCRIPTION"),
    ("narrat", "DESCRIPTION"),
    ("details", "DESCRIPTION"),
    ("particular", "DESCRIPTION"),
    ("memo", "DESCRIPTION"),
    ("amount", "AMOUNT"),
    ("amoun", "AMOUNT"),
    ("balanc", "BALANCE"),
    ("time", "TIME"),
    ("date", "DATE"),
    ("type", "TYPE"),
)

MONTH_NUMBERS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def _header_label(text: str) -> str | None:
    cleaned = re.sub(r"[^a-z]", "", text.lower())
    for prefix, name in HEADER_ALIASES:
        if cleaned.startswith(prefix):
            return name
    return None


def _find_table_header(lines: list[list[OcrWord]]) -> tuple[int, list[TableColumn]] | None:
    """Locate a transaction-table header and its column positions."""
    best: tuple[int, list[TableColumn]] | None = None
    for index, line in enumerate(lines):
        columns: list[TableColumn] = []
        seen: set[str] = set()
        for word in sorted(line, key=lambda item: item.left):
            name = _header_label(word.text)
            if name is None or name in seen:
                continue
            seen.add(name)
            columns.append(TableColumn(name=name, left=word.left, right=word.right))
        names = {column.name for column in columns}
        if len(columns) < 3:
            continue
        if "AMOUNT" not in names or "DESCRIPTION" not in names:
            continue
        if "TIME" not in names and "DATE" not in names:
            continue
        if best is None or len(columns) > len(best[1]):
            best = (index, columns)
    return best


def _table_boundaries(columns: list[TableColumn], width: int) -> list[int]:
    boundaries: list[int] = []
    for previous, current in pairwise(columns):
        gap = max(0, current.left - previous.right)
        padding = min(24, max(4, gap // 5))
        boundary = max(previous.left + 1, current.left - padding)
        if boundaries:
            boundary = max(boundary, boundaries[-1] + 1)
        boundaries.append(min(boundary, width - 1))
    return boundaries


def _footer_top(lines: list[list[OcrWord]], height: int) -> int:
    footer_pattern = re.compile(
        r"carried|forward|brought|www\.|support@|page\s+\d+|^[\d\s]+$",
        re.IGNORECASE,
    )
    footer_top = height
    for line in lines:
        top = min(word.top for word in line)
        if top < height * 0.78:
            continue
        text = " ".join(word.text for word in line)
        if footer_pattern.search(text):
            footer_top = min(footer_top, max(0, top - 8))
    return footer_top


def _date_from_text(text: str) -> str | None:
    cleaned = re.sub(r"\b[Oo](?=\d)", "0", text)
    cleaned = re.sub(r"(?<=\d)[Oo]\b", "0", cleaned)
    cleaned = cleaned.replace("\u2013", "-").replace("\u2212", "-")

    match = re.search(r"(\d{1,3})\s*([A-Za-z]{3,9})\.?,?\s*(\d{4})", cleaned)
    if match is not None:
        day = int(match.group(1))
        month = MONTH_NUMBERS.get(match.group(2)[:3].lower())
        year = int(match.group(3))
        if month is not None:
            try:
                return date(year, month, day).isoformat()
            except ValueError:
                pass

    match = re.search(r"([A-Za-z]{3,9})\.?\s+(\d{1,3}),?\s+(\d{4})", cleaned)
    if match is not None:
        day = int(match.group(2))
        month = MONTH_NUMBERS.get(match.group(1)[:3].lower())
        year = int(match.group(3))
        if month is not None:
            try:
                return date(year, month, day).isoformat()
            except ValueError:
                pass

    match = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", cleaned)
    if match is not None:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat()
        except ValueError:
            pass

    match = re.search(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})", cleaned)
    if match is not None:
        first, second = int(match.group(1)), int(match.group(2))
        year = int(match.group(3))
        if year < 100:
            year += 2000
        for day, month in ((first, second), (second, first)):
            try:
                return date(year, month, day).isoformat()
            except ValueError:
                continue
    return None


def _clean_description(text: str) -> str:
    description = re.sub(r"\s+", " ", text).strip()
    description = re.sub(r"\bTransaction ID\b\s*\S*", " ", description, flags=re.IGNORECASE)
    description = re.sub(r"\b[a-f0-9]{20,}\b", " ", description, flags=re.IGNORECASE)
    description = re.sub(
        r"\b(?:Service Charges|Fees and Government Taxes)\b\s*(?:Rs\.?)?\s*[\d.,OoCc]+",
        " ",
        description,
        flags=re.IGNORECASE,
    )
    description = re.sub(r"\s+", " ", description).strip(" -\u2013")
    return description[:500]


def _table_headers(columns: list[TableColumn]) -> list[str] | None:
    names = {column.name for column in columns}
    if "AMOUNT" not in names or "DESCRIPTION" not in names:
        return None
    if "TIME" not in names and "DATE" not in names:
        return None
    headers = ["Date", "Description", "Amount"]
    if "BALANCE" in names:
        headers.append("Balance")
    return headers


def _extract_table_page(
    image: Image.Image,
    words: list[OcrWord],
    lines: list[list[OcrWord]],
    header_index: int,
    columns: list[TableColumn],
) -> list[list[str]]:
    header_bottom = max((word.top + word.height for word in lines[header_index]), default=0)
    boundaries = _table_boundaries(columns, image.width)
    footer_top = _footer_top(lines, image.height)
    table_words = [word for word in words if word.top >= header_bottom - 2 and word.top < footer_top]
    if not table_words:
        return []

    amount_index = next(index for index, column in enumerate(columns) if column.name == "AMOUNT")
    date_index = next(index for index, column in enumerate(columns) if column.name in {"TIME", "DATE"})
    description_indexes = [index for index, column in enumerate(columns) if column.name in {"TYPE", "DESCRIPTION"}]
    balance_index = next(
        (index for index, column in enumerate(columns) if column.name == "BALANCE"),
        None,
    )

    def column_of(word: OcrWord) -> int:
        return bisect.bisect_right(boundaries, word.centre)

    amount_words = [
        word
        for word in table_words
        if column_of(word) == amount_index and any(character.isdigit() for character in word.text)
    ]
    if not amount_words:
        return []

    amount_words.sort(key=lambda word: word.top)
    tolerance = max(6, int(sum(word.height for word in amount_words) / len(amount_words) * 0.6))
    anchors: list[float] = []
    for word in amount_words:
        centre = word.top + word.height / 2
        if anchors and abs(centre - anchors[-1]) <= tolerance:
            continue
        anchors.append(centre)

    median_height = sorted(word.height for word in words)[len(words) // 2] or 10
    first_start = float(header_bottom)
    if header_index < 0:
        first_start = max(0.0, anchors[0] - median_height * 2)

    rows: list[list[str]] = []
    for index, anchor in enumerate(anchors):
        start = first_start if index == 0 else (anchors[index - 1] + anchor) / 2
        end = (anchor + anchors[index + 1]) / 2 if index + 1 < len(anchors) else footer_top
        band = [word for word in table_words if start <= (word.top + word.height / 2) < end]

        cells = ["" for _ in columns]
        for band_line in _group_lines(band):
            for word in band_line:
                column = column_of(word)
                cells[column] = f"{cells[column]} {word.text}".strip()

        journal_date = _date_from_text(cells[date_index])
        amount_text = cells[amount_index].strip()
        if journal_date is None or not amount_text:
            continue

        description = _clean_description(" ".join(cells[column] for column in description_indexes))
        row = [journal_date, description, amount_text]
        if balance_index is not None:
            row.append(cells[balance_index].strip())
        rows.append(row)
    return rows


def _colour_band_words(image: Image.Image) -> list[OcrWord]:
    """OCR thin coloured header bands after inverting them.

    White text on an orange/blue header band turns into white-on-black after
    normal grayscale conversion, which Tesseract reads poorly. Detecting those
    bands and inverting just the band recovers the column headings.
    """
    import numpy as np

    hsv = np.asarray(image.convert("HSV"))
    if hsv.size == 0:
        return []

    height, width = hsv.shape[0], hsv.shape[1]
    saturation = hsv[:, :, 1]
    coverage = (saturation > 60).mean(axis=1) > 0.5

    runs: list[tuple[int, int]] = []
    start: int | None = None
    for y, covered in enumerate(coverage):
        if covered and start is None:
            start = y
        elif not covered and start is not None:
            runs.append((start, y))
            start = None
    if start is not None:
        runs.append((start, len(coverage)))

    words: list[OcrWord] = []
    for top, bottom in runs:
        if bottom - top < 10 or bottom - top > height * 0.08:
            continue
        crop_top = max(0, top - 4)
        band = image.crop((0, crop_top, width, min(height, bottom + 4)))
        inverted = ImageOps.autocontrast(ImageOps.invert(band.convert("L")))
        for word in _extract_words(inverted, DEFAULT_PSM):
            word.top += crop_top
            words.append(word)
    return words


def extract_pdf_table(path: Path) -> tuple[list[str], list[list[str]]] | None:
    """Extract statement tables using the document's own column headings."""
    settings = get_settings()
    headers: list[str] | None = None
    rows: list[list[str]] = []
    known_columns: list[TableColumn] | None = None

    for image in _render_pages(
        path,
        settings.ocr_scale,
        settings.ocr_page_limit,
        settings.ocr_batch_pages,
    ):
        band_words = _colour_band_words(image)
        prepared = _remove_rules(image)
        words = _extract_words(prepared, settings.ocr_psm or DEFAULT_PSM)
        if not words:
            words = _extract_words(prepared, 4)
        if band_words:
            band_top = min(word.top for word in band_words) - 4
            band_bottom = max(word.top + word.height for word in band_words) + 4
            words = [word for word in words if not (band_top <= word.top + word.height / 2 <= band_bottom)]
            words.extend(band_words)
        if not words:
            continue

        lines = _group_lines(words)
        found = _find_table_header(lines)
        if found is not None:
            header_index, columns = found
            known_columns = columns
        elif known_columns is not None:
            header_index, columns = -1, known_columns
        else:
            continue

        if headers is None:
            headers = _table_headers(columns)
            if headers is None:
                continue

        rows.extend(_extract_table_page(prepared, words, lines, header_index, columns))

    if headers is None or not rows:
        return None
    return headers, rows


def extract_pdf_rows(path: Path) -> list[list[str]]:
    """Return OCR rows (lists of cells) for every statement page."""
    settings = get_settings()
    rows: list[list[str]] = []
    try:
        for image in _render_pages(
            path,
            settings.ocr_scale,
            settings.ocr_page_limit,
            settings.ocr_batch_pages,
        ):
            prepared = _remove_rules(image)
            words = _extract_words(prepared, settings.ocr_psm or DEFAULT_PSM)
            if not words:
                words = _extract_words(prepared, 4)
            if not words:
                continue

            lines = _group_lines(words)
            boundaries = _column_boundaries(lines, prepared.width)
            for line in lines:
                cells = _line_cells(line, boundaries)
                while cells and not cells[-1]:
                    cells.pop()
                if sum(1 for cell in cells if cell) >= 2:
                    rows.append(cells)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"Could not read the PDF file: {exc}") from exc

    return rows
