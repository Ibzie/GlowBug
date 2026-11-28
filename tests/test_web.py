import re
from datetime import date
from decimal import Decimal
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from glowbug.models.account import Account
from glowbug.models.category import Category
from glowbug.models.tax import TaxProfile
from glowbug.models.transaction import TransactionJournal
from glowbug.models.user import User, UserCurrency
from glowbug.services.accounts import get_primary_currency, get_secondary_currency
from glowbug.services.settings import set_setting
from glowbug.services.transactions import create_transaction

SETUP_DATA = {
    "email": "ibzie@example.com",
    "display_name": "Ibzie",
    "password": "longpassword",
    "password_confirm": "longpassword",
    "timezone": "Asia/Karachi",
    "locale": "en_US",
    "primary_currency": "PKR",
    "secondary_currency": "USD",
}

ACCOUNT_DATA = {
    "account_name": ["Bank", "Cash"],
    "account_type": ["asset", "cash"],
    "account_currency": ["PKR", "PKR"],
    "account_opening_balance": ["100000", "5000"],
}


async def post_form(
    client: httpx.AsyncClient,
    path: str,
    fields: dict[str, list[str]],
    *,
    follow_redirects: bool = False,
) -> httpx.Response:
    body = urlencode(fields, doseq=True)
    return await client.post(
        path,
        content=body,
        headers={"content-type": "application/x-www-form-urlencoded"},
        follow_redirects=follow_redirects,
    )


async def complete_setup(client: httpx.AsyncClient) -> None:
    response = await post_form(client, "/setup", {key: [value] for key, value in SETUP_DATA.items()})
    assert response.status_code == 303
    response = await post_form(client, "/setup/accounts", ACCOUNT_DATA)
    assert response.status_code == 303


async def test_first_run_setup_and_dashboard(client: httpx.AsyncClient, session: AsyncSession) -> None:
    response = await client.get("/")
    assert response.status_code == 303
    assert response.headers["location"] == "/setup"

    response = await client.get("/setup")
    assert response.status_code == 200
    assert "Create your GlowBug account" in response.text

    response = await post_form(client, "/setup", {key: [value] for key, value in SETUP_DATA.items()})
    assert response.status_code == 303
    assert response.headers["location"] == "/setup/accounts"

    user = await session.scalar(select(User).where(User.email == SETUP_DATA["email"]))
    assert user is not None
    primary = await get_primary_currency(session, user.id)
    secondary = await get_secondary_currency(session, user.id)
    assert primary is not None and primary.code == "PKR"
    assert secondary is not None and secondary.code == "USD"

    response = await client.get("/setup/accounts")
    assert response.status_code == 200
    assert "Add your first accounts" in response.text

    response = await post_form(client, "/setup/accounts", ACCOUNT_DATA)
    assert response.status_code == 303
    assert response.headers["location"] == "/"

    accounts = list(await session.scalars(select(Account).where(Account.user_id == user.id)))
    assert len(accounts) == 2
    assert {account.name for account in accounts} == {"Bank", "Cash"}

    response = await client.get("/")
    assert response.status_code == 200
    assert "Net worth" in response.text
    assert "PKR" in response.text
    assert "USD" in response.text


async def test_login_and_logout(client: httpx.AsyncClient, session: AsyncSession) -> None:
    await complete_setup(client)

    response = await client.post("/logout", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"

    response = await client.get("/")
    assert response.status_code == 303
    assert response.headers["location"] == "/login"

    response = await post_form(
        client,
        "/login",
        {"email": [SETUP_DATA["email"]], "password": ["wrong-password"]},
    )
    assert response.status_code == 401
    assert "incorrect" in response.text.lower()

    response = await post_form(
        client,
        "/login",
        {"email": [SETUP_DATA["email"]], "password": [SETUP_DATA["password"]]},
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"

    response = await client.get("/")
    assert response.status_code == 200


async def test_change_primary_and_secondary_currency(
    client: httpx.AsyncClient,
    session: AsyncSession,
) -> None:
    await complete_setup(client)

    response = await post_form(
        client,
        "/settings/currencies",
        {
            "enabled_currencies": ["PKR", "USD", "EUR"],
            "primary_currency": ["USD"],
            "secondary_currency": ["EUR"],
        },
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/settings/currencies"

    user = await session.scalar(select(User).where(User.email == SETUP_DATA["email"]))
    assert user is not None
    primary = await get_primary_currency(session, user.id)
    secondary = await get_secondary_currency(session, user.id)
    assert primary is not None and primary.code == "USD"
    assert secondary is not None and secondary.code == "EUR"

    entries = list(await session.scalars(select(UserCurrency).where(UserCurrency.user_id == user.id)))
    assert {entry.currency_code for entry in entries} == {"PKR", "USD", "EUR"}

    response = await client.get("/")
    assert response.status_code == 200
    assert "USD" in response.text


async def test_create_additional_account(client: httpx.AsyncClient, session: AsyncSession) -> None:
    await complete_setup(client)

    response = await client.get("/accounts/new")
    assert response.status_code == 200

    response = await post_form(
        client,
        "/accounts",
        {
            "name": ["USD wallet"],
            "type": ["cash"],
            "currency": ["USD"],
            "opening_balance": ["250.50"],
        },
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/accounts"

    user = await session.scalar(select(User).where(User.email == SETUP_DATA["email"]))
    assert user is not None
    account = await session.scalar(select(Account).where(Account.user_id == user.id, Account.name == "USD wallet"))
    assert account is not None
    assert account.currency_code == "USD"

    response = await client.get("/accounts")
    assert response.status_code == 200
    assert "USD wallet" in response.text


async def test_dashboard_renders_widget_builder(client: httpx.AsyncClient) -> None:
    await complete_setup(client)

    response = await client.get("/")
    assert response.status_code == 200
    assert "dashboard-bootstrap" in response.text
    assert "grid-stack" in response.text
    assert "Add widget" in response.text
    assert "Net worth" in response.text


async def test_dashboard_shows_last_sync_discreetly(
    client: httpx.AsyncClient,
    session: AsyncSession,
) -> None:
    await complete_setup(client)
    await set_setting(session, "fx.last_sync_at", "2026-11-28T06:00:00+00:00")
    await session.commit()

    response = await client.get("/")
    assert response.status_code == 200
    assert "last sync" in response.text.lower()
    assert "28 Nov 2026" in response.text


async def test_transactions_page_and_create(
    client: httpx.AsyncClient,
    session: AsyncSession,
) -> None:
    await complete_setup(client)

    response = await client.get("/transactions")
    assert response.status_code == 200
    assert "Transactions" in response.text

    user = await session.scalar(select(User).where(User.email == SETUP_DATA["email"]))
    assert user is not None
    account = await session.scalar(select(Account).where(Account.user_id == user.id, Account.name == "Bank"))
    assert account is not None
    category = await session.scalar(
        select(Category).where(Category.user_id == user.id, Category.normalized_name == "groceries")
    )
    assert category is not None

    response = await post_form(
        client,
        "/transactions",
        {
            "type": ["withdrawal"],
            "date": [date.today().isoformat()],
            "description": ["Weekly groceries"],
            "amount": ["75.25"],
            "account_id": [str(account.id)],
            "destination_account_id": [""],
            "category_id": [str(category.id)],
            "notes": [""],
        },
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/transactions"

    response = await client.get("/transactions")
    assert response.status_code == 200
    assert "Weekly groceries" in response.text
    assert "Groceries" in response.text


async def test_csv_import_is_idempotent_and_detects_subscription(
    client: httpx.AsyncClient,
    session: AsyncSession,
) -> None:
    await complete_setup(client)
    user = await session.scalar(select(User).where(User.email == SETUP_DATA["email"]))
    assert user is not None
    account = await session.scalar(select(Account).where(Account.user_id == user.id, Account.name == "Bank"))
    assert account is not None
    subscriptions_category = await session.scalar(
        select(Category).where(Category.user_id == user.id, Category.normalized_name == "subscriptions")
    )
    assert subscriptions_category is not None

    csv_data = (
        b"date,description,amount,category\n"
        b"2026-01-15,NETFLIX,15.99,Subscriptions\n"
        b"2026-02-15,NETFLIX,15.99,Subscriptions\n"
        b"2026-03-15,NETFLIX,15.99,Subscriptions\n"
        b"2026-01-20,COFFEE SHOP,4.50,Food & dining\n"
    )

    response = await client.post(
        "/transactions/import/upload",
        files={"file": ("statement.csv", csv_data, "text/csv")},
    )
    assert response.status_code == 200
    token_match = re.search(r'name="token" value="([^"]+)"', response.text)
    assert token_match is not None
    token = token_match.group(1)

    mapping = {
        "token": [token],
        "date_column": ["date"],
        "description_column": ["description"],
        "amount_column": ["amount"],
        "currency_column": [""],
        "category_column": ["category"],
        "account_id": [str(account.id)],
        "date_format": ["%Y-%m-%d"],
        "sign_convention": ["expense"],
    }

    response = await post_form(client, "/transactions/import/preview", mapping)
    assert response.status_code == 200
    assert "4 row(s) parsed" in response.text
    assert "NETFLIX" in response.text

    response = await post_form(client, "/transactions/import/confirm", mapping)
    assert response.status_code == 200
    assert "Statement processed" in response.text
    assert "Recurring payments found" in response.text
    assert "Create subscription" in response.text

    journal_count = len(
        list(await session.scalars(select(TransactionJournal).where(TransactionJournal.user_id == user.id)))
    )
    assert journal_count == 4

    response = await post_form(
        client,
        "/subscriptions/from-candidate",
        {
            "name": ["NETFLIX"],
            "merchant_key": ["netflix"],
            "amount": ["15.99"],
            "currency_code": [account.currency_code],
            "frequency": ["monthly"],
            "next_due_date": ["2026-04-15"],
            "account_id": [str(account.id)],
            "category_id": [str(subscriptions_category.id)],
        },
    )
    assert response.status_code == 303
    response = await client.get("/subscriptions")
    assert "NETFLIX" in response.text

    # Upload and import the same file again: no duplicate transactions.
    response = await client.post(
        "/transactions/import/upload",
        files={"file": ("statement.csv", csv_data, "text/csv")},
    )
    token_match = re.search(r'name="token" value="([^"]+)"', response.text)
    assert token_match is not None
    mapping["token"] = [token_match.group(1)]
    response = await post_form(client, "/transactions/import/confirm", mapping)
    assert response.status_code == 200
    assert "Statement processed" in response.text

    journals = list(await session.scalars(select(TransactionJournal).where(TransactionJournal.user_id == user.id)))
    assert len(journals) == 4


async def test_categories_seeded_and_custom_category(client: httpx.AsyncClient) -> None:
    await complete_setup(client)

    response = await client.get("/categories")
    assert response.status_code == 200
    assert "Groceries" in response.text
    assert "Food &amp; dining" in response.text

    response = await post_form(
        client,
        "/categories",
        {"name": ["Coffee"], "color": ["#123456"]},
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/categories"

    response = await client.get("/categories")
    assert response.status_code == 200
    assert "Coffee" in response.text


async def test_taxes_page_and_profile(
    client: httpx.AsyncClient,
    session: AsyncSession,
) -> None:
    await complete_setup(client)
    user = await session.scalar(select(User).where(User.email == SETUP_DATA["email"]))
    assert user is not None
    account = await session.scalar(select(Account).where(Account.user_id == user.id, Account.name == "Bank"))
    assert account is not None
    income = await session.scalar(
        select(Category).where(Category.user_id == user.id, Category.normalized_name == "income")
    )
    assert income is not None

    await create_transaction(
        session,
        user,
        transaction_type="deposit",
        journal_date=date.today(),
        description="Salary",
        amount=Decimal("1000"),
        currency_code=account.currency_code,
        account_id=account.id,
        category_id=income.id,
    )

    response = await client.get("/taxes")
    assert response.status_code == 200
    assert "Tax estimates" in response.text

    year = date.today().year
    response = await post_form(
        client,
        "/taxes/profiles",
        {
            "name": ["Income tax"],
            "rate": ["20"],
            "currency_code": [account.currency_code],
            "tax_year": [str(year)],
            "category_ids": [str(income.id)],
            "is_active": ["1"],
        },
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/taxes?year={year}"

    response = await client.get(f"/taxes?year={year}")
    assert "Income tax" in response.text
    assert "Estimated tax" in response.text

    profile = await session.scalar(
        select(TaxProfile).where(TaxProfile.user_id == user.id, TaxProfile.name == "Income tax")
    )
    assert profile is not None
    response = await post_form(
        client,
        f"/taxes/profiles/{profile.id}/payments",
        {
            "amount": ["50"],
            "paid_on": [date.today().isoformat()],
            "note": ["First payment"],
        },
    )
    assert response.status_code == 303

    response = await client.get(f"/taxes?year={year}")
    assert "First payment" in response.text


async def test_setup_rejects_mismatched_passwords(client: httpx.AsyncClient) -> None:
    data = dict(SETUP_DATA)
    data["password_confirm"] = "different-password"

    response = await post_form(client, "/setup", {key: [value] for key, value in data.items()})
    assert response.status_code == 400
    assert "Passwords do not match" in response.text


async def test_setup_rejects_same_primary_and_secondary(client: httpx.AsyncClient) -> None:
    data = dict(SETUP_DATA)
    data["secondary_currency"] = "PKR"

    response = await post_form(client, "/setup", {key: [value] for key, value in data.items()})
    assert response.status_code == 400
    assert "must be different" in response.text
