import shutil
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fpdf import FPDF
from openpyxl import Workbook
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from glowbug.enums import AccountType
from glowbug.models.currency import Currency
from glowbug.models.transaction import TransactionJournal
from glowbug.models.user import User
from glowbug.services.accounts import create_account, set_user_currencies
from glowbug.services.categories import get_category_by_name, seed_default_categories
from glowbug.services.importer import (
    ImportMapping,
    import_rows,
    parse_amount,
    parse_date,
    parse_rows,
    read_statement,
)

requires_tesseract = pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract is not installed")


async def ensure_currency(session: AsyncSession, code: str, symbol: str) -> None:
    if await session.get(Currency, code) is None:
        session.add(Currency(code=code, name=code, symbol=symbol, decimal_places=2))
        await session.flush()


async def make_user(session: AsyncSession) -> User:
    await ensure_currency(session, "USD", "$")
    user = User(
        email="import@example.com",
        password_hash="not-used",
        display_name="Import",
        timezone="UTC",
        locale="en_US",
    )
    session.add(user)
    await session.flush()
    await set_user_currencies(session, user, enabled_codes=["USD"], primary_code="USD")
    await seed_default_categories(session, user)
    await session.commit()
    return user


def test_parse_amount_and_date() -> None:
    assert parse_amount("$1,234.56") == Decimal("1234.56")
    assert parse_amount("(12.34)") == Decimal("-12.34")
    assert parse_date("15/01/2026", "%d/%m/%Y") == date(2026, 1, 15)


def test_read_xlsx_statement(tmp_path: Path) -> None:
    path = tmp_path / "statement.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(["My bank statement"])
    sheet.append([])
    sheet.append(["Date", "Description", "Amount"])
    sheet.append(["2026-01-15", "NETFLIX", 15.99])
    sheet.append(["2026-01-20", "COFFEE SHOP", 4.5])
    workbook.save(path)

    headers, rows = read_statement(path)
    assert headers[:3] == ["Date", "Description", "Amount"]
    assert len(rows) == 2
    assert rows[0]["Description"] == "NETFLIX"
    assert rows[0]["Amount"] == "15.99"


def test_parse_date_accepts_compact_ocr_date() -> None:
    assert parse_date("20260115", "%Y-%m-%d") == date(2026, 1, 15)


@requires_tesseract
def test_read_pdf_statement(tmp_path: Path) -> None:
    path = tmp_path / "statement.pdf"
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=10)
    with pdf.table() as table:
        row = table.row()
        for value in ("Date", "Description", "Amount"):
            row.cell(value)
        for values in (
            ("2026-01-15", "NETFLIX", "15.99"),
            ("2026-01-20", "COFFEE SHOP", "4.50"),
        ):
            row = table.row()
            for value in values:
                row.cell(value)
    pdf.output(str(path))

    headers, rows = read_statement(path)
    assert headers[:3] == ["Date", "Description", "Amount"]
    assert len(rows) == 2
    assert rows[0]["Description"] == "NETFLIX"
    assert rows[0]["Amount"] == "15.99"


@requires_tesseract
def test_read_scanned_pdf_without_text_layer(tmp_path: Path) -> None:
    path = tmp_path / "scan.pdf"
    image = Image.new("RGB", (1800, 900), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=44)

    draw.text((80, 80), "Date", fill="black", font=font)
    draw.text((520, 80), "Description", fill="black", font=font)
    draw.text((1300, 80), "Amount", fill="black", font=font)

    rows = (
        ("2026-01-15", "NETFLIX", "15.99"),
        ("2026-01-20", "COFFEE SHOP", "4.50"),
    )
    for index, values in enumerate(rows, start=1):
        top = 80 + index * 110
        draw.text((80, top), values[0], fill="black", font=font)
        draw.text((520, top), values[1], fill="black", font=font)
        draw.text((1300, top), values[2], fill="black", font=font)

    image.save(path, "PDF", resolution=150)

    headers, parsed = read_statement(path)
    assert headers[:3] == ["Date", "Description", "Amount"]
    assert len(parsed) == 2
    assert parsed[0]["Description"] == "NETFLIX"
    assert parsed[0]["Amount"] == "15.99"
    assert parsed[1]["Description"].replace(" ", "").upper() == "COFFEESHOP"


@requires_tesseract
def test_read_pdf_repairs_merged_header_cells(tmp_path: Path) -> None:
    path = tmp_path / "paragraph.pdf"
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Courier", size=10)
    for line in (
        "Date Description Amount",
        "2026-01-15 NETFLIX 15.99",
        "2026-01-20 COFFEE SHOP 4.50",
    ):
        pdf.cell(0, 8, line, new_x="LMARGIN", new_y="NEXT")
    pdf.output(str(path))

    headers, rows = read_statement(path)
    assert headers[:3] == ["Date", "Description", "Amount"]
    assert len(rows) == 2
    assert rows[1]["Description"] == "COFFEE SHOP"
    assert rows[1]["Amount"] == "4.50"


@requires_tesseract
def test_read_pdf_drops_repeated_page_headers(tmp_path: Path) -> None:
    path = tmp_path / "pages.pdf"
    pdf = FPDF()
    pdf.set_font("Helvetica", size=10)
    for page in range(2):
        pdf.add_page()
        with pdf.table() as table:
            row = table.row()
            for value in ("Date", "Description", "Amount"):
                row.cell(value)
            row = table.row()
            for value in (f"2026-0{page + 1}-15", f"MERCHANT {page + 1}", f"{page + 1}.00"):
                row.cell(value)
    pdf.output(str(path))

    _, rows = read_statement(path)
    assert len(rows) == 2
    assert [row["Description"] for row in rows] == ["MERCHANT 1", "MERCHANT 2"]


def test_read_statement_rejects_unknown_type(tmp_path: Path) -> None:
    path = tmp_path / "statement.txt"
    path.write_text("not a statement", encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported file type"):
        read_statement(path)


async def test_import_is_idempotent_and_categorised(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    user = await make_user(session)
    bank = await create_account(
        session,
        user,
        account_type=AccountType.ASSET,
        name="Bank",
        currency_code="USD",
    )
    subscriptions = await get_category_by_name(session, user.id, "Subscriptions")
    assert subscriptions is not None

    csv_path = tmp_path / "statement.csv"
    csv_path.write_text(
        "date,description,amount,category\n"
        "2026-01-15,NETFLIX,15.99,Subscriptions\n"
        "2026-02-15,NETFLIX,15.99,Subscriptions\n"
        "2026-03-15,NETFLIX,15.99,Subscriptions\n",
        encoding="utf-8",
    )
    mapping = ImportMapping(
        date_column="date",
        description_column="description",
        amount_column="amount",
        account_id=bank.id,
        category_column="category",
    )

    parsed = parse_rows(csv_path, mapping, bank)
    assert not parsed.errors
    assert len(parsed.rows) == 3
    assert all(row.transaction_type == "withdrawal" for row in parsed.rows)

    summary = await import_rows(session, user, parsed.rows, bank)
    assert summary.imported == 3
    assert summary.skipped == 0
    assert summary.categorized == 3
    assert summary.uncategorized == 0

    journals = list(await session.scalars(select(TransactionJournal).where(TransactionJournal.user_id == user.id)))
    assert len(journals) == 3
    assert all(journal.category_id == subscriptions.id for journal in journals)
    assert all(journal.import_fingerprint for journal in journals)
    assert len({journal.import_batch_id for journal in journals}) == 1

    second = await import_rows(session, user, parsed.rows, bank)
    assert second.imported == 0
    assert second.skipped == 3
    count = await session.scalar(select(func.count(TransactionJournal.id)).where(TransactionJournal.user_id == user.id))
    assert count == 3


async def test_duplicate_rows_within_file_are_skipped(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    user = await make_user(session)
    bank = await create_account(
        session,
        user,
        account_type=AccountType.ASSET,
        name="Bank",
        currency_code="USD",
    )
    csv_path = tmp_path / "duplicate.csv"
    csv_path.write_text(
        "date,description,amount\n2026-01-15,COFFEE SHOP,4.50\n2026-01-15,COFFEE SHOP,4.50\n",
        encoding="utf-8",
    )
    mapping = ImportMapping(
        date_column="date",
        description_column="description",
        amount_column="amount",
        account_id=bank.id,
    )
    parsed = parse_rows(csv_path, mapping, bank)
    assert len(parsed.rows) == 2

    summary = await import_rows(session, user, parsed.rows, bank)
    assert summary.imported == 1
    assert summary.skipped == 1
