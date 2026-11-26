from sqlalchemy.ext.asyncio import AsyncSession

from glowbug.services.currency.catalog import (
    build_currency,
    iso_currency_codes,
    search_currencies,
    seed_iso_currencies,
)


def test_catalog_contains_common_iso_codes() -> None:
    codes = set(iso_currency_codes())
    assert {"USD", "EUR", "GBP", "PKR", "NGN", "VND", "KWD", "JPY"} <= codes
    assert "XXX" not in codes
    assert "XTS" not in codes


def test_currency_precision() -> None:
    jpy = build_currency("JPY")
    kwd = build_currency("KWD")
    usd = build_currency("USD")

    assert jpy is not None
    assert kwd is not None
    assert usd is not None
    assert jpy.decimal_places == 0
    assert kwd.decimal_places == 3
    assert usd.decimal_places == 2


async def test_seed_and_search(session: AsyncSession) -> None:
    added = await seed_iso_currencies(session)
    assert added > 150
    assert await seed_iso_currencies(session) == 0

    results = await search_currencies(session, "pkr")
    assert results
    assert results[0].code == "PKR"
    assert results[0].symbol

    rupee_results = await search_currencies(session, "rupee", limit=100)
    assert rupee_results
    assert all(currency.is_active for currency in rupee_results)
    assert "MVP" not in {currency.code for currency in rupee_results}
