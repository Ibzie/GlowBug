from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from glowbug.db.session import get_session
from glowbug.models.account import Account
from glowbug.security import get_session_user
from glowbug.services.accounts import list_accounts
from glowbug.services.categories import ensure_categories
from glowbug.services.dashboards import default_range
from glowbug.services.transactions import (
    TRANSACTION_TYPES,
    assign_journal_category,
    create_transaction,
    get_journal,
    list_transactions,
)
from glowbug.web.routes import flash, parse_decimal, render

router = APIRouter(tags=["transactions"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def parse_uuid(value: str | None) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


async def transaction_form_context(session: AsyncSession, user) -> dict:  # type: ignore[no-untyped-def]
    categories = await ensure_categories(session, user)
    accounts = await list_accounts(session, user.id)
    return {
        "user": user,
        "accounts": accounts,
        "categories": categories,
        "transaction_types": TRANSACTION_TYPES,
    }


@router.get("/transactions", response_class=HTMLResponse, response_model=None)
async def transactions_index(
    request: Request,
    session: SessionDep,
) -> HTMLResponse | RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    categories = await ensure_categories(session, user)
    range_key = request.query_params.get("range", "3M").upper()
    start, end = default_range(user, range_key)
    category_filter = parse_uuid(request.query_params.get("category"))
    uncategorized = request.query_params.get("uncategorized") == "1"
    views = await list_transactions(
        session,
        user,
        start,
        end,
        category_id=category_filter,
        uncategorized=uncategorized,
    )

    return render(
        request,
        "transactions/index.html",
        {
            "user": user,
            "views": views,
            "categories": categories,
            "range_key": range_key,
            "start": start,
            "end": end,
            "category_filter": category_filter,
            "uncategorized": uncategorized,
        },
    )


@router.get("/transactions/new", response_class=HTMLResponse, response_model=None)
async def transaction_create_page(
    request: Request,
    session: SessionDep,
) -> HTMLResponse | RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    context = await transaction_form_context(session, user)
    context["values"] = {"date": date.today().isoformat()}
    return render(request, "transactions/form.html", context)


@router.post("/transactions", response_model=None)
async def transaction_create_submit(
    request: Request,
    session: SessionDep,
) -> HTMLResponse | RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    form = await request.form()
    transaction_type = str(form.get("type", ""))
    description = str(form.get("description", "")).strip()
    date_string = str(form.get("date", ""))
    amount = parse_decimal(str(form.get("amount", "")))
    account_id = parse_uuid(str(form.get("account_id", "")))
    destination_account_id = parse_uuid(str(form.get("destination_account_id", "")))
    category_id = parse_uuid(str(form.get("category_id", "")))
    notes = str(form.get("notes", "")).strip() or None
    values = {
        "type": transaction_type,
        "description": description,
        "date": date_string,
        "amount": str(form.get("amount", "")),
        "account_id": str(form.get("account_id", "")),
        "destination_account_id": str(form.get("destination_account_id", "")),
        "category_id": str(form.get("category_id", "")),
        "notes": notes or "",
    }
    errors: list[str] = []

    try:
        journal_date = date.fromisoformat(date_string)
    except ValueError:
        journal_date = None
        errors.append("Enter a valid date.")

    if not description:
        errors.append("Enter a description.")
    if amount is None or amount <= 0:
        errors.append("Enter an amount greater than zero.")
    if account_id is None:
        errors.append("Choose an account.")

    account = await session.get(Account, account_id) if account_id is not None else None
    if account is None or account.user_id != user.id:
        errors.append("Choose a valid account.")

    if errors or journal_date is None or amount is None or account is None:
        context = await transaction_form_context(session, user)
        context.update({"values": values, "error": " ".join(errors)})
        return render(request, "transactions/form.html", context, status_code=400)

    try:
        await create_transaction(
            session,
            user,
            transaction_type=transaction_type,
            journal_date=journal_date,
            description=description,
            amount=amount,
            currency_code=account.currency_code,
            account_id=account.id,
            destination_account_id=destination_account_id,
            category_id=category_id,
            notes=notes,
        )
    except ValueError as exc:
        context = await transaction_form_context(session, user)
        context.update({"values": values, "error": str(exc)})
        return render(request, "transactions/form.html", context, status_code=400)

    flash(request, "Transaction created.", "success")
    return RedirectResponse("/transactions", status_code=303)


@router.post("/transactions/{journal_id}/category", response_model=None)
async def transaction_assign_category(
    journal_id: UUID,
    request: Request,
    session: SessionDep,
) -> RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    form = await request.form()
    category_id = parse_uuid(str(form.get("category_id", "")))
    remember = str(form.get("remember", "")) == "1"
    if category_id is None:
        flash(request, "Choose a category.", "error")
        return RedirectResponse("/transactions", status_code=303)

    journal = await get_journal(session, user, journal_id)
    if journal is None:
        flash(request, "Transaction not found.", "error")
        return RedirectResponse("/transactions", status_code=303)

    try:
        updated = await assign_journal_category(
            session,
            user,
            journal,
            category_id,
            remember=remember,
        )
    except ValueError as exc:
        flash(request, str(exc), "error")
        return RedirectResponse("/transactions", status_code=303)

    message = "Category saved."
    if updated:
        message += f" {updated} similar transaction(s) were updated too."
    flash(request, message, "success")
    return RedirectResponse("/transactions", status_code=303)
