from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from glowbug.enums import AccountType, JournalType
from glowbug.models.currency import Currency
from glowbug.models.user import User
from glowbug.services.accounts import account_balances, create_account
from glowbug.services.ledger import LedgerError, Split, post_journal


async def make_user(session: AsyncSession) -> User:
    user = User(
        email="ledger@example.com",
        password_hash="not-used",
        display_name="Ledger",
        timezone="UTC",
        locale="en_US",
    )
    session.add(user)
    await session.flush()
    return user


async def add_currency(session: AsyncSession, code: str, symbol: str) -> None:
    session.add(Currency(code=code, name=code, symbol=symbol, decimal_places=2))
    await session.flush()


async def test_balanced_journal_updates_balances(session: AsyncSession) -> None:
    user = await make_user(session)
    await add_currency(session, "USD", "$")
    bank = await create_account(
        session,
        user,
        account_type=AccountType.ASSET,
        name="Bank",
        currency_code="USD",
        opening_balance=Decimal("1000"),
    )
    food = await create_account(
        session,
        user,
        account_type=AccountType.EXPENSE,
        name="Food",
        currency_code="USD",
    )

    journal = await post_journal(
        session,
        user,
        journal_type=JournalType.WITHDRAWAL,
        journal_date=date(2026, 1, 1),
        description="Groceries",
        currency_code="USD",
        splits=[
            Split(account_id=bank.id, amount=Decimal("-100")),
            Split(account_id=food.id, amount=Decimal("100")),
        ],
    )
    await session.commit()

    balances = await account_balances(session, [bank, food])
    assert journal.type == JournalType.WITHDRAWAL.value
    assert balances[bank.id] == Decimal("900")
    assert balances[food.id] == Decimal("100")


async def test_unbalanced_journal_is_rejected(session: AsyncSession) -> None:
    user = await make_user(session)
    await add_currency(session, "USD", "$")
    bank = await create_account(
        session,
        user,
        account_type=AccountType.ASSET,
        name="Bank",
        currency_code="USD",
    )
    food = await create_account(
        session,
        user,
        account_type=AccountType.EXPENSE,
        name="Food",
        currency_code="USD",
    )

    with pytest.raises(LedgerError, match="balance to zero"):
        await post_journal(
            session,
            user,
            journal_type=JournalType.WITHDRAWAL,
            journal_date=date(2026, 1, 1),
            description="Broken",
            currency_code="USD",
            splits=[
                Split(account_id=bank.id, amount=Decimal("-100")),
                Split(account_id=food.id, amount=Decimal("90")),
            ],
        )


async def test_single_split_is_rejected(session: AsyncSession) -> None:
    user = await make_user(session)
    await add_currency(session, "USD", "$")
    bank = await create_account(
        session,
        user,
        account_type=AccountType.ASSET,
        name="Bank",
        currency_code="USD",
    )

    with pytest.raises(LedgerError, match="at least two"):
        await post_journal(
            session,
            user,
            journal_type=JournalType.WITHDRAWAL,
            journal_date=date(2026, 1, 1),
            description="One-sided",
            currency_code="USD",
            splits=[Split(account_id=bank.id, amount=Decimal("0"))],
        )


async def test_accounts_must_belong_to_user(session: AsyncSession) -> None:
    owner = await make_user(session)
    other = User(
        email="other@example.com",
        password_hash="not-used",
        display_name="Other",
        timezone="UTC",
        locale="en_US",
    )
    session.add(other)
    await session.flush()
    await add_currency(session, "USD", "$")

    owner_account = await create_account(
        session,
        owner,
        account_type=AccountType.ASSET,
        name="Owner bank",
        currency_code="USD",
    )
    other_account = await create_account(
        session,
        other,
        account_type=AccountType.EXPENSE,
        name="Other food",
        currency_code="USD",
    )

    with pytest.raises(LedgerError, match="owned by this user"):
        await post_journal(
            session,
            owner,
            journal_type=JournalType.WITHDRAWAL,
            journal_date=date(2026, 1, 1),
            description="Cross-user",
            currency_code="USD",
            splits=[
                Split(account_id=owner_account.id, amount=Decimal("-10")),
                Split(account_id=other_account.id, amount=Decimal("10")),
            ],
        )


async def test_journal_currency_must_match_accounts(session: AsyncSession) -> None:
    user = await make_user(session)
    await add_currency(session, "USD", "$")
    await add_currency(session, "EUR", "€")
    usd_account = await create_account(
        session,
        user,
        account_type=AccountType.ASSET,
        name="USD bank",
        currency_code="USD",
    )
    eur_account = await create_account(
        session,
        user,
        account_type=AccountType.EXPENSE,
        name="EUR food",
        currency_code="EUR",
    )

    with pytest.raises(LedgerError, match="uses EUR"):
        await post_journal(
            session,
            user,
            journal_type=JournalType.WITHDRAWAL,
            journal_date=date(2026, 1, 1),
            description="Wrong currency",
            currency_code="USD",
            splits=[
                Split(account_id=usd_account.id, amount=Decimal("-10")),
                Split(account_id=eur_account.id, amount=Decimal("10")),
            ],
        )
