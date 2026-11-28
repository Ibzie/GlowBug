from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from glowbug.enums import AccountType
from glowbug.models.currency import Currency
from glowbug.models.user import User
from glowbug.services.accounts import create_account, set_user_currencies
from glowbug.services.savings import (
    add_contribution,
    apply_due_contributions,
    balance_sections,
    create_goal,
    savings_summary,
)


async def ensure_currency(session: AsyncSession, code: str, symbol: str) -> None:
    if await session.get(Currency, code) is None:
        session.add(Currency(code=code, name=code, symbol=symbol, decimal_places=2))
        await session.flush()


async def make_user(session: AsyncSession) -> User:
    await ensure_currency(session, "USD", "$")
    await ensure_currency(session, "EUR", "€")
    user = User(
        email="savings@example.com",
        password_hash="not-used",
        display_name="Savings",
        timezone="UTC",
        locale="en_US",
    )
    session.add(user)
    await session.flush()
    await set_user_currencies(session, user, enabled_codes=["USD", "EUR"], primary_code="USD")
    await session.commit()
    return user


async def make_goal(session: AsyncSession, user: User, **overrides):  # type: ignore[no-untyped-def]
    values = {
        "name": "Holiday",
        "target_amount": Decimal("1000"),
        "currency_code": "USD",
        "monthly_contribution": Decimal("200"),
        "contribution_day": 5,
        "start_date": date(2026, 1, 1),
        "target_date": None,
        "color": "#123456",
        "notes": None,
    }
    values.update(overrides)
    return await create_goal(session, user, **values)


async def test_goal_contributions_and_summary(session: AsyncSession) -> None:
    user = await make_user(session)
    goal = await make_goal(session, user)
    await add_contribution(
        session,
        user,
        goal,
        amount=Decimal("300"),
        contributed_on=date(2026, 1, 10),
    )
    await session.commit()

    summary = await savings_summary(session, user, date(2026, 1, 15))
    assert len(summary.goals) == 1
    view = summary.goals[0]
    assert view.saved == Decimal("300")
    assert view.remaining == Decimal("700")
    assert view.progress == 30.0
    assert view.next_contribution == date(2026, 2, 5)
    assert summary.total_saved == Decimal("300")
    assert summary.total_target == Decimal("1000")
    assert summary.monthly_commitment == Decimal("200")


async def test_scheduled_contributions_are_monthly_and_capped(session: AsyncSession) -> None:
    user = await make_user(session)
    goal = await make_goal(
        session,
        user,
        name="Laptop",
        target_amount=Decimal("250"),
        monthly_contribution=Decimal("200"),
        contribution_day=1,
    )

    first = await apply_due_contributions(session, user, date(2026, 1, 15))
    assert len(first) == 1
    assert first[0].amount == Decimal("200")
    assert first[0].period == "2026-01"
    assert first[0].contributed_on == date(2026, 1, 1)

    repeat = await apply_due_contributions(session, user, date(2026, 1, 20))
    assert repeat == []

    second = await apply_due_contributions(session, user, date(2026, 2, 2))
    assert len(second) == 1
    assert second[0].amount == Decimal("50")

    third = await apply_due_contributions(session, user, date(2026, 3, 2))
    assert third == []

    summary = await savings_summary(session, user, date(2026, 3, 2))
    assert summary.goals[0].saved == Decimal("250")
    assert summary.goals[0].remaining == Decimal("0")
    assert summary.goals[0].next_contribution is None
    assert summary.goals[0].goal.id == goal.id


async def test_contribution_waits_for_pay_day(session: AsyncSession) -> None:
    user = await make_user(session)
    await make_goal(session, user, name="Car", contribution_day=20)

    assert await apply_due_contributions(session, user, date(2026, 1, 10)) == []
    applied = await apply_due_contributions(session, user, date(2026, 1, 20))
    assert len(applied) == 1
    assert applied[0].contributed_on == date(2026, 1, 20)


async def test_balance_sections_split_net_worth(session: AsyncSession) -> None:
    user = await make_user(session)
    await create_account(
        session,
        user,
        account_type=AccountType.ASSET,
        name="Bank",
        currency_code="USD",
        opening_balance=Decimal("1000"),
    )
    holiday = await make_goal(session, user, name="Holiday", color="#123456")
    laptop = await make_goal(session, user, name="Laptop", color="#abcdef")
    paused = await make_goal(session, user, name="Paused", color="#654321", active=False)
    await add_contribution(session, user, holiday, amount=Decimal("200"), contributed_on=date(2026, 1, 2))
    await add_contribution(session, user, laptop, amount=Decimal("100"), contributed_on=date(2026, 1, 3))
    await add_contribution(session, user, paused, amount=Decimal("50"), contributed_on=date(2026, 1, 4))
    await session.commit()

    sections = await balance_sections(session, user, date(2026, 1, 15))
    assert sections.total == Decimal("1000")
    assert sections.saved == Decimal("300")
    assert sections.available == Decimal("700")
    assert [section.label for section in sections.sections] == ["Unallocated", "Holiday", "Laptop"]
    assert [section.amount for section in sections.sections] == [
        Decimal("700"),
        Decimal("200"),
        Decimal("100"),
    ]
    assert [section.color for section in sections.sections] == ["#94a3b8", "#123456", "#abcdef"]


async def test_balance_sections_clamp_over_allocated_balance(session: AsyncSession) -> None:
    user = await make_user(session)
    await create_account(
        session,
        user,
        account_type=AccountType.ASSET,
        name="Bank",
        currency_code="USD",
        opening_balance=Decimal("100"),
    )
    goal = await make_goal(session, user, name="Dream")
    await add_contribution(session, user, goal, amount=Decimal("500"), contributed_on=date(2026, 1, 2))
    await session.commit()

    sections = await balance_sections(session, user, date(2026, 1, 15))
    assert sections.saved == Decimal("500")
    assert sections.available == Decimal("0")
    assert sections.complete is False
    assert any("larger than your current balance" in warning for warning in sections.warnings)
