from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from glowbug.enums import NET_WORTH_TYPES, AccountType
from glowbug.models.account import Account
from glowbug.models.currency import Currency
from glowbug.models.transaction import Transaction
from glowbug.models.user import User, UserCurrency


async def list_accounts(
    session: AsyncSession,
    user_id: UUID,
    *,
    include_archived: bool = False,
) -> list[Account]:
    statement = select(Account).where(Account.user_id == user_id).order_by(Account.created_at, Account.name)
    if not include_archived:
        statement = statement.where(Account.archived.is_(False))

    return list(await session.scalars(statement))


async def account_balances(
    session: AsyncSession,
    accounts: list[Account],
) -> dict[UUID, Decimal]:
    if not accounts:
        return {}

    sums: defaultdict[UUID, Decimal] = defaultdict(lambda: Decimal("0"))
    statement = select(Transaction.account_id, Transaction.amount).where(
        Transaction.account_id.in_([account.id for account in accounts])
    )
    for account_id, amount in (await session.execute(statement)).all():
        sums[account_id] += amount

    return {
        account.id: (account.opening_balance or Decimal("0")) + sums.get(account.id, Decimal("0"))
        for account in accounts
    }


async def create_account(
    session: AsyncSession,
    user: User,
    *,
    account_type: AccountType,
    name: str,
    currency_code: str,
    opening_balance: Decimal = Decimal("0"),
    opening_balance_date: date | None = None,
) -> Account:
    account = Account(
        user_id=user.id,
        type=account_type.value,
        name=name.strip(),
        currency_code=currency_code,
        opening_balance=opening_balance,
        opening_balance_date=opening_balance_date or date.today(),
        include_in_net_worth=account_type in NET_WORTH_TYPES,
    )
    session.add(account)
    await session.flush()

    return account


async def enable_currency(session: AsyncSession, user_id: UUID, currency_code: str) -> None:
    existing = await session.get(UserCurrency, (user_id, currency_code))
    if existing is None:
        session.add(
            UserCurrency(
                user_id=user_id,
                currency_code=currency_code,
                enabled=True,
            )
        )
        await session.flush()


async def get_user_currencies(session: AsyncSession, user_id: UUID) -> list[Currency]:
    statement = (
        select(Currency)
        .join(UserCurrency, UserCurrency.currency_code == Currency.code)
        .where(
            UserCurrency.user_id == user_id,
            UserCurrency.enabled.is_(True),
            Currency.is_active.is_(True),
        )
        .order_by(UserCurrency.is_primary.desc(), UserCurrency.is_secondary.desc(), Currency.code)
    )
    return list(await session.scalars(statement))


async def get_primary_currency(session: AsyncSession, user_id: UUID) -> Currency | None:
    statement = (
        select(Currency)
        .join(UserCurrency, UserCurrency.currency_code == Currency.code)
        .where(
            UserCurrency.user_id == user_id,
            UserCurrency.is_primary.is_(True),
        )
    )
    return await session.scalar(statement)


async def get_secondary_currency(session: AsyncSession, user_id: UUID) -> Currency | None:
    statement = (
        select(Currency)
        .join(UserCurrency, UserCurrency.currency_code == Currency.code)
        .where(
            UserCurrency.user_id == user_id,
            UserCurrency.is_secondary.is_(True),
        )
    )
    return await session.scalar(statement)


async def set_user_currencies(
    session: AsyncSession,
    user: User,
    *,
    enabled_codes: list[str],
    primary_code: str,
    secondary_code: str | None = None,
) -> None:
    if not primary_code:
        raise ValueError("Choose a primary currency.")

    enabled = {code.upper() for code in enabled_codes if code}
    enabled.add(primary_code.upper())
    if secondary_code:
        enabled.add(secondary_code.upper())

    currencies = list(
        await session.scalars(
            select(Currency).where(
                Currency.code.in_(enabled),
                Currency.is_active.is_(True),
            )
        )
    )
    found = {currency.code for currency in currencies}
    missing = enabled - found
    if missing:
        raise ValueError(f"Unknown or inactive currency: {', '.join(sorted(missing))}")

    primary_code = primary_code.upper()
    secondary_code = secondary_code.upper() if secondary_code else None
    if secondary_code == primary_code:
        raise ValueError("Primary and secondary currency must be different.")

    await session.execute(delete(UserCurrency).where(UserCurrency.user_id == user.id))

    for code in sorted(enabled):
        session.add(
            UserCurrency(
                user_id=user.id,
                currency_code=code,
                enabled=True,
                is_primary=code == primary_code,
                is_secondary=code == secondary_code,
            )
        )

    await session.flush()
