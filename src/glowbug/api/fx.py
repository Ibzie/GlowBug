from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from glowbug.db.session import get_session
from glowbug.services.fx.exceptions import RateUnavailableError
from glowbug.services.fx.service import ExchangeRateService

router = APIRouter(prefix="/api/fx", tags=["fx"])


@router.get("/rate")
async def get_rate(
    session: Annotated[AsyncSession, Depends(get_session)],
    base: Annotated[str, Query(min_length=3, max_length=5)],
    quote: Annotated[str, Query(min_length=3, max_length=5)],
    as_of: date | None = None,
) -> dict[str, str]:
    service = ExchangeRateService(session)
    try:
        rate, rate_date = await service.get_rate(base, quote, as_of or date.today())
    except RateUnavailableError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {
        "base": base.upper(),
        "quote": quote.upper(),
        "rate": str(rate),
        "rate_date": rate_date.isoformat(),
    }


@router.get("/convert")
async def convert(
    session: Annotated[AsyncSession, Depends(get_session)],
    amount: Annotated[Decimal, Query()],
    base: Annotated[str, Query(min_length=3, max_length=5)],
    quote: Annotated[str, Query(min_length=3, max_length=5)],
    as_of: date | None = None,
) -> dict[str, str]:
    service = ExchangeRateService(session)
    try:
        converted = await service.convert(amount, base, quote, as_of or date.today())
    except RateUnavailableError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {
        "amount": str(amount),
        "base": base.upper(),
        "converted": str(converted),
        "quote": quote.upper(),
    }
