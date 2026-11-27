from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.ext.asyncio import AsyncSession

from glowbug.enums import NET_WORTH_TYPES, AccountType
from glowbug.models.account import Account
from glowbug.models.currency import Currency
from glowbug.models.user import User
from glowbug.services.accounts import (
    account_balances,
    get_primary_currency,
    get_secondary_currency,
    list_accounts,
)
from glowbug.services.fx.exceptions import RateUnavailableError
from glowbug.services.fx.service import ExchangeRateService


@dataclass
class AccountView:
    account: Account
    native_balance: Decimal
    primary_balance: Decimal | None
    secondary_balance: Decimal | None
    warning: str | None = None


@dataclass
class DashboardData:
    accounts: list[AccountView] = field(default_factory=list)
    net_primary: Decimal = Decimal("0")
    net_secondary: Decimal | None = None
    net_primary_complete: bool = True
    net_secondary_complete: bool = True
    primary_currency: Currency | None = None
    secondary_currency: Currency | None = None
    warnings: list[str] = field(default_factory=list)


def _today_for(user: User):
    try:
        return datetime.now(ZoneInfo(user.timezone)).date()
    except ZoneInfoNotFoundError, ValueError:
        return datetime.now(ZoneInfo("UTC")).date()


async def build_dashboard(session: AsyncSession, user: User) -> DashboardData:
    primary = await get_primary_currency(session, user.id)
    secondary = await get_secondary_currency(session, user.id)
    if primary is None:
        return DashboardData(warnings=["No primary currency is configured."])

    accounts = await list_accounts(session, user.id)
    balances = await account_balances(session, accounts)
    converter = ExchangeRateService(session)
    today = _today_for(user)
    data = DashboardData(
        primary_currency=primary,
        secondary_currency=secondary,
        net_secondary=Decimal("0") if secondary is not None else None,
    )

    for account in accounts:
        native = balances.get(account.id, Decimal("0"))
        primary_balance: Decimal | None = None
        secondary_balance: Decimal | None = None
        warning: str | None = None

        try:
            primary_balance = await converter.convert(native, account.currency_code, primary.code, today)
        except RateUnavailableError:
            warning = f"No {account.currency_code}/{primary.code} rate for {today.isoformat()}."

        if secondary is not None:
            try:
                secondary_balance = await converter.convert(native, account.currency_code, secondary.code, today)
            except RateUnavailableError:
                warning = warning or f"No {account.currency_code}/{secondary.code} rate for {today.isoformat()}."

        view = AccountView(
            account=account,
            native_balance=native,
            primary_balance=primary_balance,
            secondary_balance=secondary_balance,
            warning=warning,
        )
        data.accounts.append(view)

        if account.include_in_net_worth and account.type in {item.value for item in NET_WORTH_TYPES}:
            if primary_balance is None:
                data.net_primary_complete = False
            else:
                data.net_primary += primary_balance

            if secondary is not None and data.net_secondary is not None:
                if secondary_balance is None:
                    data.net_secondary_complete = False
                else:
                    data.net_secondary += secondary_balance

        if warning:
            data.warnings.append(f"{account.name}: {warning}")

    return data


def is_asset_like(account: Account) -> bool:
    return account.type in {
        AccountType.ASSET.value,
        AccountType.CASH.value,
    }
