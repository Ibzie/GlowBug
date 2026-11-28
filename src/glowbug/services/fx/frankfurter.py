from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

import httpx

FRANKFURTER_URL = "https://api.frankfurter.dev/v2/rates"


class FrankfurterError(Exception):
    """Raised when Frankfurter cannot provide a usable rate response."""


@dataclass(frozen=True)
class RateQuote:
    base: str
    quote: str
    as_of_date: date
    rate: Decimal


async def fetch_rates(
    base: str = "USD",
    quotes: list[str] | None = None,
    *,
    client: httpx.AsyncClient | None = None,
) -> list[RateQuote]:
    """Fetch reference rates from Frankfurter v2.

    No fallback provider is used. If the call fails, the sync fails and the
    last successful sync timestamp remains visible in the UI.
    """
    params: dict[str, str] = {"base": base.upper()}
    if quotes:
        params["quotes"] = ",".join(quote.upper() for quote in quotes)

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=30)

    try:
        response = await client.get(FRANKFURTER_URL, params=params)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise FrankfurterError(f"Frankfurter request failed: {exc}") from exc
    finally:
        if owns_client:
            await client.aclose()

    if not isinstance(payload, list):
        raise FrankfurterError("Frankfurter returned an unexpected response shape.")

    result: list[RateQuote] = []
    for row in payload:
        try:
            rate = Decimal(str(row["rate"]))
            if rate <= 0:
                continue
            result.append(
                RateQuote(
                    base=str(row["base"]).upper(),
                    quote=str(row["quote"]).upper(),
                    as_of_date=date.fromisoformat(str(row["date"])),
                    rate=rate,
                )
            )
        except KeyError, TypeError, ValueError, InvalidOperation:
            continue

    return result
