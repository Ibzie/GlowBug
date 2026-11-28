from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from glowbug.db.session import get_session
from glowbug.security import get_session_user
from glowbug.services.categories import (
    category_counts,
    create_category,
    delete_category,
    ensure_categories,
    get_category,
    update_category,
)
from glowbug.web.routes import flash, render

router = APIRouter(tags=["categories"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/categories", response_class=HTMLResponse, response_model=None)
async def categories_index(request: Request, session: SessionDep) -> HTMLResponse | RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    categories = await ensure_categories(session, user)
    counts = await category_counts(session, user.id)
    return render(
        request,
        "categories/index.html",
        {"user": user, "categories": categories, "counts": counts},
    )


@router.get("/categories/new", response_class=HTMLResponse, response_model=None)
async def category_create_page(request: Request, session: SessionDep) -> HTMLResponse | RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    return render(
        request,
        "categories/form.html",
        {"user": user, "category": None, "values": {"color": "#2f6fed"}},
    )


@router.post("/categories", response_model=None)
async def category_create_submit(request: Request, session: SessionDep) -> HTMLResponse | RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    form = await request.form()
    name = str(form.get("name", ""))
    color = str(form.get("color", "#2f6fed"))
    try:
        await create_category(session, user, name, color)
        await session.commit()
    except ValueError as exc:
        return render(
            request,
            "categories/form.html",
            {
                "user": user,
                "category": None,
                "values": {"name": name, "color": color},
                "error": str(exc),
            },
            status_code=400,
        )

    flash(request, f"Category “{name.strip()}” created.", "success")
    return RedirectResponse("/categories", status_code=303)


@router.get("/categories/{category_id}/edit", response_class=HTMLResponse, response_model=None)
async def category_edit_page(
    category_id: UUID,
    request: Request,
    session: SessionDep,
) -> HTMLResponse | RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    category = await get_category(session, user.id, category_id)
    if category is None:
        flash(request, "Category not found.", "error")
        return RedirectResponse("/categories", status_code=303)

    return render(
        request,
        "categories/form.html",
        {
            "user": user,
            "category": category,
            "values": {"name": category.name, "color": category.color},
        },
    )


@router.post("/categories/{category_id}", response_model=None)
async def category_edit_submit(
    category_id: UUID,
    request: Request,
    session: SessionDep,
) -> HTMLResponse | RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    category = await get_category(session, user.id, category_id)
    if category is None:
        flash(request, "Category not found.", "error")
        return RedirectResponse("/categories", status_code=303)

    form = await request.form()
    name = str(form.get("name", ""))
    color = str(form.get("color", category.color))
    try:
        await update_category(session, category, name, color)
        await session.commit()
    except ValueError as exc:
        return render(
            request,
            "categories/form.html",
            {
                "user": user,
                "category": category,
                "values": {"name": name, "color": color},
                "error": str(exc),
            },
            status_code=400,
        )

    flash(request, "Category updated.", "success")
    return RedirectResponse("/categories", status_code=303)


@router.post("/categories/{category_id}/delete", response_model=None)
async def category_delete(
    category_id: UUID,
    request: Request,
    session: SessionDep,
) -> RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    category = await get_category(session, user.id, category_id)
    if category is None:
        flash(request, "Category not found.", "error")
        return RedirectResponse("/categories", status_code=303)

    await delete_category(session, category)
    await session.commit()
    flash(request, "Category deleted. Existing transactions are now uncategorized.", "success")
    return RedirectResponse("/categories", status_code=303)
