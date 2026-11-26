from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, localcontext

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from glowbug.models.exchange_rate import ExchangeRate
from glowbug.services.fx.exceptions import RateUnavailableError

RATE_QUANTUM = Decimal("0.000000000000000001")


class ExchangeRateService:
    """Resolve historical exchange rates without silently inventing a rate."""

    def __init__(
        self,
        session: AsyncSession,
        max_stale_days: int = 7,
        pivots: tuple[str, ...] = ("EUR", "USD"),
    ) -> None:
        self.session = session
        self.max_stale_days = max_stale_days
        self.pivots = pivots

    async def get_rate(self, base: str, quote: str, as_of: date) -> tuple[Decimal, date]:
        base = base.upper()
        quote = quote.upper()

        if base == quote:
            return Decimal("1"), as_of

        direct = await self._direct(base, quote, as_of)
        if direct is not None:
            return direct

        for pivot in self.pivots:
            if pivot in (base, quote):
                continue
            left = await self._direct(base, pivot, as_of)
            right = await self._direct(pivot, quote, as_of)
            if left is not None and right is not None:
                return left[0] * right[0], min(left[1], right[1])

        raise RateUnavailableError(f"No exchange rate for {base}/{quote} on {as_of.isoformat()}")

    async def convert(
        self,
        amount: Decimal | str | int,
        base: str,
        quote: str,
        as_of: date,
    ) -> Decimal:
        value = amount if isinstance(amount, Decimal) else Decimal(str(amount))
        rate, _ = await self.get_rate(base, quote, as_of)
        return (value * rate).quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)

    async def _direct(self, base: str, quote: str, as_of: date) -> tuple[Decimal, date] | None:
        exact = await self._find_exact(base, quote, as_of)
        if exact is not None:
            return exact, as_of

        exact_inverse = await self._find_exact(quote, base, as_of)
        if exact_inverse is not None:
            return self._inverse(exact_inverse), as_of

        previous = await self._find_previous(base, quote, as_of)
        if previous is not None:
            rate, rate_date = previous
            return rate, rate_date

        previous_inverse = await self._find_previous(quote, base, as_of)
        if previous_inverse is not None:
            rate, rate_date = previous_inverse
            return self._inverse(rate), rate_date

        return None

    async def _find_exact(self, base: str, quote: str, as_of: date) -> Decimal | None:
        statement = select(ExchangeRate.rate).where(
            ExchangeRate.base_code == base,
            ExchangeRate.quote_code == quote,
            ExchangeRate.as_of_date == as_of,
        )
        return await self.session.scalar(statement)

    async def _find_previous(
        self,
        base: str,
        quote: str,
        as_of: date,
    ) -> tuple[Decimal, date] | None:
        if self.max_stale_days <= 0:
            return None

        earliest = as_of - timedelta(days=self.max_stale_days)
        statement = (
            select(ExchangeRate.rate, ExchangeRate.as_of_date)
            .where(
                ExchangeRate.base_code == base,
                ExchangeRate.quote_code == quote,
                ExchangeRate.as_of_date <= as_of,
                ExchangeRate.as_of_date >= earliest,
            )
            .order_by(ExchangeRate.as_of_date.desc())
            .limit(1)
        )
        row = (await self.session.execute(statement)).first()
        if row is None:
            return None

        return row[0], row[1]

    @staticmethod
    def _inverse(rate: Decimal) -> Decimal:
        if rate == 0:
            raise RateUnavailableError("Cannot invert a zero exchange rate.")
        with localcontext() as context:
            context.prec = 38
            return Decimal("1") / rate
