import shutil
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fpdf import FPDF
from openpyxl import Workbook
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
    _parse_layout_text,
    import_rows,
    parse_amount,
    parse_date,
    parse_rows,
    read_statement,
)


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


def test_parse_layout_text() -> None:
    rows = _parse_layout_text("Date        Description        Amount\n2026-01-15  NETFLIX            15.99\n")
    assert rows[0] == ["Date", "Description", "Amount"]
    assert rows[1] == ["2026-01-15", "NETFLIX", "15.99"]


@pytest.mark.skipif(shutil.which("pdftotext") is None, reason="pdftotext is not installed")
def test_read_pdf_layout_fallback(tmp_path: Path) -> None:
    path = tmp_path / "layout.pdf"
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=10)
    for values in (
        ("Date", "Description", "Amount"),
        ("2026-01-15", "NETFLIX", "15.99"),
        ("2026-01-20", "COFFEE SHOP", "4.50"),
    ):
        pdf.cell(40, 10, values[0])
        pdf.cell(60, 10, values[1])
        pdf.cell(30, 10, values[2])
        pdf.ln()
    pdf.output(str(path))

    headers, rows = read_statement(path)
    assert headers[:3] == ["Date", "Description", "Amount"]
    assert len(rows) == 2
    assert rows[0]["Description"] == "NETFLIX"


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
