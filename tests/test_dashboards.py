from collections.abc import AsyncIterator
from datetime import date, timedelta
from decimal import Decimal

import httpx
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from glowbug.enums import AccountType, JournalType
from glowbug.main import app
from glowbug.models.currency import Currency
from glowbug.models.exchange_rate import ExchangeRate
from glowbug.models.user import User
from glowbug.security import require_api_user
from glowbug.services.accounts import create_account, set_user_currencies
from glowbug.services.dashboards import (
    build_widget_data,
    get_or_create_default_dashboard,
)
from glowbug.services.ledger import Split, post_journal


async def ensure_currency(session: AsyncSession, code: str, symbol: str) -> None:
    if await session.get(Currency, code) is None:
        session.add(Currency(code=code, name=code, symbol=symbol, decimal_places=2))
        await session.flush()


async def make_user(session: AsyncSession, email: str = "dash@example.com") -> User:
    await ensure_currency(session, "USD", "$")
    await ensure_currency(session, "EUR", "€")
    user = User(
        email=email,
        password_hash="not-used",
        display_name="Dashboard",
        timezone="UTC",
        locale="en_US",
    )
    session.add(user)
    await session.flush()
    await set_user_currencies(
        session,
        user,
        enabled_codes=["USD", "EUR"],
        primary_code="USD",
        secondary_code="EUR",
    )
    await session.commit()
    return user


@pytest_asyncio.fixture
async def api_context(
    client: httpx.AsyncClient,
    session: AsyncSession,
) -> AsyncIterator[tuple[httpx.AsyncClient, object]]:
    user = await make_user(session)
    await create_account(
        session,
        user,
        account_type=AccountType.ASSET,
        name="Bank",
        currency_code="USD",
        opening_balance=Decimal("1000"),
    )
    session.add(
        ExchangeRate(
            base_code="USD",
            quote_code="EUR",
            as_of_date=date.today(),
            rate=Decimal("0.9"),
            source="test",
        )
    )
    await session.commit()
    dashboard = await get_or_create_default_dashboard(session, user)

    async def override_user() -> User:
        return user

    app.dependency_overrides[require_api_user] = override_user
    yield client, dashboard
    app.dependency_overrides.pop(require_api_user, None)


async def test_api_requires_auth(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/widget-catalog")
    assert response.status_code == 401


async def test_dashboard_api_crud(api_context) -> None:  # type: ignore[no-untyped-def]
    client, dashboard = api_context

    response = await client.get("/api/widget-catalog")
    assert response.status_code == 200
    types = {item["type"] for item in response.json()}
    assert {
        "net_worth",
        "account_balances",
        "income_expense",
        "category_spend",
        "balance_sections",
        "tax_summary",
        "fx_rate",
    } <= types

    response = await client.get(f"/api/dashboards/{dashboard.id}")
    assert response.status_code == 200
    body = response.json()
    assert len(body["widgets"]) == 9

    net_worth = next(widget for widget in body["widgets"] if widget["type"] == "net_worth")
    response = await client.get(f"/api/widgets/{net_worth['id']}/data")
    assert response.status_code == 200
    payload = response.json()
    assert payload["kind"] == "number"
    assert payload["primary_currency"] == "USD"
    assert Decimal(payload["value"]) == Decimal("1000")
    assert payload["secondary_currency"] == "EUR"

    sections_widget = next(widget for widget in body["widgets"] if widget["type"] == "balance_sections")
    response = await client.get(f"/api/widgets/{sections_widget['id']}/data")
    assert response.status_code == 200
    sections_payload = response.json()
    assert sections_payload["kind"] == "chart"
    assert sections_payload["chart"] == "doughnut"
    assert sections_payload["labels"] == ["Unallocated"]
    assert sections_payload["datasets"][0]["data"] == [1000.0]
    assert sections_payload["colors"] == ["#94a3b8"]

    tax_widget = next(widget for widget in body["widgets"] if widget["type"] == "tax_summary")
    response = await client.get(f"/api/widgets/{tax_widget['id']}/data")
    assert response.status_code == 200
    tax_payload = response.json()
    assert tax_payload["kind"] == "number"
    assert tax_payload["primary_currency"] == "USD"
    assert tax_payload["subtitle"] == "No tax profiles yet."

    response = await client.patch(
        f"/api/widgets/{net_worth['id']}",
        json={"config": {"currency_mode": "primary"}},
    )
    assert response.status_code == 200
    payload = (await client.get(f"/api/widgets/{net_worth['id']}/data")).json()
    assert payload["secondary_value"] is None
    assert payload["secondary_currency"] is None

    response = await client.patch(
        f"/api/dashboards/{dashboard.id}/layout",
        json={"items": [{"id": net_worth["id"], "x": 2, "y": 1, "w": 5, "h": 2}]},
    )
    assert response.status_code == 200

    body = (await client.get(f"/api/dashboards/{dashboard.id}")).json()
    updated = next(widget for widget in body["widgets"] if widget["id"] == net_worth["id"])
    assert updated["layout"] == {"x": 2, "y": 1, "w": 5, "h": 2}

    response = await client.post(
        f"/api/dashboards/{dashboard.id}/widgets",
        json={"type": "fx_rate", "title": "USD/EUR"},
    )
    assert response.status_code == 201
    widget = response.json()
    assert widget["type"] == "fx_rate"

    response = await client.get(f"/api/widgets/{widget['id']}/data")
    assert response.status_code == 200
    fx_payload = response.json()
    assert fx_payload["kind"] == "rates"
    assert fx_payload["base"] == "USD"
    assert fx_payload["rows"][0]["quote"] == "EUR"
    assert Decimal(fx_payload["rows"][0]["rate"]) == Decimal("0.9")
    assert fx_payload["rows"][0]["changes"] == {}

    response = await client.patch(f"/api/widgets/{widget['id']}", json={"title": "Rate"})
    assert response.status_code == 200
    assert response.json()["title"] == "Rate"

    response = await client.delete(f"/api/widgets/{widget['id']}")
    assert response.status_code == 204

    response = await client.get(f"/api/widgets/{widget['id']}/data")
    assert response.status_code == 404


async def test_default_dashboard_is_idempotent(session: AsyncSession) -> None:
    user = await make_user(session)
    first = await get_or_create_default_dashboard(session, user)
    second = await get_or_create_default_dashboard(session, user)

    assert first.id == second.id
    assert len(second.widgets) == 9


async def test_income_expense_widget_data(session: AsyncSession) -> None:
    user = await make_user(session)
    bank = await create_account(
        session,
        user,
        account_type=AccountType.ASSET,
        name="Bank",
        currency_code="USD",
    )
    food = await create_account(
        session,
        user,
        account_type=AccountType.EXPENSE,
        name="Food",
        currency_code="USD",
    )
    salary = await create_account(
        session,
        user,
        account_type=AccountType.REVENUE,
        name="Salary",
        currency_code="USD",
    )

    today = date.today()
    await post_journal(
        session,
        user,
        journal_type=JournalType.WITHDRAWAL,
        journal_date=today,
        description="Groceries",
        currency_code="USD",
        splits=[
            Split(account_id=bank.id, amount=Decimal("-100")),
            Split(account_id=food.id, amount=Decimal("100")),
        ],
    )
    await post_journal(
        session,
        user,
        journal_type=JournalType.DEPOSIT,
        journal_date=today,
        description="Salary",
        currency_code="USD",
        splits=[
            Split(account_id=bank.id, amount=Decimal("500")),
            Split(account_id=salary.id, amount=Decimal("-500")),
        ],
    )
    await session.commit()

    dashboard = await get_or_create_default_dashboard(session, user)
    widget = next(widget for widget in dashboard.widgets if widget.type == "income_expense")
    data = await build_widget_data(session, user, widget, today.replace(day=1), today)

    assert data["kind"] == "chart"
    assert data["chart"] == "bar"
    assert len(data["labels"]) >= 1
    assert sum(data["datasets"][0]["data"]) == 500.0
    assert sum(data["datasets"][1]["data"]) == 100.0


async def test_exchange_rate_widget_shows_movements(session: AsyncSession) -> None:
    user = await make_user(session)
    today = date.today()
    session.add_all(
        [
            ExchangeRate(
                base_code="USD",
                quote_code="EUR",
                as_of_date=today,
                rate=Decimal("0.90"),
                source="test",
            ),
            ExchangeRate(
                base_code="USD",
                quote_code="EUR",
                as_of_date=today - timedelta(days=7),
                rate=Decimal("0.80"),
                source="test",
            ),
            ExchangeRate(
                base_code="USD",
                quote_code="EUR",
                as_of_date=today - timedelta(days=30),
                rate=Decimal("0.85"),
                source="test",
            ),
        ]
    )
    await session.commit()

    dashboard = await get_or_create_default_dashboard(session, user)
    widget = next(widget for widget in dashboard.widgets if widget.type == "fx_rate")
    data = await build_widget_data(session, user, widget, today - timedelta(days=30), today)

    row = data["rows"][0]
    assert row["quote"] == "EUR"
    assert Decimal(row["rate"]) == Decimal("0.90")
    assert Decimal(row["changes"]["7d"]["delta"]) == Decimal("0.10")
    assert Decimal(row["changes"]["7d"]["percent"]) == Decimal("12.50")
    assert Decimal(row["changes"]["30d"]["delta"]) == Decimal("0.05")
