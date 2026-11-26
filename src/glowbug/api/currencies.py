from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from glowbug.db.session import get_session
from glowbug.schemas.currency import CurrencyRead
from glowbug.services.currency.catalog import search_currencies

router = APIRouter(prefix="/api/currencies", tags=["currencies"])


@router.get("", response_model=list[CurrencyRead])
async def list_currencies(
    session: Annotated[AsyncSession, Depends(get_session)],
    q: Annotated[str, Query(max_length=64)] = "",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[CurrencyRead]:
    currencies = await search_currencies(session, q, limit)
    return [CurrencyRead.model_validate(currency) for currency in currencies]
