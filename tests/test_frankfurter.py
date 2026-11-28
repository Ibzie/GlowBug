from datetime import date
from decimal import Decimal

import httpx
import pytest

from glowbug.services.fx.frankfurter import FrankfurterError, fetch_rates


def make_client(handler) -> httpx.AsyncClient:  # type: ignore[no-untyped-def]
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport, base_url="https://api.frankfurter.dev")


async def test_fetch_rates_parses_rows() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["base"] == "USD"
        assert request.url.params["quotes"] == "PKR,EUR"
        return httpx.Response(
            200,
            json=[
                {"date": "2026-11-26", "base": "USD", "quote": "PKR", "rate": 276.95},
                {"date": "2026-11-27", "base": "USD", "quote": "EUR", "rate": 0.91217},
                {"date": "2026-11-27", "base": "USD", "quote": "BTC", "rate": 0.00001},
            ],
        )

    async with make_client(handler) as client:
        quotes = await fetch_rates(base="USD", quotes=["PKR", "EUR"], client=client)

    assert len(quotes) == 3
    assert quotes[0].quote == "PKR"
    assert quotes[0].rate == Decimal("276.95")
    assert quotes[0].as_of_date == date(2026, 11, 26)


async def test_fetch_rates_raises_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    async with make_client(handler) as client:
        with pytest.raises(FrankfurterError):
            await fetch_rates(client=client)
