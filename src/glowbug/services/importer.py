from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from glowbug.enums import AccountType, JournalType
from glowbug.models.account import Account
from glowbug.models.transaction import TransactionJournal
from glowbug.models.user import User
from glowbug.services.categories import get_category_by_name
from glowbug.services.categorizer import MerchantPrediction, normalize_merchant, predict_category
from glowbug.services.ledger import Split, post_journal
from glowbug.services.transactions import ensure_system_account

DATE_FORMATS = [
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%d-%m-%Y",
    "%Y/%m/%d",
    "%d.%m.%Y",
]


@dataclass(frozen=True)
class ImportMapping:
    date_column: str
    description_column: str
    amount_column: str
    account_id: UUID
    date_format: str = "%Y-%m-%d"
    currency_column: str = ""
    category_column: str = ""
    positive_is_expense: bool = True


@dataclass(frozen=True)
class ParsedRow:
    journal_date: date
    description: str
    amount: Decimal
    transaction_type: str
    currency_code: str
    category_name: str
    fingerprint: str


@dataclass
class ImportParseResult:
    rows: list[ParsedRow] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class ImportSummary:
    imported: int
    skipped: int
    categorized: int
    uncategorized: int
    batch_id: UUID


def detect_delimiter(text: str) -> str:
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        return dialect.delimiter
    except csv.Error:
        return ","


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = path.read_text(encoding="latin-1")

    delimiter = detect_delimiter(text)
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    headers = [str(header).strip() for header in (reader.fieldnames or [])]
    if not headers:
        raise ValueError("The CSV file has no header row.")

    rows: list[dict[str, str]] = []
    for raw_row in reader:
        row = {str(key).strip(): str(value).strip() for key, value in raw_row.items() if key is not None}
        if any(row.values()):
            rows.append(row)
    return headers, rows


def _cell_to_string(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value).strip()


def _unique_headers(values: list[str], width: int) -> list[str]:
    headers: list[str] = []
    seen: dict[str, int] = {}
    for index in range(width):
        raw = values[index].strip() if index < len(values) else ""
        base = raw or f"Column {index + 1}"
        count = seen.get(base, 0)
        seen[base] = count + 1
        headers.append(base if count == 0 else f"{base} ({count + 1})")
    return headers


def _find_header_index(rows: list[list[str]]) -> int:
    keywords = ("date", "description", "details", "amount", "debit", "credit", "balance")
    for index, row in enumerate(rows[:30]):
        non_empty = [value for value in row if value]
        if len(non_empty) < 2:
            continue
        joined = " ".join(value.lower() for value in non_empty)
        if any(keyword in joined for keyword in keywords):
            return index
    for index, row in enumerate(rows):
        if len([value for value in row if value]) >= 2:
            return index
    return 0


def _rows_to_dicts(
    rows: list[list[str]],
    header_index: int,
) -> tuple[list[str], list[dict[str, str]]]:
    if not rows:
        raise ValueError("The file has no rows.")
    header_row = rows[header_index]
    width = max(len(header_row), max((len(row) for row in rows[header_index + 1 :]), default=0))
    headers = _unique_headers(header_row, width)
    data_rows: list[dict[str, str]] = []
    for values in rows[header_index + 1 :]:
        row = {headers[index]: values[index] if index < len(values) else "" for index in range(len(headers))}
        if any(row.values()):
            data_rows.append(row)
    return headers, data_rows


def read_xlsx(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    from openpyxl import load_workbook

    try:
        workbook = load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:
        raise ValueError(f"Could not read the XLSX file: {exc}") from exc

    try:
        sheet = workbook.active
        if sheet is None:
            raise ValueError("The XLSX file has no active sheet.")
        raw_rows: list[list[str]] = []
        for values in sheet.iter_rows(values_only=True):
            row = [_cell_to_string(value) for value in values]
            if any(row):
                raw_rows.append(row)
    finally:
        workbook.close()

    if not raw_rows:
        raise ValueError("The XLSX file has no rows.")
    header_index = _find_header_index(raw_rows)
    return _rows_to_dicts(raw_rows, header_index)


def read_pdf(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    import pdfplumber

    try:
        with pdfplumber.open(path) as pdf:
            tables: list[list[list[str | None]]] = []
            text_rows: list[list[str]] = []
            for page in pdf.pages:
                for table in page.extract_tables() or []:
                    if table and len(table) >= 2:
                        tables.append(table)
                text = page.extract_text() or ""
                for line in text.splitlines():
                    parts = [part.strip() for part in line.split("  ") if part.strip()]
                    if len(parts) >= 3:
                        text_rows.append(parts)
    except Exception as exc:
        raise ValueError(f"Could not read the PDF file: {exc}") from exc

    if tables:
        table = max(tables, key=len)
        rows = [[_cell_to_string(cell) for cell in row] for row in table]
        header_index = _find_header_index(rows)
        return _rows_to_dicts(rows, header_index)

    if text_rows:
        header_index = _find_header_index(text_rows)
        return _rows_to_dicts(text_rows, header_index)

    raise ValueError(
        "Could not detect a table in this PDF. Export it as CSV or XLSX, or upload a "
        "PDF with selectable text and a visible table."
    )


def read_statement(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return read_csv(path)
    if suffix in {".xlsx", ".xlsm"}:
        return read_xlsx(path)
    if suffix == ".pdf":
        return read_pdf(path)
    raise ValueError("Unsupported file type. Upload CSV, XLSX, or PDF.")


def parse_amount(value: str) -> Decimal | None:
    text = value.strip().replace("\u00a0", "")
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]

    cleaned = "".join(character for character in text if character.isdigit() or character in ".,-+")
    if not cleaned:
        return None
    cleaned = cleaned.replace(",", "")
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        return None
    if negative:
        amount = -amount
    return amount


def parse_date(value: str, preferred_format: str) -> date | None:
    formats = [preferred_format, *[fmt for fmt in DATE_FORMATS if fmt != preferred_format]]
    for fmt in formats:
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


def row_fingerprint(
    journal_date: date,
    description: str,
    amount: Decimal,
    transaction_type: str,
    currency_code: str,
    account_id: UUID,
) -> str:
    payload = "|".join(
        [
            journal_date.isoformat(),
            normalize_merchant(description),
            f"{abs(amount):f}",
            transaction_type,
            currency_code,
            str(account_id),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def parse_rows(path: Path, mapping: ImportMapping, account: Account) -> ImportParseResult:
    _, raw_rows = read_statement(path)
    result = ImportParseResult()

    for index, raw in enumerate(raw_rows, start=2):
        date_value = raw.get(mapping.date_column, "")
        description = raw.get(mapping.description_column, "").strip()
        amount_value = raw.get(mapping.amount_column, "")
        currency_value = raw.get(mapping.currency_column, "").strip().upper() if mapping.currency_column else ""
        category_name = raw.get(mapping.category_column, "").strip() if mapping.category_column else ""

        if not date_value and not description and not amount_value:
            continue
        if not date_value:
            result.errors.append(f"Row {index}: missing date.")
            continue
        journal_date = parse_date(date_value, mapping.date_format)
        if journal_date is None:
            result.errors.append(f"Row {index}: could not parse date “{date_value}”.")
            continue
        if not description:
            result.errors.append(f"Row {index}: missing description.")
            continue
        amount = parse_amount(amount_value)
        if amount is None:
            result.errors.append(f"Row {index}: could not parse amount “{amount_value}”.")
            continue
        if amount == 0:
            continue

        if amount > 0:
            transaction_type = "withdrawal" if mapping.positive_is_expense else "deposit"
        else:
            transaction_type = "deposit" if mapping.positive_is_expense else "withdrawal"
        magnitude = abs(amount)

        currency_code = currency_value or account.currency_code
        if currency_code != account.currency_code:
            result.errors.append(
                f"Row {index}: currency {currency_code} does not match account currency {account.currency_code}."
            )
            continue

        result.rows.append(
            ParsedRow(
                journal_date=journal_date,
                description=description[:512],
                amount=magnitude,
                transaction_type=transaction_type,
                currency_code=currency_code,
                category_name=category_name,
                fingerprint=row_fingerprint(
                    journal_date,
                    description,
                    magnitude,
                    transaction_type,
                    currency_code,
                    account.id,
                ),
            )
        )

    return result


async def existing_fingerprints(
    session: AsyncSession,
    user: User,
    fingerprints: list[str],
) -> set[str]:
    if not fingerprints:
        return set()
    rows = await session.scalars(
        select(TransactionJournal.import_fingerprint).where(
            TransactionJournal.user_id == user.id,
            TransactionJournal.import_fingerprint.in_(fingerprints),
        )
    )
    return {fingerprint for fingerprint in rows if fingerprint is not None}


async def import_rows(
    session: AsyncSession,
    user: User,
    rows: list[ParsedRow],
    account: Account,
    *,
    batch_id: UUID | None = None,
) -> ImportSummary:
    batch_id = batch_id or uuid4()
    existing = await existing_fingerprints(session, user, [row.fingerprint for row in rows])
    prediction_cache: dict[str, MerchantPrediction | None] = {}
    imported = 0
    skipped = 0
    categorized = 0
    uncategorized = 0

    for row in rows:
        if row.fingerprint in existing:
            skipped += 1
            continue

        category_id = None
        category_source = None
        category_confidence = None

        if row.category_name:
            category = await get_category_by_name(session, user.id, row.category_name)
            if category is not None:
                category_id = category.id
                category_source = "import"
                category_confidence = Decimal("1.0000")
                categorized += 1

        if category_id is None:
            cache_key = normalize_merchant(row.description)
            if cache_key not in prediction_cache:
                prediction_cache[cache_key] = await predict_category(session, user, row.description)
            prediction = prediction_cache[cache_key]
            if prediction is not None and not prediction.needs_review:
                category_id = prediction.category_id
                category_source = prediction.source
                category_confidence = Decimal(str(round(prediction.confidence, 4)))
                categorized += 1
            else:
                uncategorized += 1

        if row.transaction_type == "withdrawal":
            expense = await ensure_system_account(session, user, AccountType.EXPENSE, row.currency_code)
            journal_type = JournalType.WITHDRAWAL
            splits = [
                Split(account_id=account.id, amount=-row.amount),
                Split(account_id=expense.id, amount=row.amount),
            ]
        else:
            income = await ensure_system_account(session, user, AccountType.REVENUE, row.currency_code)
            journal_type = JournalType.DEPOSIT
            splits = [
                Split(account_id=income.id, amount=-row.amount),
                Split(account_id=account.id, amount=row.amount),
            ]

        journal = await post_journal(
            session,
            user,
            journal_type=journal_type,
            journal_date=row.journal_date,
            description=row.description,
            currency_code=row.currency_code,
            splits=splits,
        )
        journal.category_id = category_id
        journal.category_source = category_source
        journal.category_confidence = category_confidence
        journal.import_fingerprint = row.fingerprint
        journal.import_batch_id = batch_id
        await session.flush()
        existing.add(row.fingerprint)
        imported += 1

    await session.commit()
    return ImportSummary(
        imported=imported,
        skipped=skipped,
        categorized=categorized,
        uncategorized=uncategorized,
        batch_id=batch_id,
    )
