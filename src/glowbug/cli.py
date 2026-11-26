from __future__ import annotations

import argparse
import asyncio

from glowbug.db.session import async_session_factory
from glowbug.services.currency.catalog import search_currencies, seed_iso_currencies


async def seed_currencies() -> None:
    async with async_session_factory() as session:
        added = await seed_iso_currencies(session)
    print(f"Inserted {added} ISO currencies.")


async def list_currencies(query: str) -> None:
    async with async_session_factory() as session:
        currencies = await search_currencies(session, query, limit=50)
    for currency in currencies:
        print(f"{currency.code}  {currency.name}  {currency.symbol}  {currency.decimal_places}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="glowbug")
    commands = parser.add_subparsers(dest="command", required=True)

    currency = commands.add_parser("currency")
    currency_commands = currency.add_subparsers(dest="currency_command", required=True)

    currency_commands.add_parser("seed")

    search = currency_commands.add_parser("search")
    search.add_argument("query", nargs="?", default="")

    args = parser.parse_args()

    if args.command == "currency" and args.currency_command == "seed":
        asyncio.run(seed_currencies())
    elif args.command == "currency" and args.currency_command == "search":
        asyncio.run(list_currencies(args.query))
