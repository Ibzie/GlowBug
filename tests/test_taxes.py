from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from glowbug.enums import AccountType
from glowbug.models.currency import Currency
from glowbug.models.user import User
from glowbug.services.accounts import create_account, set_user_currencies
from glowbug.services.categories import create_category, get_category_by_name, seed_default_categories
from glowbug.services.taxes import (
    calculate_tax,
    create_profile,
    record_payment,
    update_profile,
)
from glowbug.services.transactions import create_transaction


async def ensure_currency(session: AsyncSession, code: str, symbol: str) -> None:
    if await session.get(Currency, code) is None:
        session.add(Currency(code=code, name=code, symbol=symbol, decimal_places=2))
        await session.flush()


async def make_user(session: AsyncSession) -> User:
    await ensure_currency(session, "USD", "$")
    user = User(
        email="taxes@example.com",
        password_hash="not-used",
        display_name="Taxes",
        timezone="UTC",
        locale="en_US",
    )
    session.add(user)
    await session.flush()
    await set_user_currencies(session, user, enabled_codes=["USD"], primary_code="USD")
    await seed_default_categories(session, user)
    await session.commit()
    return user


async def test_tax_calculation_and_payment(session: AsyncSession) -> None:
    user = await make_user(session)
    bank = await create_account(
        session,
        user,
        account_type=AccountType.ASSET,
        name="Bank",
        currency_code="USD",
    )
    income = await get_category_by_name(session, user.id, "Income")
    assert income is not None
    freelance = await create_category(session, user, "Freelance")
    await session.commit()

    year = date.today().year
    await create_transaction(
        session,
        user,
        transaction_type="deposit",
        journal_date=date(year, 2, 1),
        description="Salary",
        amount=Decimal("1000"),
        currency_code="USD",
        account_id=bank.id,
        category_id=income.id,
    )
    await create_transaction(
        session,
        user,
        transaction_type="deposit",
        journal_date=date(year, 3, 1),
        description="Freelance project",
        amount=Decimal("500"),
        currency_code="USD",
        account_id=bank.id,
        category_id=freelance.id,
    )
    await create_transaction(
        session,
        user,
        transaction_type="deposit",
        journal_date=date(year, 4, 1),
        description="Uncategorized deposit",
        amount=Decimal("200"),
        currency_code="USD",
        account_id=bank.id,
    )

    profile = await create_profile(
        session,
        user,
        name="Income tax",
        rate=Decimal("20"),
        currency_code="USD",
        tax_year=year,
        category_ids=[income.id, freelance.id],
    )
    await session.commit()

    summary = await calculate_tax(session, user, year)
    assert summary.total_income == Decimal("1700")
    assert summary.taxable_income == Decimal("1500")
    assert summary.estimated_tax == Decimal("300.00")
    assert summary.paid == 0
    assert summary.remaining == Decimal("300.00")

    profile_summary = summary.profiles[0]
    assert profile_summary.taxable_income == Decimal("1500")
    assert profile_summary.estimated_tax == Decimal("300.00")
    categories = {total.category.name: total for total in profile_summary.category_totals}
    assert categories["Income"].income == Decimal("1000")
    assert categories["Income"].tax == Decimal("200.00")
    assert categories["Freelance"].income == Decimal("500")
    assert categories["Freelance"].tax == Decimal("100.00")

    await record_payment(
        session,
        user,
        profile,
        amount=Decimal("100"),
        paid_on=date.today(),
        note="First instalment",
    )
    await session.commit()

    summary = await calculate_tax(session, user, year)
    assert summary.paid == Decimal("100")
    assert summary.remaining == Decimal("200.00")

    with pytest.raises(ValueError, match="already exists"):
        await create_profile(
            session,
            user,
            name="Income tax",
            rate=Decimal("10"),
            currency_code="USD",
            tax_year=year,
            category_ids=[income.id],
        )

    await update_profile(
        session,
        profile,
        name="Income tax",
        rate=Decimal("20"),
        currency_code="USD",
        tax_year=year,
        category_ids=[income.id, freelance.id],
        is_active=False,
    )
    await session.commit()

    summary = await calculate_tax(session, user, year)
    assert summary.estimated_tax == 0
    assert summary.profiles[0].estimated_tax == Decimal("300.00")
