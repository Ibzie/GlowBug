from __future__ import annotations

import re

from babel.numbers import (
    UnknownCurrencyError,
    get_currency_name,
    get_currency_precision,
    get_currency_symbol,
    list_currencies,
)
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from glowbug.models.currency import Currency

# Babel's symbol data is incomplete or generic for some currencies. These
# overrides keep the catalog readable while remaining editable by the user.
SYMBOL_OVERRIDES: dict[str, str] = {
    "AED": "د.إ",
    "ARS": "$",
    "AUD": "A$",
    "BDT": "৳",
    "BRL": "R$",
    "CAD": "C$",
    "CHF": "CHF",
    "CLP": "$",
    "CNY": "¥",
    "COP": "$",
    "CZK": "Kč",
    "DKK": "kr",
    "EUR": "€",
    "GBP": "£",
    "HKD": "HK$",
    "HUF": "Ft",
    "IDR": "Rp",
    "INR": "₹",
    "JPY": "¥",
    "KRW": "₩",
    "MXN": "MX$",
    "MYR": "RM",
    "NGN": "₦",
    "NOK": "kr",
    "NZD": "NZ$",
    "PEN": "S/",
    "PHP": "₱",
    "PKR": "₨",
    "PLN": "zł",
    "RON": "lei",
    "RUB": "₽",
    "SAR": "﷼",
    "SEK": "kr",
    "SGD": "S$",
    "THB": "฿",
    "TRY": "₺",
    "TWD": "NT$",
    "USD": "$",
    "UYU": "$",
    "VND": "₫",
    "ZAR": "R",
}

EXCLUDED_CODES = {
    "XXX",
    "XTS",
    # Metals and special drawing rights, not spendable currencies.
    "XAU",
    "XAG",
    "XPT",
    "XPD",
    "XDR",
    "XBA",
    "XBB",
    "XBC",
    "XBD",
    "XSU",
    "XUA",
    "XFO",
    "XFU",
}

HISTORICAL_NAME_PATTERN = re.compile(
    r"\((?:1[6-9]\d{2}|20\d{2})\s*[-\u2013]\s*(?:1[6-9]\d{2}|20\d{2})\)|\((?:old|historic|obsolete)\)",
    re.IGNORECASE,
)


def iso_currency_codes() -> list[str]:
    """Return the ISO 4217 alphabetic codes known to Babel."""
    return sorted(
        code for code in list_currencies("en") if len(code) == 3 and code.isalpha() and code not in EXCLUDED_CODES
    )


def build_currency(code: str, locale: str = "en") -> Currency | None:
    if code in EXCLUDED_CODES:
        return None
    try:
        name = get_currency_name(code, locale=locale)
        precision = get_currency_precision(code)
    except UnknownCurrencyError:
        return None

    symbol = SYMBOL_OVERRIDES.get(code) or get_currency_symbol(code, locale=locale) or code
    return Currency(
        code=code,
        name=name,
        symbol=symbol,
        decimal_places=precision,
        is_iso=True,
        is_active=HISTORICAL_NAME_PATTERN.search(name) is None,
    )


async def seed_iso_currencies(session: AsyncSession) -> int:
    """Insert or refresh every ISO currency. Returns the number inserted."""
    existing = {currency.code: currency for currency in await session.scalars(select(Currency))}
    added = 0
    updated = 0

    for code in iso_currency_codes():
        currency = build_currency(code)
        if currency is None:
            continue

        current = existing.get(code)
        if current is None:
            session.add(currency)
            added += 1
            continue

        current.name = currency.name
        current.symbol = currency.symbol
        current.decimal_places = currency.decimal_places
        current.is_active = currency.is_active
        updated += 1

    if added or updated:
        await session.commit()

    return added


async def search_currencies(
    session: AsyncSession,
    query: str = "",
    limit: int = 20,
) -> list[Currency]:
    statement = select(Currency).where(Currency.is_active.is_(True)).order_by(Currency.code).limit(limit)
    cleaned = query.strip()
    if cleaned:
        pattern = f"%{cleaned}%"
        statement = statement.where(
            or_(
                Currency.code.ilike(pattern),
                Currency.name.ilike(pattern),
                Currency.symbol.ilike(pattern),
            )
        )

    return list(await session.scalars(statement))


async def get_currency(session: AsyncSession, code: str) -> Currency | None:
    return await session.get(Currency, code.upper())
