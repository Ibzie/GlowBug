from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from glowbug.models.currency import Currency
from glowbug.models.exchange_rate import ExchangeRate
from glowbug.services.fx.frankfurter import FrankfurterError, RateQuote
from glowbug.services.fx.sync import get_last_sync, sync_frankfurter_rates
from glowbug.services.settings import get_setting

RATE_DATE = date(2026, 11, 26)


async def add_currency(session: AsyncSession, code: str, symbol: str) -> None:
    session.add(Currency(code=code, name=code, symbol=symbol, decimal_places=2))
    await session.flush()


async def test_sync_inserts_rates_and_records_last_sync(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await add_currency(session, "USD", "$")
    await add_currency(session, "PKR", "₨")
    await add_currency(session, "EUR", "€")

    async def fake_fetch(
        base: str = "USD",
        quotes: list[str] | None = None,
        *,
        client=None,  # type: ignore[no-untyped-def]
    ) -> list[RateQuote]:
        assert base == "USD"
        return [
            RateQuote(base="USD", quote="PKR", as_of_date=RATE_DATE, rate=Decimal("276.95")),
            RateQuote(base="USD", quote="EUR", as_of_date=RATE_DATE, rate=Decimal("0.91217")),
        ]

    monkeypatch.setattr("glowbug.services.fx.sync.fetch_rates", fake_fetch)

    result = await sync_frankfurter_rates(session)
    assert result.inserted == 2
    assert result.skipped == 0

    rates = list(await session.scalars(select(ExchangeRate)))
    assert {rate.quote_code for rate in rates} == {"PKR", "EUR"}
    assert all(rate.source == "frankfurter" for rate in rates)

    last_sync = await get_last_sync(session)
    assert last_sync is not None
    assert await get_setting(session, "fx.last_status") == "success"


async def test_sync_skips_existing_and_preserves_manual_rate(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await add_currency(session, "USD", "$")
    await add_currency(session, "PKR", "₨")
    await add_currency(session, "EUR", "€")
    session.add(
        ExchangeRate(
            base_code="USD",
            quote_code="PKR",
            as_of_date=RATE_DATE,
            rate=Decimal("999"),
            source="manual",
        )
    )
    await session.flush()

    async def fake_fetch(
        base: str = "USD",
        quotes: list[str] | None = None,
        *,
        client=None,  # type: ignore[no-untyped-def]
    ) -> list[RateQuote]:
        return [
            RateQuote(base="USD", quote="PKR", as_of_date=RATE_DATE, rate=Decimal("276.95")),
            RateQuote(base="USD", quote="EUR", as_of_date=RATE_DATE, rate=Decimal("0.91217")),
        ]

    monkeypatch.setattr("glowbug.services.fx.sync.fetch_rates", fake_fetch)

    result = await sync_frankfurter_rates(session)
    assert result.inserted == 1
    assert result.skipped == 1

    manual = await session.scalar(
        select(ExchangeRate).where(
            ExchangeRate.base_code == "USD",
            ExchangeRate.quote_code == "PKR",
            ExchangeRate.as_of_date == RATE_DATE,
        )
    )
    assert manual is not None
    assert manual.rate == Decimal("999")
    assert manual.source == "manual"


async def test_sync_failure_records_status_and_raises(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_fetch(
        base: str = "USD",
        quotes: list[str] | None = None,
        *,
        client=None,  # type: ignore[no-untyped-def]
    ) -> list[RateQuote]:
        raise FrankfurterError("network down")

    monkeypatch.setattr("glowbug.services.fx.sync.fetch_rates", fake_fetch)

    with pytest.raises(FrankfurterError):
        await sync_frankfurter_rates(session)

    assert await get_setting(session, "fx.last_status") == "failed"
    error = await get_setting(session, "fx.last_error")
    assert error is not None and "network down" in error
