from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from glowbug.enums import AccountType
from glowbug.models.currency import Currency
from glowbug.models.user import User
from glowbug.services.accounts import create_account, set_user_currencies
from glowbug.services.categories import get_category_by_name, seed_default_categories
from glowbug.services.subscriptions import (
    add_months,
    create_subscription,
    detect_candidates,
    dismiss_candidate,
    list_subscriptions,
)
from glowbug.services.transactions import create_transaction


async def ensure_currency(session: AsyncSession, code: str, symbol: str) -> None:
    if await session.get(Currency, code) is None:
        session.add(Currency(code=code, name=code, symbol=symbol, decimal_places=2))
        await session.flush()


async def make_user(session: AsyncSession) -> User:
    await ensure_currency(session, "USD", "$")
    user = User(
        email="subscriptions@example.com",
        password_hash="not-used",
        display_name="Subscriptions",
        timezone="UTC",
        locale="en_US",
    )
    session.add(user)
    await session.flush()
    await set_user_currencies(session, user, enabled_codes=["USD"], primary_code="USD")
    await seed_default_categories(session, user)
    await session.commit()
    return user


def test_add_months_handles_month_end() -> None:
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2026, 1, 15), 3) == date(2026, 4, 15)


async def test_detect_and_register_subscription(session: AsyncSession) -> None:
    user = await make_user(session)
    bank = await create_account(
        session,
        user,
        account_type=AccountType.ASSET,
        name="Bank",
        currency_code="USD",
    )
    subscriptions_category = await get_category_by_name(session, user.id, "Subscriptions")
    assert subscriptions_category is not None

    start = date(2026, 1, 15)
    for index in range(3):
        await create_transaction(
            session,
            user,
            transaction_type="withdrawal",
            journal_date=add_months(start, index),
            description="NETFLIX",
            amount=Decimal("15.99"),
            currency_code="USD",
            account_id=bank.id,
            category_id=subscriptions_category.id,
        )

    candidates = await detect_candidates(session, user)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.name == "NETFLIX"
    assert candidate.amount == Decimal("15.990000000000000000")
    assert candidate.frequency == "monthly"
    assert candidate.occurrences == 3
    assert candidate.category_id == subscriptions_category.id
    assert candidate.account_id == bank.id
    assert candidate.next_due_date > date.today()

    subscription, created = await create_subscription(
        session,
        user,
        name=candidate.name,
        amount=candidate.amount,
        currency_code=candidate.currency_code,
        frequency=candidate.frequency,
        next_due_date=candidate.next_due_date,
        account_id=candidate.account_id,
        category_id=candidate.category_id,
        merchant_key=candidate.merchant_key,
        source="detected",
    )
    await session.commit()
    assert created is True
    assert subscription is not None

    duplicate, created_again = await create_subscription(
        session,
        user,
        name=candidate.name,
        amount=candidate.amount,
        currency_code=candidate.currency_code,
        frequency=candidate.frequency,
        next_due_date=candidate.next_due_date,
        account_id=candidate.account_id,
        category_id=candidate.category_id,
        merchant_key=candidate.merchant_key,
        source="detected",
    )
    await session.commit()
    assert created_again is False
    assert duplicate is not None
    assert duplicate.id == subscription.id

    assert await detect_candidates(session, user) == []
    assert len(await list_subscriptions(session, user)) == 1


async def test_dismissed_candidate_is_hidden_and_not_listed(session: AsyncSession) -> None:
    user = await make_user(session)
    bank = await create_account(
        session,
        user,
        account_type=AccountType.ASSET,
        name="Bank",
        currency_code="USD",
    )
    start = date(2026, 1, 15)
    for index in range(3):
        await create_transaction(
            session,
            user,
            transaction_type="withdrawal",
            journal_date=add_months(start, index),
            description="SPOTIFY",
            amount=Decimal("9.99"),
            currency_code="USD",
            account_id=bank.id,
        )

    candidates = await detect_candidates(session, user)
    assert len(candidates) == 1
    candidate = candidates[0]

    await dismiss_candidate(
        session,
        user,
        name=candidate.name,
        merchant_key=candidate.merchant_key,
        amount=candidate.amount,
        currency_code=candidate.currency_code,
        frequency=candidate.frequency,
        next_due_date=candidate.next_due_date,
    )
    await session.commit()

    assert await detect_candidates(session, user) == []
    assert await list_subscriptions(session, user) == []


async def test_irregular_amounts_are_not_detected(session: AsyncSession) -> None:
    user = await make_user(session)
    bank = await create_account(
        session,
        user,
        account_type=AccountType.ASSET,
        name="Bank",
        currency_code="USD",
    )
    start = date(2026, 1, 15)
    amounts = [Decimal("10"), Decimal("20"), Decimal("40")]
    for index, amount in enumerate(amounts):
        await create_transaction(
            session,
            user,
            transaction_type="withdrawal",
            journal_date=add_months(start, index),
            description="IRREGULAR SHOP",
            amount=amount,
            currency_code="USD",
            account_id=bank.id,
        )

    assert await detect_candidates(session, user) == []
