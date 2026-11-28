from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class WidgetCreate(BaseModel):
    type: str
    title: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class WidgetUpdate(BaseModel):
    title: str | None = None
    config: dict[str, Any] | None = None


class LayoutItem(BaseModel):
    id: UUID
    x: int
    y: int
    w: int
    h: int


class LayoutUpdate(BaseModel):
    items: list[LayoutItem]
