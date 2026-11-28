from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from glowbug.db.session import get_session
from glowbug.security import get_session_user
from glowbug.services.accounts import get_user_currencies
from glowbug.services.categories import ensure_categories
from glowbug.services.taxes import (
    calculate_tax,
    create_profile,
    delete_payment,
    delete_profile,
    get_payment,
    get_profile,
    record_payment,
    update_profile,
)
from glowbug.web.routes import flash, parse_decimal, render
from glowbug.web.transactions import parse_uuid

router = APIRouter(tags=["taxes"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def tax_form_context(session: AsyncSession, user) -> dict:  # type: ignore[no-untyped-def]
    return {
        "user": user,
        "categories": await ensure_categories(session, user),
        "currencies": await get_user_currencies(session, user.id),
    }


def profile_values(form) -> dict:  # type: ignore[no-untyped-def]
    return {
        "name": str(form.get("name", "")),
        "rate": str(form.get("rate", "")),
        "currency_code": str(form.get("currency_code", "")).upper(),
        "tax_year": str(form.get("tax_year", "")),
        "category_ids": {str(value) for value in form.getlist("category_ids")},
        "is_active": str(form.get("is_active", "")) == "1",
    }


@router.get("/taxes", response_class=HTMLResponse, response_model=None)
async def taxes_index(request: Request, session: SessionDep) -> HTMLResponse | RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    year_string = request.query_params.get("year", str(date.today().year))
    try:
        year = int(year_string)
    except ValueError:
        year = date.today().year
    year = max(1900, min(2200, year))

    summary = await calculate_tax(session, user, year)
    return render(
        request,
        "taxes/index.html",
        {"user": user, "summary": summary, "year": year},
    )


@router.get("/taxes/profiles/new", response_class=HTMLResponse, response_model=None)
async def tax_profile_create_page(
    request: Request,
    session: SessionDep,
) -> HTMLResponse | RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    context = await tax_form_context(session, user)
    year = int(request.query_params.get("year", date.today().year))
    context["profile"] = None
    context["values"] = {
        "name": "",
        "rate": "",
        "currency_code": context["currencies"][0].code if context["currencies"] else "",
        "tax_year": str(year),
        "category_ids": set(),
        "is_active": True,
    }
    return render(request, "taxes/profile_form.html", context)


@router.post("/taxes/profiles", response_model=None)
async def tax_profile_create_submit(
    request: Request,
    session: SessionDep,
) -> HTMLResponse | RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    form = await request.form()
    values = profile_values(form)
    rate = parse_decimal(values["rate"])
    try:
        tax_year = int(values["tax_year"])
    except ValueError:
        tax_year = None
    category_ids = [
        category_id for value in form.getlist("category_ids") if (category_id := parse_uuid(str(value))) is not None
    ]

    if rate is None or tax_year is None:
        context = await tax_form_context(session, user)
        context.update({"profile": None, "values": values, "error": "Enter a valid rate and tax year."})
        return render(request, "taxes/profile_form.html", context, status_code=400)

    try:
        profile = await create_profile(
            session,
            user,
            name=values["name"],
            rate=rate,
            currency_code=values["currency_code"],
            tax_year=tax_year,
            category_ids=category_ids,
            is_active=values["is_active"],
        )
        await session.commit()
    except ValueError as exc:
        context = await tax_form_context(session, user)
        context.update({"profile": None, "values": values, "error": str(exc)})
        return render(request, "taxes/profile_form.html", context, status_code=400)

    flash(request, f"Tax profile “{profile.name}” created.", "success")
    return RedirectResponse(f"/taxes?year={profile.tax_year}", status_code=303)


@router.get("/taxes/profiles/{profile_id}/edit", response_class=HTMLResponse, response_model=None)
async def tax_profile_edit_page(
    profile_id: UUID,
    request: Request,
    session: SessionDep,
) -> HTMLResponse | RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    profile = await get_profile(session, user, profile_id)
    if profile is None:
        flash(request, "Tax profile not found.", "error")
        return RedirectResponse("/taxes", status_code=303)

    context = await tax_form_context(session, user)
    context["profile"] = profile
    context["values"] = {
        "name": profile.name,
        "rate": str(profile.rate),
        "currency_code": profile.currency_code,
        "tax_year": str(profile.tax_year),
        "category_ids": {str(category.id) for category in profile.categories},
        "is_active": profile.is_active,
    }
    return render(request, "taxes/profile_form.html", context)


@router.post("/taxes/profiles/{profile_id}", response_model=None)
async def tax_profile_edit_submit(
    profile_id: UUID,
    request: Request,
    session: SessionDep,
) -> HTMLResponse | RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    profile = await get_profile(session, user, profile_id)
    if profile is None:
        flash(request, "Tax profile not found.", "error")
        return RedirectResponse("/taxes", status_code=303)

    form = await request.form()
    values = profile_values(form)
    rate = parse_decimal(values["rate"])
    try:
        tax_year = int(values["tax_year"])
    except ValueError:
        tax_year = None
    category_ids = [
        category_id for value in form.getlist("category_ids") if (category_id := parse_uuid(str(value))) is not None
    ]
    if rate is None or tax_year is None:
        context = await tax_form_context(session, user)
        context.update({"profile": profile, "values": values, "error": "Enter a valid rate and tax year."})
        return render(request, "taxes/profile_form.html", context, status_code=400)

    try:
        await update_profile(
            session,
            profile,
            name=values["name"],
            rate=rate,
            currency_code=values["currency_code"],
            tax_year=tax_year,
            category_ids=category_ids,
            is_active=values["is_active"],
        )
        await session.commit()
    except ValueError as exc:
        context = await tax_form_context(session, user)
        context.update({"profile": profile, "values": values, "error": str(exc)})
        return render(request, "taxes/profile_form.html", context, status_code=400)

    flash(request, "Tax profile updated.", "success")
    return RedirectResponse(f"/taxes?year={tax_year}", status_code=303)


@router.post("/taxes/profiles/{profile_id}/delete", response_model=None)
async def tax_profile_delete(
    profile_id: UUID,
    request: Request,
    session: SessionDep,
) -> RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    profile = await get_profile(session, user, profile_id)
    if profile is None:
        flash(request, "Tax profile not found.", "error")
        return RedirectResponse("/taxes", status_code=303)

    year = profile.tax_year
    await delete_profile(session, profile)
    await session.commit()
    flash(request, "Tax profile deleted.", "success")
    return RedirectResponse(f"/taxes?year={year}", status_code=303)


@router.get("/taxes/profiles/{profile_id}/payments/new", response_class=HTMLResponse, response_model=None)
async def tax_payment_create_page(
    profile_id: UUID,
    request: Request,
    session: SessionDep,
) -> HTMLResponse | RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    profile = await get_profile(session, user, profile_id)
    if profile is None:
        flash(request, "Tax profile not found.", "error")
        return RedirectResponse("/taxes", status_code=303)

    return render(
        request,
        "taxes/payment_form.html",
        {
            "user": user,
            "profile": profile,
            "values": {"paid_on": date.today().isoformat(), "amount": "", "note": ""},
        },
    )


@router.post("/taxes/profiles/{profile_id}/payments", response_model=None)
async def tax_payment_create_submit(
    profile_id: UUID,
    request: Request,
    session: SessionDep,
) -> HTMLResponse | RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    profile = await get_profile(session, user, profile_id)
    if profile is None:
        flash(request, "Tax profile not found.", "error")
        return RedirectResponse("/taxes", status_code=303)

    form = await request.form()
    amount = parse_decimal(str(form.get("amount", "")))
    paid_on_string = str(form.get("paid_on", ""))
    note = str(form.get("note", "")).strip() or None
    try:
        paid_on = date.fromisoformat(paid_on_string)
    except ValueError:
        paid_on = None

    if amount is None or paid_on is None:
        return render(
            request,
            "taxes/payment_form.html",
            {
                "user": user,
                "profile": profile,
                "values": {
                    "amount": str(form.get("amount", "")),
                    "paid_on": paid_on_string,
                    "note": note or "",
                },
                "error": "Enter a valid amount and payment date.",
            },
            status_code=400,
        )

    try:
        await record_payment(
            session,
            user,
            profile,
            amount=amount,
            paid_on=paid_on,
            note=note,
        )
        await session.commit()
    except ValueError as exc:
        return render(
            request,
            "taxes/payment_form.html",
            {
                "user": user,
                "profile": profile,
                "values": {
                    "amount": str(form.get("amount", "")),
                    "paid_on": paid_on_string,
                    "note": note or "",
                },
                "error": str(exc),
            },
            status_code=400,
        )

    flash(request, "Tax payment recorded.", "success")
    return RedirectResponse(f"/taxes?year={profile.tax_year}", status_code=303)


@router.post("/taxes/payments/{payment_id}/delete", response_model=None)
async def tax_payment_delete(
    payment_id: UUID,
    request: Request,
    session: SessionDep,
) -> RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    payment = await get_payment(session, user, payment_id)
    if payment is None:
        flash(request, "Tax payment not found.", "error")
        return RedirectResponse("/taxes", status_code=303)

    profile = await get_profile(session, user, payment.tax_profile_id)
    year = profile.tax_year if profile is not None else date.today().year
    await delete_payment(session, payment)
    await session.commit()
    flash(request, "Tax payment deleted.", "success")
    return RedirectResponse(f"/taxes?year={year}", status_code=303)
