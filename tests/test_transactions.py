from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from glowbug.enums import AccountType
from glowbug.models.account import Account
from glowbug.models.currency import Currency
from glowbug.models.user import User
from glowbug.services.accounts import account_balances, create_account, list_accounts, set_user_currencies
from glowbug.services.categories import get_category_by_name, seed_default_categories
from glowbug.services.transactions import (
    assign_journal_category,
    create_transaction,
    list_transactions,
)


async def ensure_currency(session: AsyncSession, code: str, symbol: str) -> None:
    if await session.get(Currency, code) is None:
        session.add(Currency(code=code, name=code, symbol=symbol, decimal_places=2))
        await session.flush()


async def make_user(session: AsyncSession) -> User:
    await ensure_currency(session, "USD", "$")
    user = User(
        email="transactions@example.com",
        password_hash="not-used",
        display_name="Transactions",
        timezone="UTC",
        locale="en_US",
    )
    session.add(user)
    await session.flush()
    await set_user_currencies(session, user, enabled_codes=["USD"], primary_code="USD")
    await seed_default_categories(session, user)
    await session.commit()
    return user


async def test_create_and_balance_transactions(session: AsyncSession) -> None:
    user = await make_user(session)
    bank = await create_account(
        session,
        user,
        account_type=AccountType.ASSET,
        name="Bank",
        currency_code="USD",
        opening_balance=Decimal("1000"),
    )
    savings = await create_account(
        session,
        user,
        account_type=AccountType.ASSET,
        name="Savings",
        currency_code="USD",
    )
    food = await get_category_by_name(session, user.id, "Food & dining")
    income = await get_category_by_name(session, user.id, "Income")
    assert food is not None
    assert income is not None

    withdrawal = await create_transaction(
        session,
        user,
        transaction_type="withdrawal",
        journal_date=date.today(),
        description="Coffee",
        amount=Decimal("50"),
        currency_code="USD",
        account_id=bank.id,
        category_id=food.id,
    )
    deposit = await create_transaction(
        session,
        user,
        transaction_type="deposit",
        journal_date=date.today(),
        description="Salary",
        amount=Decimal("500"),
        currency_code="USD",
        account_id=bank.id,
        category_id=income.id,
    )
    transfer = await create_transaction(
        session,
        user,
        transaction_type="transfer",
        journal_date=date.today(),
        description="Move to savings",
        amount=Decimal("100"),
        currency_code="USD",
        account_id=bank.id,
        destination_account_id=savings.id,
    )

    balances = await account_balances(session, [bank, savings])
    assert balances[bank.id] == Decimal("1350")
    assert balances[savings.id] == Decimal("100")
    assert withdrawal.category_id == food.id
    assert deposit.category_id == income.id
    assert transfer.category_id is None

    visible_accounts = await list_accounts(session, user.id)
    assert {account.name for account in visible_accounts} == {"Bank", "Savings"}

    system_accounts = list(
        await session.scalars(
            select(Account).where(
                Account.user_id == user.id,
                Account.is_system.is_(True),
            )
        )
    )
    assert len(system_accounts) == 2


async def test_list_transactions_views(session: AsyncSession) -> None:
    user = await make_user(session)
    bank = await create_account(
        session,
        user,
        account_type=AccountType.ASSET,
        name="Bank",
        currency_code="USD",
        opening_balance=Decimal("0"),
    )
    await create_transaction(
        session,
        user,
        transaction_type="withdrawal",
        journal_date=date.today(),
        description="Groceries",
        amount=Decimal("42.50"),
        currency_code="USD",
        account_id=bank.id,
    )

    views = await list_transactions(session, user, date.today().replace(day=1), date.today())
    assert len(views) == 1
    view = views[0]
    assert view.journal.description == "Groceries"
    assert view.amount == Decimal("42.500000000000000000")
    assert view.account_name == "Bank"


async def test_assign_category_learns(session: AsyncSession) -> None:
    user = await make_user(session)
    bank = await create_account(
        session,
        user,
        account_type=AccountType.ASSET,
        name="Bank",
        currency_code="USD",
    )
    groceries = await get_category_by_name(session, user.id, "Groceries")
    assert groceries is not None

    journal = await create_transaction(
        session,
        user,
        transaction_type="withdrawal",
        journal_date=date.today(),
        description="FRESH MARKET 12345",
        amount=Decimal("20"),
        currency_code="USD",
        account_id=bank.id,
    )
    updated = await assign_journal_category(session, user, journal, groceries.id, remember=True)
    assert updated == 0
    assert journal.category_id == groceries.id
