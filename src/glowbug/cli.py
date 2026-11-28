from __future__ import annotations

import argparse
import asyncio

from glowbug.db.session import async_session_factory
from glowbug.services.currency.catalog import search_currencies, seed_iso_currencies
from glowbug.services.fx.sync import get_last_sync, sync_frankfurter_rates
from glowbug.services.settings import get_setting


async def seed_currencies() -> None:
    async with async_session_factory() as session:
        added = await seed_iso_currencies(session)
    print(f"Inserted {added} ISO currencies.")


async def list_currencies(query: str) -> None:
    async with async_session_factory() as session:
        currencies = await search_currencies(session, query, limit=50)
    for currency in currencies:
        print(f"{currency.code}  {currency.name}  {currency.symbol}  {currency.decimal_places}")


async def sync_fx() -> None:
    async with async_session_factory() as session:
        result = await sync_frankfurter_rates(session)
    print(f"Frankfurter sync finished: inserted={result.inserted} skipped={result.skipped}")


async def fx_status() -> None:
    async with async_session_factory() as session:
        last_sync = await get_last_sync(session)
        status = await get_setting(session, "fx.last_status")
        error = await get_setting(session, "fx.last_error")

    print(f"Last sync: {last_sync.isoformat() if last_sync else 'never'}")
    print(f"Status: {status or 'unknown'}")
    if error:
        print(f"Error: {error}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="glowbug")
    commands = parser.add_subparsers(dest="command", required=True)

    currency = commands.add_parser("currency")
    currency_commands = currency.add_subparsers(dest="currency_command", required=True)

    currency_commands.add_parser("seed")

    search = currency_commands.add_parser("search")
    search.add_argument("query", nargs="?", default="")

    fx = commands.add_parser("fx")
    fx_commands = fx.add_subparsers(dest="fx_command", required=True)
    fx_commands.add_parser("sync")
    fx_commands.add_parser("status")

    args = parser.parse_args()

    if args.command == "currency" and args.currency_command == "seed":
        asyncio.run(seed_currencies())
    elif args.command == "currency" and args.currency_command == "search":
        asyncio.run(list_currencies(args.query))
    elif args.command == "fx" and args.fx_command == "sync":
        asyncio.run(sync_fx())
    elif args.command == "fx" and args.fx_command == "status":
        asyncio.run(fx_status())
