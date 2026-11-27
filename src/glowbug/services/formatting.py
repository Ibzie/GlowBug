from __future__ import annotations

from decimal import Decimal

from babel.numbers import UnknownCurrencyError, format_currency


def format_money(
    amount: Decimal | str | int,
    currency_code: str,
    locale: str = "en_US",
) -> str:
    value = amount if isinstance(amount, Decimal) else Decimal(str(amount))
    try:
        return format_currency(value, currency_code, locale=locale)
    except UnknownCurrencyError:
        return f"{value:,.2f} {currency_code}"
