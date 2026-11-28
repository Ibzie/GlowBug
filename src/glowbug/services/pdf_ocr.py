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
