from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from glowbug.models.currency import Currency
from glowbug.models.exchange_rate import ExchangeRate
from glowbug.services.fx.exceptions import RateUnavailableError
from glowbug.services.fx.service import ExchangeRateService

RATE_DATE = date(2026, 1, 10)


async def add_currency(session: AsyncSession, code: str, name: str, symbol: str) -> None:
    session.add(Currency(code=code, name=name, symbol=symbol, decimal_places=2))
    await session.flush()


async def add_rate(
    session: AsyncSession,
    base: str,
    quote: str,
    rate: str,
    as_of: date = RATE_DATE,
) -> None:
    session.add(
        ExchangeRate(
            base_code=base,
            quote_code=quote,
            rate=Decimal(rate),
            as_of_date=as_of,
            source="test",
        )
    )
    await session.flush()


async def test_exact_rate(session: AsyncSession) -> None:
    await add_currency(session, "EUR", "Euro", "€")
    await add_currency(session, "USD", "US Dollar", "$")
    await add_rate(session, "EUR", "USD", "1.1")

    service = ExchangeRateService(session)
    rate, rate_date = await service.get_rate("EUR", "USD", RATE_DATE)

    assert rate == Decimal("1.1")
    assert rate_date == RATE_DATE


async def test_inverse_rate(session: AsyncSession) -> None:
    await add_currency(session, "EUR", "Euro", "€")
    await add_currency(session, "USD", "US Dollar", "$")
    await add_rate(session, "EUR", "USD", "1.1")

    service = ExchangeRateService(session)
    rate, rate_date = await service.get_rate("USD", "EUR", RATE_DATE)
    product = (rate * Decimal("1.1")).quantize(Decimal("0.000000000000000001"))

    assert product == Decimal("1.000000000000000000")
    assert rate_date == RATE_DATE


async def test_nearest_previous_rate(session: AsyncSession) -> None:
    await add_currency(session, "EUR", "Euro", "€")
    await add_currency(session, "USD", "US Dollar", "$")
    await add_rate(session, "EUR", "USD", "1.1")

    service = ExchangeRateService(session)
    rate, rate_date = await service.get_rate("EUR", "USD", date(2026, 1, 12))

    assert rate == Decimal("1.1")
    assert rate_date == RATE_DATE


async def test_cross_rate(session: AsyncSession) -> None:
    await add_currency(session, "EUR", "Euro", "€")
    await add_currency(session, "USD", "US Dollar", "$")
    await add_currency(session, "PKR", "Pakistani Rupee", "₨")
    await add_rate(session, "EUR", "USD", "1.1")
    await add_rate(session, "EUR", "PKR", "300")

    service = ExchangeRateService(session)
    rate, _ = await service.get_rate("PKR", "USD", RATE_DATE)

    assert rate == pytest.approx(Decimal("1.1") / Decimal("300"), rel=Decimal("1e-15"))


async def test_missing_rate_does_not_default_to_one(session: AsyncSession) -> None:
    await add_currency(session, "JPY", "Japanese Yen", "¥")
    await add_currency(session, "USD", "US Dollar", "$")

    service = ExchangeRateService(session)
    with pytest.raises(RateUnavailableError):
        await service.get_rate("JPY", "USD", RATE_DATE)


async def test_convert_uses_decimal_math(session: AsyncSession) -> None:
    await add_currency(session, "EUR", "Euro", "€")
    await add_currency(session, "USD", "US Dollar", "$")
    await add_rate(session, "EUR", "USD", "1.1")

    service = ExchangeRateService(session)
    converted = await service.convert(Decimal("100"), "EUR", "USD", RATE_DATE)

    assert converted == Decimal("110.000000000000000000")
