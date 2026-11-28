from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from glowbug.enums import AccountType, JournalType
from glowbug.models.category import MerchantMemory
from glowbug.models.currency import Currency
from glowbug.models.user import User
from glowbug.services.accounts import create_account, set_user_currencies
from glowbug.services.categories import (
    create_category,
    delete_category,
    get_category_by_name,
    seed_default_categories,
    update_category,
)
from glowbug.services.categorizer import (
    assign_category,
    learn_merchant,
    normalize_merchant,
    predict_category,
)
from glowbug.services.ledger import Split, post_journal


async def ensure_currency(session: AsyncSession, code: str, symbol: str) -> None:
    if await session.get(Currency, code) is None:
        session.add(Currency(code=code, name=code, symbol=symbol, decimal_places=2))
        await session.flush()


async def make_user(session: AsyncSession, email: str = "categories@example.com") -> User:
    await ensure_currency(session, "USD", "$")
    user = User(
        email=email,
        password_hash="not-used",
        display_name="Categories",
        timezone="UTC",
        locale="en_US",
    )
    session.add(user)
    await session.flush()
    await set_user_currencies(
        session,
        user,
        enabled_codes=["USD"],
        primary_code="USD",
    )
    await seed_default_categories(session, user)
    await session.commit()
    return user


async def test_seed_defaults_is_idempotent(session: AsyncSession) -> None:
    user = await make_user(session)
    assert await seed_default_categories(session, user) == 0
    await session.commit()

    grocery = await get_category_by_name(session, user.id, "Groceries")
    assert grocery is not None


async def test_custom_category_crud(session: AsyncSession) -> None:
    user = await make_user(session)

    category = await create_category(session, user, "Coffee", "#123456")
    assert category.name == "Coffee"
    assert category.normalized_name == "coffee"

    with pytest.raises(ValueError, match="already exists"):
        await create_category(session, user, "coffee", "#654321")

    updated = await update_category(session, category, "Coffee shops", "#abcdef")
    assert updated.name == "Coffee shops"
    assert updated.color == "#abcdef"

    await delete_category(session, category)
    await session.commit()
    assert await get_category_by_name(session, user.id, "Coffee shops") is None


def test_normalize_merchant_removes_noise() -> None:
    assert normalize_merchant("POS DEBIT STARBUCKS 12345") == "starbucks"
    assert normalize_merchant("Blue Bottle Coffee") == "blue bottle coffee"
    assert normalize_merchant("7-Eleven") == "7 eleven"


async def test_exact_and_fuzzy_memory(session: AsyncSession) -> None:
    user = await make_user(session)
    food = await get_category_by_name(session, user.id, "Food & dining")
    assert food is not None

    await learn_merchant(session, user, "POS STARBUCKS 12345", food.id)
    await learn_merchant(session, user, "Blue Bottle Coffee", food.id)
    await session.commit()

    exact = await predict_category(session, user, "STARBUCKS 99999")
    assert exact is not None
    assert exact.category_name == "Food & dining"
    assert exact.source == "memory"
    assert exact.confidence == 1.0

    fuzzy = await predict_category(session, user, "Blue Bottle Coffe")
    assert fuzzy is not None
    assert fuzzy.category_name == "Food & dining"
    assert fuzzy.source == "memory"


async def test_small_model_learns_category_patterns(session: AsyncSession) -> None:
    user = await make_user(session)
    subscriptions = await get_category_by_name(session, user.id, "Subscriptions")
    transport = await get_category_by_name(session, user.id, "Transport")
    assert subscriptions is not None
    assert transport is not None

    for description in ("netflix subscription", "spotify subscription"):
        await learn_merchant(session, user, description, subscriptions.id)
    for description in ("shell fuel station", "esso fuel station"):
        await learn_merchant(session, user, description, transport.id)
    await session.commit()

    prediction = await predict_category(session, user, "youtube subscription")
    assert prediction is not None
    assert prediction.category_name == "Subscriptions"
    assert prediction.source == "model"


async def test_assign_category_learns_and_updates_existing(session: AsyncSession) -> None:
    user = await make_user(session)
    food = await get_category_by_name(session, user.id, "Food & dining")
    assert food is not None

    bank = await create_account(
        session,
        user,
        account_type=AccountType.ASSET,
        name="Bank",
        currency_code="USD",
    )
    expense = await create_account(
        session,
        user,
        account_type=AccountType.EXPENSE,
        name="Expenses",
        currency_code="USD",
    )

    first = await post_journal(
        session,
        user,
        journal_type=JournalType.WITHDRAWAL,
        journal_date=date.today(),
        description="STARBUCKS 11111",
        currency_code="USD",
        splits=[
            Split(account_id=bank.id, amount=Decimal("-10")),
            Split(account_id=expense.id, amount=Decimal("10")),
        ],
    )
    second = await post_journal(
        session,
        user,
        journal_type=JournalType.WITHDRAWAL,
        journal_date=date.today(),
        description="STARBUCKS 22222",
        currency_code="USD",
        splits=[
            Split(account_id=bank.id, amount=Decimal("-12")),
            Split(account_id=expense.id, amount=Decimal("12")),
        ],
    )
    await session.commit()

    updated = await assign_category(session, user, first, food.id)
    await session.commit()

    assert updated == 1
    assert second.category_id == food.id
    assert second.category_source == "memory"

    memory = await session.scalar(
        select(MerchantMemory).where(
            MerchantMemory.user_id == user.id,
            MerchantMemory.merchant_key == "starbucks",
        )
    )
    assert memory is not None
    assert memory.category_id == food.id
