from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from glowbug.enums import AccountType, JournalType
from glowbug.models.currency import Currency
from glowbug.models.transaction import TransactionJournal
from glowbug.models.user import User
from glowbug.services.accounts import account_balances, create_account, set_user_currencies
from glowbug.services.income import (
    apply_due_income,
    create_schedule,
    income_summary,
    list_schedule_views,
)


async def ensure_currency(session: AsyncSession, code: str, symbol: str) -> None:
    if await session.get(Currency, code) is None:
        session.add(Currency(code=code, name=code, symbol=symbol, decimal_places=2))
        await session.flush()


async def make_user(session: AsyncSession) -> User:
    await ensure_currency(session, "USD", "$")
    user = User(
        email="income@example.com",
        password_hash="not-used",
        display_name="Income",
        timezone="UTC",
        locale="en_US",
    )
    session.add(user)
    await session.flush()
    await set_user_currencies(session, user, enabled_codes=["USD"], primary_code="USD")
    await session.commit()
    return user


async def make_bank(session: AsyncSession, user: User):  # type: ignore[no-untyped-def]
    return await create_account(
        session,
        user,
        account_type=AccountType.ASSET,
        name="Bank",
        currency_code="USD",
    )


async def test_recurring_income_is_applied_once_per_month(session: AsyncSession) -> None:
    user = await make_user(session)
    bank = await make_bank(session, user)
    schedule = await create_schedule(
        session,
        user,
        name="Salary",
        amount=Decimal("2500"),
        account_id=bank.id,
        category_id=None,
        day_of_month=1,
        start_date=date(2026, 1, 1),
        end_date=None,
        notes=None,
    )
    await session.commit()

    first = await apply_due_income(session, user, date(2026, 1, 1))
    assert len(first) == 1
    journal = first[0]
    assert journal.type == JournalType.DEPOSIT.value
    assert journal.currency_code == "USD"
    assert journal.description == "Salary"

    repeat = await apply_due_income(session, user, date(2026, 1, 15))
    assert repeat == []

    balances = await account_balances(session, [bank])
    assert balances[bank.id] == Decimal("2500")

    second = await apply_due_income(session, user, date(2026, 2, 3))
    assert len(second) == 1
    assert schedule.last_applied_on == date(2026, 2, 1)

    count = await session.scalar(select(func.count(TransactionJournal.id)).where(TransactionJournal.user_id == user.id))
    assert count == 2


async def test_income_waits_for_pay_day_and_skips_inactive(session: AsyncSession) -> None:
    user = await make_user(session)
    bank = await make_bank(session, user)
    await create_schedule(
        session,
        user,
        name="Consulting",
        amount=Decimal("400"),
        account_id=bank.id,
        category_id=None,
        day_of_month=20,
        start_date=date(2026, 1, 1),
        end_date=None,
        notes=None,
    )
    paused = await create_schedule(
        session,
        user,
        name="Paused",
        amount=Decimal("900"),
        account_id=bank.id,
        category_id=None,
        day_of_month=1,
        start_date=date(2026, 1, 1),
        end_date=None,
        notes=None,
        active=False,
    )
    await session.commit()

    assert await apply_due_income(session, user, date(2026, 1, 10)) == []
    applied = await apply_due_income(session, user, date(2026, 1, 20))
    assert len(applied) == 1
    assert applied[0].date == date(2026, 1, 20)

    views = await list_schedule_views(session, user, date(2026, 1, 21))
    by_name = {view.schedule.name: view for view in views}
    assert by_name["Consulting"].next_payment == date(2026, 2, 20)
    assert by_name["Paused"].next_payment is None
    assert paused.active is False


async def test_schedule_respects_end_date_and_summary(session: AsyncSession) -> None:
    user = await make_user(session)
    bank = await make_bank(session, user)
    await create_schedule(
        session,
        user,
        name="Contract",
        amount=Decimal("1000"),
        account_id=bank.id,
        category_id=None,
        day_of_month=15,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 2, 28),
        notes=None,
    )
    await session.commit()

    assert len(await apply_due_income(session, user, date(2026, 1, 15))) == 1
    assert len(await apply_due_income(session, user, date(2026, 2, 15))) == 1
    assert await apply_due_income(session, user, date(2026, 3, 15)) == []

    summary = await income_summary(session, user, date(2026, 2, 1))
    assert summary.monthly_total == Decimal("1000")
    assert summary.currency_code == "USD"

    ended = await income_summary(session, user, date(2026, 3, 1))
    assert ended.monthly_total == Decimal("0")
