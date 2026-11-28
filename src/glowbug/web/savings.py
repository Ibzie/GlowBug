from __future__ import annotations

from datetime import date
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from glowbug.db.session import get_session
from glowbug.security import get_session_user
from glowbug.services.accounts import get_primary_currency, get_user_currencies
from glowbug.services.dates import user_today
from glowbug.services.savings import (
    DEFAULT_GOAL_COLOR,
    add_contribution,
    apply_due_contributions,
    create_goal,
    delete_goal,
    get_goal,
    savings_summary,
    update_goal,
)
from glowbug.web.routes import flash, parse_decimal, render

router = APIRouter(tags=["savings"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


async def savings_form_context(session: AsyncSession, user, today: date) -> dict:  # type: ignore[no-untyped-def]
    primary = await get_primary_currency(session, user.id)
    return {
        "user": user,
        "today": today,
        "currencies": await get_user_currencies(session, user.id),
        "default_currency": primary.code if primary is not None else "",
        "default_color": DEFAULT_GOAL_COLOR,
    }


def goal_form_values(form) -> dict:  # type: ignore[no-untyped-def]
    return {
        "name": str(form.get("name", "")),
        "target_amount": str(form.get("target_amount", "")),
        "currency_code": str(form.get("currency_code", "")).upper(),
        "monthly_contribution": str(form.get("monthly_contribution", "")),
        "contribution_day": str(form.get("contribution_day", "1")),
        "start_date": str(form.get("start_date", "")),
        "target_date": str(form.get("target_date", "")),
        "color": str(form.get("color", DEFAULT_GOAL_COLOR)),
        "notes": str(form.get("notes", "")),
        "active": str(form.get("active", "")) == "1",
    }


def parse_goal_form(form) -> tuple[dict[str, str], dict[str, Any]]:  # type: ignore[no-untyped-def]
    values = goal_form_values(form)
    target_amount = parse_decimal(values["target_amount"])
    monthly = parse_decimal(values["monthly_contribution"])
    start_date = parse_date(values["start_date"])
    target_date = parse_date(values["target_date"]) if values["target_date"] else None
    try:
        day = int(values["contribution_day"] or "1")
    except ValueError:
        day = 0

    if target_amount is None or monthly is None or start_date is None or day == 0:
        raise ValueError("Enter valid amounts, a contribution day, and a start date.")

    return values, {
        "name": values["name"],
        "target_amount": target_amount,
        "currency_code": values["currency_code"],
        "monthly_contribution": monthly,
        "contribution_day": day,
        "start_date": start_date,
        "target_date": target_date,
        "color": values["color"],
        "notes": values["notes"],
        "active": values["active"],
    }


@router.get("/savings", response_class=HTMLResponse, response_model=None)
async def savings_index(request: Request, session: SessionDep) -> HTMLResponse | RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    today = user_today(user)
    summary = await savings_summary(session, user, today)
    return render(
        request,
        "savings/index.html",
        {
            "user": user,
            "today": today,
            "summary": summary,
            "views": summary.goals,
        },
    )


@router.get("/savings/new", response_class=HTMLResponse, response_model=None)
async def savings_create_page(request: Request, session: SessionDep) -> HTMLResponse | RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    today = user_today(user)
    context = await savings_form_context(session, user, today)
    context["values"] = {
        "currency_code": context["default_currency"],
        "contribution_day": "1",
        "start_date": today.isoformat(),
        "color": DEFAULT_GOAL_COLOR,
        "active": True,
    }
    return render(request, "savings/form.html", context)


@router.post("/savings", response_model=None)
async def savings_create_submit(request: Request, session: SessionDep) -> HTMLResponse | RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    today = user_today(user)
    form = await request.form()
    values = goal_form_values(form)
    try:
        _values, payload = parse_goal_form(form)
        goal = await create_goal(session, user, **payload)
        await session.commit()
    except ValueError as exc:
        context = await savings_form_context(session, user, today)
        context.update({"values": values, "error": str(exc)})
        return render(request, "savings/form.html", context, status_code=400)

    flash(request, f"Savings goal “{goal.name}” created.", "success")
    return RedirectResponse("/savings", status_code=303)


@router.post("/savings/apply-due", response_model=None)
async def savings_apply_due(request: Request, session: SessionDep) -> RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    today = user_today(user)
    created = await apply_due_contributions(session, user, today)
    await session.commit()
    if created:
        flash(request, f"Applied {len(created)} scheduled savings contribution(s).", "success")
    else:
        flash(request, "No savings contributions were due.", "info")
    return RedirectResponse("/savings", status_code=303)


@router.get("/savings/{goal_id}/edit", response_class=HTMLResponse, response_model=None)
async def savings_edit_page(
    goal_id: UUID,
    request: Request,
    session: SessionDep,
) -> HTMLResponse | RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    goal = await get_goal(session, user, goal_id)
    if goal is None:
        flash(request, "Savings goal not found.", "error")
        return RedirectResponse("/savings", status_code=303)

    today = user_today(user)
    context = await savings_form_context(session, user, today)
    context["goal"] = goal
    context["values"] = {
        "name": goal.name,
        "target_amount": str(goal.target_amount),
        "currency_code": goal.currency_code,
        "monthly_contribution": str(goal.monthly_contribution),
        "contribution_day": str(goal.contribution_day),
        "start_date": goal.start_date.isoformat(),
        "target_date": goal.target_date.isoformat() if goal.target_date else "",
        "color": goal.color,
        "notes": goal.notes or "",
        "active": goal.active,
    }
    return render(request, "savings/form.html", context)


@router.post("/savings/{goal_id}", response_model=None)
async def savings_edit_submit(
    goal_id: UUID,
    request: Request,
    session: SessionDep,
) -> HTMLResponse | RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    goal = await get_goal(session, user, goal_id)
    if goal is None:
        flash(request, "Savings goal not found.", "error")
        return RedirectResponse("/savings", status_code=303)

    today = user_today(user)
    form = await request.form()
    values = goal_form_values(form)
    try:
        _values, payload = parse_goal_form(form)
        await update_goal(session, user, goal, **payload)
        await session.commit()
    except ValueError as exc:
        context = await savings_form_context(session, user, today)
        context.update({"goal": goal, "values": values, "error": str(exc)})
        return render(request, "savings/form.html", context, status_code=400)

    flash(request, "Savings goal updated.", "success")
    return RedirectResponse("/savings", status_code=303)


@router.post("/savings/{goal_id}/contribute", response_model=None)
async def savings_contribute(
    goal_id: UUID,
    request: Request,
    session: SessionDep,
) -> RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    goal = await get_goal(session, user, goal_id)
    if goal is None:
        flash(request, "Savings goal not found.", "error")
        return RedirectResponse("/savings", status_code=303)

    form = await request.form()
    amount = parse_decimal(str(form.get("amount", "")))
    if amount is None or amount <= 0:
        flash(request, "Enter a contribution amount greater than zero.", "error")
        return RedirectResponse("/savings", status_code=303)

    today = user_today(user)
    await add_contribution(
        session,
        user,
        goal,
        amount=amount,
        contributed_on=today,
        note=str(form.get("note", "")) or None,
    )
    await session.commit()
    flash(request, f"Added {goal.currency_code} contribution to “{goal.name}”.", "success")
    return RedirectResponse("/savings", status_code=303)


@router.post("/savings/{goal_id}/delete", response_model=None)
async def savings_delete(
    goal_id: UUID,
    request: Request,
    session: SessionDep,
) -> RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    goal = await get_goal(session, user, goal_id)
    if goal is None:
        flash(request, "Savings goal not found.", "error")
        return RedirectResponse("/savings", status_code=303)

    await delete_goal(session, goal)
    await session.commit()
    flash(request, "Savings goal deleted.", "success")
    return RedirectResponse("/savings", status_code=303)
