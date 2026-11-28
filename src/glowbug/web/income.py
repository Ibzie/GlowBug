from __future__ import annotations

from datetime import date
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from glowbug.db.session import get_session
from glowbug.security import get_session_user
from glowbug.services.accounts import list_accounts
from glowbug.services.categories import ensure_categories
from glowbug.services.dates import user_today
from glowbug.services.income import (
    ASSET_TYPES,
    apply_due_income,
    create_schedule,
    delete_schedule,
    get_schedule,
    income_summary,
    update_schedule,
)
from glowbug.web.routes import flash, parse_decimal, render
from glowbug.web.transactions import parse_uuid

router = APIRouter(tags=["income"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


async def income_form_context(session: AsyncSession, user, today: date) -> dict:  # type: ignore[no-untyped-def]
    accounts = [account for account in await list_accounts(session, user.id) if account.type in ASSET_TYPES]
    return {
        "user": user,
        "today": today,
        "accounts": accounts,
        "categories": await ensure_categories(session, user),
    }


def income_form_values(form) -> dict:  # type: ignore[no-untyped-def]
    return {
        "name": str(form.get("name", "")),
        "amount": str(form.get("amount", "")),
        "account_id": str(form.get("account_id", "")),
        "category_id": str(form.get("category_id", "")),
        "day_of_month": str(form.get("day_of_month", "1")),
        "start_date": str(form.get("start_date", "")),
        "end_date": str(form.get("end_date", "")),
        "notes": str(form.get("notes", "")),
        "active": str(form.get("active", "")) == "1",
    }


def parse_income_form(form) -> tuple[dict[str, str], dict[str, Any]]:  # type: ignore[no-untyped-def]
    values = income_form_values(form)
    amount = parse_decimal(values["amount"])
    account_id = parse_uuid(values["account_id"])
    category_id = parse_uuid(values["category_id"])
    start_date = parse_date(values["start_date"])
    end_date = parse_date(values["end_date"]) if values["end_date"] else None
    try:
        day = int(values["day_of_month"] or "1")
    except ValueError:
        day = 0

    if amount is None or account_id is None or start_date is None or day == 0:
        raise ValueError("Enter a valid amount, account, day, and start date.")

    return values, {
        "name": values["name"],
        "amount": amount,
        "account_id": account_id,
        "category_id": category_id,
        "day_of_month": day,
        "start_date": start_date,
        "end_date": end_date,
        "notes": values["notes"],
        "active": values["active"],
    }


@router.get("/income", response_class=HTMLResponse, response_model=None)
async def income_index(request: Request, session: SessionDep) -> HTMLResponse | RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    today = user_today(user)
    summary = await income_summary(session, user, today)
    return render(
        request,
        "income/index.html",
        {
            "user": user,
            "today": today,
            "summary": summary,
            "views": summary.schedules,
        },
    )


@router.get("/income/new", response_class=HTMLResponse, response_model=None)
async def income_create_page(request: Request, session: SessionDep) -> HTMLResponse | RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    today = user_today(user)
    context = await income_form_context(session, user, today)
    context["values"] = {
        "day_of_month": "1",
        "start_date": today.isoformat(),
        "active": True,
    }
    return render(request, "income/form.html", context)


@router.post("/income", response_model=None)
async def income_create_submit(request: Request, session: SessionDep) -> HTMLResponse | RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    today = user_today(user)
    form = await request.form()
    values = income_form_values(form)
    try:
        _values, payload = parse_income_form(form)
        schedule = await create_schedule(session, user, **payload)
        await session.commit()
    except ValueError as exc:
        context = await income_form_context(session, user, today)
        context.update({"values": values, "error": str(exc)})
        return render(request, "income/form.html", context, status_code=400)

    flash(request, f"Income schedule “{schedule.name}” created.", "success")
    return RedirectResponse("/income", status_code=303)


@router.post("/income/apply-due", response_model=None)
async def income_apply_due(request: Request, session: SessionDep) -> RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    today = user_today(user)
    applied = await apply_due_income(session, user, today)
    await session.commit()
    if applied:
        flash(request, f"Added {len(applied)} scheduled income payment(s).", "success")
    else:
        flash(request, "No income payments were due.", "info")
    return RedirectResponse("/income", status_code=303)


@router.get("/income/{schedule_id}/edit", response_class=HTMLResponse, response_model=None)
async def income_edit_page(
    schedule_id: UUID,
    request: Request,
    session: SessionDep,
) -> HTMLResponse | RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    schedule = await get_schedule(session, user, schedule_id)
    if schedule is None:
        flash(request, "Income schedule not found.", "error")
        return RedirectResponse("/income", status_code=303)

    today = user_today(user)
    context = await income_form_context(session, user, today)
    context["schedule"] = schedule
    context["values"] = {
        "name": schedule.name,
        "amount": str(schedule.amount),
        "account_id": str(schedule.account_id),
        "category_id": str(schedule.category_id or ""),
        "day_of_month": str(schedule.day_of_month),
        "start_date": schedule.start_date.isoformat(),
        "end_date": schedule.end_date.isoformat() if schedule.end_date else "",
        "notes": schedule.notes or "",
        "active": schedule.active,
    }
    return render(request, "income/form.html", context)


@router.post("/income/{schedule_id}", response_model=None)
async def income_edit_submit(
    schedule_id: UUID,
    request: Request,
    session: SessionDep,
) -> HTMLResponse | RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    schedule = await get_schedule(session, user, schedule_id)
    if schedule is None:
        flash(request, "Income schedule not found.", "error")
        return RedirectResponse("/income", status_code=303)

    today = user_today(user)
    form = await request.form()
    values = income_form_values(form)
    try:
        _values, payload = parse_income_form(form)
        await update_schedule(session, user, schedule, **payload)
        await session.commit()
    except ValueError as exc:
        context = await income_form_context(session, user, today)
        context.update({"schedule": schedule, "values": values, "error": str(exc)})
        return render(request, "income/form.html", context, status_code=400)

    flash(request, "Income schedule updated.", "success")
    return RedirectResponse("/income", status_code=303)


@router.post("/income/{schedule_id}/delete", response_model=None)
async def income_delete(
    schedule_id: UUID,
    request: Request,
    session: SessionDep,
) -> RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    schedule = await get_schedule(session, user, schedule_id)
    if schedule is None:
        flash(request, "Income schedule not found.", "error")
        return RedirectResponse("/income", status_code=303)

    await delete_schedule(session, schedule)
    await session.commit()
    flash(request, "Income schedule deleted.", "success")
    return RedirectResponse("/income", status_code=303)
