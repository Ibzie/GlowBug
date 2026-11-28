from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from glowbug.db.session import get_session
from glowbug.security import get_session_user
from glowbug.services.accounts import get_user_currencies, list_accounts
from glowbug.services.categories import ensure_categories
from glowbug.services.subscriptions import (
    FREQUENCIES,
    create_subscription,
    delete_subscription,
    dismiss_candidate,
    get_subscription,
    list_subscriptions,
    update_subscription,
)
from glowbug.web.routes import flash, parse_decimal, render
from glowbug.web.transactions import parse_uuid

router = APIRouter(tags=["subscriptions"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def subscription_form_context(session: AsyncSession, user) -> dict:  # type: ignore[no-untyped-def]
    return {
        "user": user,
        "accounts": await list_accounts(session, user.id),
        "categories": await ensure_categories(session, user),
        "currencies": await get_user_currencies(session, user.id),
        "frequencies": FREQUENCIES,
    }


@router.get("/subscriptions", response_class=HTMLResponse, response_model=None)
async def subscriptions_index(
    request: Request,
    session: SessionDep,
) -> HTMLResponse | RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    subscriptions = await list_subscriptions(session, user)
    return render(
        request,
        "subscriptions/index.html",
        {"user": user, "subscriptions": subscriptions},
    )


@router.get("/subscriptions/new", response_class=HTMLResponse, response_model=None)
async def subscription_create_page(
    request: Request,
    session: SessionDep,
) -> HTMLResponse | RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    context = await subscription_form_context(session, user)
    context["values"] = {"next_due_date": date.today().isoformat(), "active": True}
    return render(request, "subscriptions/form.html", context)


@router.post("/subscriptions", response_model=None)
async def subscription_create_submit(
    request: Request,
    session: SessionDep,
) -> HTMLResponse | RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    form = await request.form()
    name = str(form.get("name", ""))
    amount = parse_decimal(str(form.get("amount", "")))
    currency_code = str(form.get("currency_code", "")).upper()
    frequency = str(form.get("frequency", "monthly"))
    date_string = str(form.get("next_due_date", ""))
    account_id = parse_uuid(str(form.get("account_id", "")))
    category_id = parse_uuid(str(form.get("category_id", "")))
    active = str(form.get("active", "")) == "1"
    values = {
        "name": name,
        "amount": str(form.get("amount", "")),
        "currency_code": currency_code,
        "frequency": frequency,
        "next_due_date": date_string,
        "account_id": str(account_id or ""),
        "category_id": str(category_id or ""),
        "active": active,
    }

    try:
        next_due_date = date.fromisoformat(date_string)
    except ValueError:
        next_due_date = None

    if amount is None or next_due_date is None:
        context = await subscription_form_context(session, user)
        context.update({"values": values, "error": "Enter a valid amount and next due date."})
        return render(request, "subscriptions/form.html", context, status_code=400)

    try:
        subscription, created = await create_subscription(
            session,
            user,
            name=name,
            amount=amount,
            currency_code=currency_code,
            frequency=frequency,
            next_due_date=next_due_date,
            account_id=account_id,
            category_id=category_id,
            source="manual",
        )
        if subscription is not None:
            subscription.active = active
        await session.commit()
    except ValueError as exc:
        context = await subscription_form_context(session, user)
        context.update({"values": values, "error": str(exc)})
        return render(request, "subscriptions/form.html", context, status_code=400)

    flash(request, "Subscription created." if created else "A matching subscription already exists.", "success")
    return RedirectResponse("/subscriptions", status_code=303)


@router.post("/subscriptions/from-candidate", response_model=None)
async def subscription_from_candidate(
    request: Request,
    session: SessionDep,
) -> RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    form = await request.form()
    amount = parse_decimal(str(form.get("amount", "")))
    date_string = str(form.get("next_due_date", ""))
    try:
        next_due_date = date.fromisoformat(date_string)
    except ValueError:
        next_due_date = None
    if amount is None or next_due_date is None:
        flash(request, "The subscription candidate was invalid.", "error")
        return RedirectResponse("/transactions", status_code=303)

    try:
        _subscription, created = await create_subscription(
            session,
            user,
            name=str(form.get("name", "")),
            amount=amount,
            currency_code=str(form.get("currency_code", "")).upper(),
            frequency=str(form.get("frequency", "monthly")),
            next_due_date=next_due_date,
            account_id=parse_uuid(str(form.get("account_id", ""))),
            category_id=parse_uuid(str(form.get("category_id", ""))),
            merchant_key=str(form.get("merchant_key", "")) or None,
            source="detected",
        )
        await session.commit()
    except ValueError as exc:
        flash(request, str(exc), "error")
        return RedirectResponse("/transactions", status_code=303)

    flash(request, "Subscription created." if created else "This subscription is already registered.", "success")
    return RedirectResponse("/subscriptions", status_code=303)


@router.post("/subscriptions/dismiss-candidate", response_model=None)
async def subscription_dismiss_candidate(
    request: Request,
    session: SessionDep,
) -> RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    form = await request.form()
    amount = parse_decimal(str(form.get("amount", "")))
    merchant_key = str(form.get("merchant_key", "")) or None
    try:
        next_due_date = date.fromisoformat(str(form.get("next_due_date", "")))
    except ValueError:
        next_due_date = None
    if amount is None or next_due_date is None or merchant_key is None:
        flash(request, "The subscription candidate was invalid.", "error")
        return RedirectResponse("/transactions", status_code=303)

    try:
        await dismiss_candidate(
            session,
            user,
            name=str(form.get("name", "")),
            merchant_key=merchant_key,
            amount=amount,
            currency_code=str(form.get("currency_code", "")).upper(),
            frequency=str(form.get("frequency", "monthly")),
            next_due_date=next_due_date,
        )
        await session.commit()
    except ValueError as exc:
        flash(request, str(exc), "error")
        return RedirectResponse("/transactions", status_code=303)

    flash(request, "Suggestion dismissed. It won't be offered again.", "success")
    return RedirectResponse("/transactions", status_code=303)


@router.get("/subscriptions/{subscription_id}/edit", response_class=HTMLResponse, response_model=None)
async def subscription_edit_page(
    subscription_id: UUID,
    request: Request,
    session: SessionDep,
) -> HTMLResponse | RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    subscription = await get_subscription(session, user, subscription_id)
    if subscription is None:
        flash(request, "Subscription not found.", "error")
        return RedirectResponse("/subscriptions", status_code=303)

    context = await subscription_form_context(session, user)
    context["subscription"] = subscription
    context["values"] = {
        "name": subscription.name,
        "amount": str(subscription.amount),
        "currency_code": subscription.currency_code,
        "frequency": subscription.frequency,
        "next_due_date": subscription.next_due_date.isoformat(),
        "account_id": str(subscription.account_id or ""),
        "category_id": str(subscription.category_id or ""),
        "active": subscription.active,
    }
    return render(request, "subscriptions/form.html", context)


@router.post("/subscriptions/{subscription_id}", response_model=None)
async def subscription_edit_submit(
    subscription_id: UUID,
    request: Request,
    session: SessionDep,
) -> HTMLResponse | RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    subscription = await get_subscription(session, user, subscription_id)
    if subscription is None:
        flash(request, "Subscription not found.", "error")
        return RedirectResponse("/subscriptions", status_code=303)

    form = await request.form()
    name = str(form.get("name", ""))
    amount = parse_decimal(str(form.get("amount", "")))
    currency_code = str(form.get("currency_code", "")).upper()
    frequency = str(form.get("frequency", "monthly"))
    date_string = str(form.get("next_due_date", ""))
    account_id = parse_uuid(str(form.get("account_id", "")))
    category_id = parse_uuid(str(form.get("category_id", "")))
    active = str(form.get("active", "")) == "1"
    try:
        next_due_date = date.fromisoformat(date_string)
    except ValueError:
        next_due_date = None

    if amount is None or next_due_date is None:
        context = await subscription_form_context(session, user)
        context.update(
            {
                "subscription": subscription,
                "values": {
                    "name": name,
                    "amount": str(form.get("amount", "")),
                    "currency_code": currency_code,
                    "frequency": frequency,
                    "next_due_date": date_string,
                    "account_id": str(account_id or ""),
                    "category_id": str(category_id or ""),
                    "active": active,
                },
                "error": "Enter a valid amount and next due date.",
            }
        )
        return render(request, "subscriptions/form.html", context, status_code=400)

    try:
        await update_subscription(
            session,
            subscription,
            name=name,
            amount=amount,
            currency_code=currency_code,
            frequency=frequency,
            next_due_date=next_due_date,
            account_id=account_id,
            category_id=category_id,
            active=active,
        )
        await session.commit()
    except ValueError as exc:
        context = await subscription_form_context(session, user)
        context.update({"subscription": subscription, "error": str(exc)})
        return render(request, "subscriptions/form.html", context, status_code=400)

    flash(request, "Subscription updated.", "success")
    return RedirectResponse("/subscriptions", status_code=303)


@router.post("/subscriptions/{subscription_id}/delete", response_model=None)
async def subscription_delete(
    subscription_id: UUID,
    request: Request,
    session: SessionDep,
) -> RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    subscription = await get_subscription(session, user, subscription_id)
    if subscription is None:
        flash(request, "Subscription not found.", "error")
        return RedirectResponse("/subscriptions", status_code=303)

    await delete_subscription(session, subscription)
    await session.commit()
    flash(request, "Subscription deleted.", "success")
    return RedirectResponse("/subscriptions", status_code=303)
