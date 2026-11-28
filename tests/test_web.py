from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from glowbug.models.account import Account
from glowbug.models.user import User, UserCurrency
from glowbug.services.accounts import get_primary_currency, get_secondary_currency
from glowbug.services.settings import set_setting

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
