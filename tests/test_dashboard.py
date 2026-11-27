from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from glowbug.enums import AccountType
from glowbug.models.currency import Currency
from glowbug.models.exchange_rate import ExchangeRate
from glowbug.models.user import User
from glowbug.services.accounts import create_account, set_user_currencies
from glowbug.services.dashboard import build_dashboard


async def make_user(session: AsyncSession) -> User:
    user = User(
        email="dashboard@example.com",
        password_hash="not-used",
        display_name="Dashboard",
        timezone="UTC",
        locale="en_US",
    )
    session.add(user)
    await session.flush()
    return user


async def add_currency(session: AsyncSession, code: str, symbol: str) -> None:
    session.add(Currency(code=code, name=code, symbol=symbol, decimal_places=2))
    await session.flush()


async def test_dashboard_dual_currency_and_missing_rate(session: AsyncSession) -> None:
    user = await make_user(session)
    await add_currency(session, "PKR", "₨")
    await add_currency(session, "USD", "$")
    await set_user_currencies(
        session,
        user,
        enabled_codes=["PKR", "USD"],
        primary_code="PKR",
        secondary_code="USD",
    )
    await create_account(
        session,
        user,
        account_type=AccountType.ASSET,
        name="Bank",
        currency_code="PKR",
        opening_balance=Decimal("1000"),
    )

    data = await build_dashboard(session, user)
    assert data.primary_currency is not None and data.primary_currency.code == "PKR"
    assert data.secondary_currency is not None and data.secondary_currency.code == "USD"
    assert data.net_primary == Decimal("1000")
    assert data.net_primary_complete is True
    assert data.net_secondary_complete is False
    assert data.warnings

    session.add(
        ExchangeRate(
            base_code="PKR",
            quote_code="USD",
            as_of_date=date.today(),
            rate=Decimal("0.0036"),
            source="test",
        )
    )
    await session.flush()

    data = await build_dashboard(session, user)
    assert data.net_secondary_complete is True
    assert data.net_secondary == Decimal("3.600000000000000000")
    assert not data.warnings
