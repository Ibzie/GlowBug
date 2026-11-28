from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from glowbug.db.session import get_session
from glowbug.models.user import User
from glowbug.schemas.dashboard import LayoutUpdate, WidgetCreate, WidgetUpdate
from glowbug.security import require_api_user
from glowbug.services.dashboards import (
    WIDGET_CATALOG,
    build_widget_data,
    create_widget,
    default_range,
    delete_widget,
    get_dashboard,
    get_widget,
    save_layout,
    serialize_dashboard,
    serialize_widget,
    update_widget,
)

router = APIRouter(prefix="/api", tags=["dashboard"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
UserDep = Annotated[User, Depends(require_api_user)]


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


@router.get("/widget-catalog")
async def widget_catalog(user: UserDep) -> list[dict]:
    return WIDGET_CATALOG


@router.get("/dashboards/{dashboard_id}")
async def dashboard_detail(dashboard_id: UUID, user: UserDep, session: SessionDep) -> dict:
    dashboard = await get_dashboard(session, user, dashboard_id)
    if dashboard is None:
        raise HTTPException(status_code=404, detail="Dashboard not found.")
    return serialize_dashboard(dashboard)


@router.post("/dashboards/{dashboard_id}/widgets", status_code=201)
async def add_widget(
    dashboard_id: UUID,
    payload: WidgetCreate,
    user: UserDep,
    session: SessionDep,
) -> dict:
    dashboard = await get_dashboard(session, user, dashboard_id)
    if dashboard is None:
        raise HTTPException(status_code=404, detail="Dashboard not found.")

    try:
        widget = await create_widget(
            session,
            dashboard,
            widget_type=payload.type,
            title=payload.title,
            config=payload.config,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return serialize_widget(widget)


@router.patch("/widgets/{widget_id}")
async def edit_widget(
    widget_id: UUID,
    payload: WidgetUpdate,
    user: UserDep,
    session: SessionDep,
) -> dict:
    widget = await get_widget(session, user, widget_id)
    if widget is None:
        raise HTTPException(status_code=404, detail="Widget not found.")

    dashboard = await get_dashboard(session, user, widget.dashboard_id)
    if dashboard is None:
        raise HTTPException(status_code=404, detail="Dashboard not found.")

    updated = await update_widget(
        session,
        dashboard,
        widget_id,
        title=payload.title,
        config=payload.config,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Widget not found.")
    return serialize_widget(updated)


@router.delete("/widgets/{widget_id}", status_code=204)
async def remove_widget(widget_id: UUID, user: UserDep, session: SessionDep) -> Response:
    widget = await get_widget(session, user, widget_id)
    if widget is None:
        raise HTTPException(status_code=404, detail="Widget not found.")

    dashboard = await get_dashboard(session, user, widget.dashboard_id)
    if dashboard is None:
        raise HTTPException(status_code=404, detail="Dashboard not found.")

    deleted = await delete_widget(session, dashboard, widget_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Widget not found.")
    return Response(status_code=204)


@router.patch("/dashboards/{dashboard_id}/layout")
async def update_layout(
    dashboard_id: UUID,
    payload: LayoutUpdate,
    user: UserDep,
    session: SessionDep,
) -> dict:
    dashboard = await get_dashboard(session, user, dashboard_id)
    if dashboard is None:
        raise HTTPException(status_code=404, detail="Dashboard not found.")

    await save_layout(
        session,
        dashboard,
        [item.model_dump() for item in payload.items],
    )
    return {"ok": True}


@router.get("/widgets/{widget_id}/data")
async def widget_data(
    widget_id: UUID,
    request: Request,
    user: UserDep,
    session: SessionDep,
    start: Annotated[str | None, Query()] = None,
    end: Annotated[str | None, Query()] = None,
) -> dict:
    widget = await get_widget(session, user, widget_id)
    if widget is None:
        raise HTTPException(status_code=404, detail="Widget not found.")

    default_start, default_end = default_range(user, request.query_params.get("range"))
    start_date = parse_date(start) or default_start
    end_date = parse_date(end) or default_end
    if start_date > end_date:
        raise HTTPException(status_code=400, detail="Start date must be before end date.")

    return await build_widget_data(session, user, widget, start_date, end_date)
