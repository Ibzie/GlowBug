from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from glowbug.db.session import get_session
from glowbug.models.account import Account
from glowbug.security import get_session_user
from glowbug.services.accounts import list_accounts
from glowbug.services.categories import ensure_categories
from glowbug.services.categorizer import predict_category
from glowbug.services.importer import (
    DATE_FORMATS,
    ImportMapping,
    existing_fingerprints,
    import_rows,
    parse_rows,
    read_statement,
)
from glowbug.services.subscriptions import detect_candidates
from glowbug.web.routes import flash, render
from glowbug.web.transactions import parse_uuid

router = APIRouter(tags=["imports"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]

IMPORT_DIR = Path("storage/imports")
SUPPORTED_EXTENSIONS = {".csv", ".xlsx", ".xlsm", ".pdf"}


def import_path(token: str) -> Path | None:
    if not token or not token.isalnum():
        return None
    for path in IMPORT_DIR.glob(f"{token}.*"):
        if path.is_file():
            return path
    return None


def mapping_from_form(form) -> ImportMapping:  # type: ignore[no-untyped-def]
    account_id = parse_uuid(str(form.get("account_id", "")))
    if account_id is None:
        raise ValueError("Choose an account.")
    date_format = str(form.get("date_format", "%Y-%m-%d"))
    if date_format not in DATE_FORMATS:
        date_format = "%Y-%m-%d"
    return ImportMapping(
        date_column=str(form.get("date_column", "")),
        description_column=str(form.get("description_column", "")),
        amount_column=str(form.get("amount_column", "")),
        account_id=account_id,
        date_format=date_format,
        currency_column=str(form.get("currency_column", "")),
        category_column=str(form.get("category_column", "")),
        positive_is_expense=str(form.get("sign_convention", "expense")) == "expense",
    )


@router.get("/transactions/import", response_class=HTMLResponse, response_model=None)
async def import_upload_page(request: Request, session: SessionDep) -> HTMLResponse | RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    accounts = await list_accounts(session, user.id)
    return render(
        request,
        "transactions/import_upload.html",
        {"user": user, "accounts": accounts},
    )


@router.post("/transactions/import/upload", response_model=None)
async def import_upload_submit(request: Request, session: SessionDep) -> HTMLResponse | RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    form = await request.form()
    upload = form.get("file")
    accounts = await list_accounts(session, user.id)
    if not isinstance(upload, UploadFile):
        return render(
            request,
            "transactions/import_upload.html",
            {"user": user, "accounts": accounts, "error": "Choose a CSV, XLSX, or PDF statement."},
            status_code=400,
        )

    content = await upload.read()
    if not content:
        return render(
            request,
            "transactions/import_upload.html",
            {"user": user, "accounts": accounts, "error": "The uploaded file is empty."},
            status_code=400,
        )

    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        return render(
            request,
            "transactions/import_upload.html",
            {
                "user": user,
                "accounts": accounts,
                "error": "Upload a CSV, XLSX, or PDF statement.",
            },
            status_code=400,
        )

    IMPORT_DIR.mkdir(parents=True, exist_ok=True)
    token = uuid4().hex
    path = IMPORT_DIR / f"{token}{suffix}"
    path.write_bytes(content)

    try:
        headers, rows = await run_in_threadpool(read_statement, path)
    except ValueError as exc:
        path.unlink(missing_ok=True)
        return render(
            request,
            "transactions/import_upload.html",
            {"user": user, "accounts": accounts, "error": str(exc)},
            status_code=400,
        )

    request.session["import_token"] = token
    return render(
        request,
        "transactions/import_map.html",
        {
            "user": user,
            "accounts": accounts,
            "headers": headers,
            "sample_rows": rows[:5],
            "token": token,
            "filename": upload.filename or "statement.csv",
            "date_formats": DATE_FORMATS,
        },
    )


@router.post("/transactions/import/preview", response_model=None)
async def import_preview(request: Request, session: SessionDep) -> HTMLResponse | RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    form = await request.form()
    token = str(form.get("token", "")) or str(request.session.get("import_token", ""))
    path = import_path(token)
    accounts = await list_accounts(session, user.id)

    try:
        mapping = mapping_from_form(form)
    except ValueError as exc:
        return render(
            request,
            "transactions/import_upload.html",
            {"user": user, "accounts": accounts, "error": str(exc)},
            status_code=400,
        )

    account = await session.get(Account, mapping.account_id)
    if account is None or account.user_id != user.id:
        return render(
            request,
            "transactions/import_upload.html",
            {"user": user, "accounts": accounts, "error": "Choose a valid account."},
            status_code=400,
        )
    if path is None or not path.exists():
        return render(
            request,
            "transactions/import_upload.html",
            {"user": user, "accounts": accounts, "error": "The upload expired. Please upload the file again."},
            status_code=400,
        )

    parsed = await run_in_threadpool(parse_rows, path, mapping, account)
    existing = await existing_fingerprints(session, user, [row.fingerprint for row in parsed.rows])
    seen: set[str] = set()
    duplicate_count = 0
    preview_rows = []
    for index, row in enumerate(parsed.rows):
        duplicate = row.fingerprint in existing or row.fingerprint in seen
        if duplicate:
            duplicate_count += 1
        seen.add(row.fingerprint)
        suggestion = None
        if index < 20:
            prediction = await predict_category(session, user, row.description)
            if prediction is not None and not prediction.needs_review:
                suggestion = prediction.category_name
        preview_rows.append({"row": row, "duplicate": duplicate, "suggestion": suggestion})

    return render(
        request,
        "transactions/import_preview.html",
        {
            "user": user,
            "account": account,
            "mapping": mapping,
            "token": token,
            "rows": preview_rows[:20],
            "total_rows": len(parsed.rows),
            "duplicate_count": duplicate_count,
            "errors": parsed.errors[:20],
            "date_formats": DATE_FORMATS,
        },
    )


@router.post("/transactions/import/confirm", response_model=None)
async def import_confirm(request: Request, session: SessionDep) -> HTMLResponse | RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    form = await request.form()
    token = str(form.get("token", ""))
    path = import_path(token)
    try:
        mapping = mapping_from_form(form)
    except ValueError as exc:
        flash(request, str(exc), "error")
        return RedirectResponse("/transactions/import", status_code=303)

    account = await session.get(Account, mapping.account_id)
    if account is None or account.user_id != user.id or path is None or not path.exists():
        flash(request, "The upload expired. Please upload the file again.", "error")
        return RedirectResponse("/transactions/import", status_code=303)

    parsed = await run_in_threadpool(parse_rows, path, mapping, account)
    summary = await import_rows(session, user, parsed.rows, account)
    path.unlink(missing_ok=True)
    request.session.pop("import_token", None)

    categories = {category.id: category for category in await ensure_categories(session, user)}
    account_map = {acc.id: acc for acc in await list_accounts(session, user.id)}
    candidates = await detect_candidates(session, user)
    candidate_views = [
        {
            "candidate": candidate,
            "category": categories.get(candidate.category_id) if candidate.category_id else None,
            "account": account_map.get(candidate.account_id) if candidate.account_id else None,
        }
        for candidate in candidates
    ]

    return render(
        request,
        "transactions/import_result.html",
        {
            "user": user,
            "summary": summary,
            "candidate_views": candidate_views,
            "errors": parsed.errors[:20],
            "today": date.today(),
        },
    )
