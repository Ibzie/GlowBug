from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal, localcontext
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from glowbug.enums import AccountType, JournalType
from glowbug.models.account import Account
from glowbug.models.dashboard import Dashboard, DashboardWidget
from glowbug.models.transaction import Transaction, TransactionJournal
from glowbug.models.user import User
from glowbug.services.accounts import get_primary_currency, get_secondary_currency
from glowbug.services.dashboard import build_dashboard
from glowbug.services.formatting import format_money
from glowbug.services.fx.exceptions import RateUnavailableError
from glowbug.services.fx.service import ExchangeRateService

VALID_RANGES = {"1M", "3M", "6M", "YTD", "1Y", "ALL"}

WIDGET_CATALOG: list[dict[str, Any]] = [
    {
        "type": "net_worth",
        "name": "Net worth",
        "description": "Total net worth in your primary and secondary currencies.",
        "visualizations": ["number"],
        "default_title": "Net worth",
        "default_config": {"visualization": "number", "currency_mode": "dual"},
        "default_size": {"w": 4, "h": 2},
    },
    {
        "type": "account_balances",
        "name": "Account balances",
        "description": "Current balances across your accounts, converted to your primary currency.",
        "visualizations": ["bar"],
        "default_title": "Account balances",
        "default_config": {"visualization": "bar", "currency_mode": "primary"},
        "default_size": {"w": 8, "h": 3},
    },
    {
        "type": "income_expense",
        "name": "Income vs expenses",
        "description": "Monthly income and spending.",
        "visualizations": ["bar"],
        "default_title": "Income vs expenses",
        "default_config": {"visualization": "bar", "currency_mode": "primary", "period": "month"},
        "default_size": {"w": 6, "h": 3},
    },
    {
        "type": "cash_flow",
        "name": "Cash flow",
        "description": "Monthly net cash flow.",
        "visualizations": ["line"],
        "default_title": "Cash flow",
        "default_config": {"visualization": "line", "currency_mode": "primary", "period": "month"},
        "default_size": {"w": 6, "h": 3},
    },
    {
        "type": "recent_transactions",
        "name": "Recent transactions",
        "description": "Your latest journal entries.",
        "visualizations": ["table"],
        "default_title": "Recent transactions",
        "default_config": {"visualization": "table", "limit": 8},
        "default_size": {"w": 7, "h": 3},
    },
    {
        "type": "fx_rate",
        "name": "Exchange rate",
        "description": "Latest rate between your primary and secondary currencies.",
        "visualizations": ["number"],
        "default_title": "Exchange rate",
        "default_config": {"visualization": "number"},
        "default_size": {"w": 5, "h": 2},
    },
]

CATALOG_BY_TYPE = {item["type"]: item for item in WIDGET_CATALOG}


def default_widgets() -> list[dict[str, Any]]:
    return [
        {"type": "net_worth", "layout": {"x": 0, "y": 0, "w": 4, "h": 2}},
        {"type": "account_balances", "layout": {"x": 4, "y": 0, "w": 8, "h": 3}},
        {"type": "income_expense", "layout": {"x": 0, "y": 3, "w": 6, "h": 3}},
        {"type": "cash_flow", "layout": {"x": 6, "y": 3, "w": 6, "h": 3}},
        {"type": "recent_transactions", "layout": {"x": 0, "y": 6, "w": 7, "h": 3}},
        {"type": "fx_rate", "layout": {"x": 7, "y": 6, "w": 5, "h": 2}},
    ]


def current_date(user: User) -> date:
    try:
        return datetime.now(ZoneInfo(user.timezone)).date()
    except ZoneInfoNotFoundError, ValueError:
        return datetime.now(ZoneInfo("UTC")).date()


def default_range(user: User, range_key: str | None) -> tuple[date, date]:
    today = current_date(user)
    key = (range_key or "6M").upper()
    if key not in VALID_RANGES:
        key = "6M"

    if key == "1M":
        start = today - timedelta(days=30)
    elif key == "3M":
        start = today - timedelta(days=90)
    elif key == "YTD":
        start = date(today.year, 1, 1)
    elif key == "1Y":
        start = today - timedelta(days=365)
    elif key == "ALL":
        start = today - timedelta(days=1825)
    else:
        start = today - timedelta(days=180)

    return start, today


def normalize_config(widget_type: str, config: dict[str, Any] | None) -> dict[str, Any]:
    catalog = CATALOG_BY_TYPE.get(widget_type)
    if catalog is None:
        raise ValueError(f"Unknown widget type: {widget_type}")

    merged = {**catalog["default_config"], **(config or {})}
    if merged.get("visualization") not in catalog["visualizations"]:
        merged["visualization"] = catalog["visualizations"][0]
    if merged.get("currency_mode") not in {"primary", "dual", "native"}:
        merged["currency_mode"] = "primary"
    if widget_type == "recent_transactions":
        try:
            merged["limit"] = max(1, min(50, int(merged.get("limit", 8))))
        except TypeError, ValueError:
            merged["limit"] = 8

    return merged


def normalize_layout(layout: dict[str, Any] | None, default_size: dict[str, int]) -> dict[str, int]:
    raw = layout or {}
    try:
        x = max(0, min(11, int(raw.get("x", 0))))
        y = max(0, int(raw.get("y", 0)))
        w = max(1, min(12 - x, int(raw.get("w", default_size["w"]))))
        h = max(1, min(12, int(raw.get("h", default_size["h"]))))
    except TypeError, ValueError:
        return {"x": 0, "y": 0, "w": default_size["w"], "h": default_size["h"]}

    return {"x": x, "y": y, "w": w, "h": h}


async def get_or_create_default_dashboard(session: AsyncSession, user: User) -> Dashboard:
    statement = (
        select(Dashboard)
        .where(Dashboard.user_id == user.id, Dashboard.is_default.is_(True))
        .options(selectinload(Dashboard.widgets))
        .execution_options(populate_existing=True)
    )
    dashboard = await session.scalar(statement)
    if dashboard is not None:
        return dashboard

    dashboard = Dashboard(user_id=user.id, name="My dashboard", is_default=True)
    session.add(dashboard)
    await session.flush()

    for position, entry in enumerate(default_widgets()):
        catalog = CATALOG_BY_TYPE[entry["type"]]
        session.add(
            DashboardWidget(
                dashboard_id=dashboard.id,
                type=entry["type"],
                title=catalog["default_title"],
                config=dict(catalog["default_config"]),
                layout=entry["layout"],
                position=position,
            )
        )

    await session.commit()
    statement = (
        select(Dashboard)
        .where(Dashboard.id == dashboard.id)
        .options(selectinload(Dashboard.widgets))
        .execution_options(populate_existing=True)
    )
    created = await session.scalar(statement)
    if created is None:
        raise RuntimeError("Dashboard creation failed.")
    return created


async def get_dashboard(session: AsyncSession, user: User, dashboard_id: UUID) -> Dashboard | None:
    statement = (
        select(Dashboard)
        .where(Dashboard.id == dashboard_id, Dashboard.user_id == user.id)
        .options(selectinload(Dashboard.widgets))
        .execution_options(populate_existing=True)
    )
    return await session.scalar(statement)


async def get_widget(session: AsyncSession, user: User, widget_id: UUID) -> DashboardWidget | None:
    statement = (
        select(DashboardWidget)
        .join(Dashboard, Dashboard.id == DashboardWidget.dashboard_id)
        .where(DashboardWidget.id == widget_id, Dashboard.user_id == user.id)
        .options(selectinload(DashboardWidget.dashboard))
        .execution_options(populate_existing=True)
    )
    return await session.scalar(statement)


def serialize_widget(widget: DashboardWidget) -> dict[str, Any]:
    catalog = CATALOG_BY_TYPE.get(widget.type)
    default_size = catalog["default_size"] if catalog else {"w": 4, "h": 3}
    return {
        "id": str(widget.id),
        "type": widget.type,
        "title": widget.title,
        "config": normalize_config(widget.type, widget.config) if catalog else dict(widget.config or {}),
        "layout": normalize_layout(widget.layout, default_size),
    }


def serialize_dashboard(dashboard: Dashboard) -> dict[str, Any]:
    return {
        "id": str(dashboard.id),
        "name": dashboard.name,
        "widgets": [serialize_widget(widget) for widget in dashboard.widgets],
    }


async def create_widget(
    session: AsyncSession,
    dashboard: Dashboard,
    *,
    widget_type: str,
    title: str | None,
    config: dict[str, Any] | None,
) -> DashboardWidget:
    catalog = CATALOG_BY_TYPE.get(widget_type)
    if catalog is None:
        raise ValueError(f"Unknown widget type: {widget_type}")

    normalized_config = normalize_config(widget_type, config)
    max_y = max(
        (int(widget.layout.get("y", 0)) + int(widget.layout.get("h", 1)) for widget in dashboard.widgets),
        default=0,
    )
    layout = normalize_layout(
        {"x": 0, "y": max_y, "w": catalog["default_size"]["w"], "h": catalog["default_size"]["h"]},
        catalog["default_size"],
    )
    widget = DashboardWidget(
        dashboard_id=dashboard.id,
        type=widget_type,
        title=(title or "").strip() or catalog["default_title"],
        config=normalized_config,
        layout=layout,
        position=len(dashboard.widgets),
    )
    session.add(widget)
    await session.commit()
    await session.refresh(widget)
    return widget


async def update_widget(
    session: AsyncSession,
    dashboard: Dashboard,
    widget_id: UUID,
    *,
    title: str | None,
    config: dict[str, Any] | None,
) -> DashboardWidget | None:
    widget = next((item for item in dashboard.widgets if item.id == widget_id), None)
    if widget is None:
        return None

    if title is not None:
        widget.title = title.strip() or widget.title
    if config is not None:
        widget.config = normalize_config(widget.type, {**(widget.config or {}), **config})

    await session.commit()
    await session.refresh(widget)
    return widget


async def delete_widget(session: AsyncSession, dashboard: Dashboard, widget_id: UUID) -> bool:
    widget = next((item for item in dashboard.widgets if item.id == widget_id), None)
    if widget is None:
        return False
    await session.delete(widget)
    await session.commit()
    return True


async def save_layout(
    session: AsyncSession,
    dashboard: Dashboard,
    items: list[dict[str, Any]],
) -> None:
    by_id = {str(item["id"]): item for item in items}
    for widget in dashboard.widgets:
        item = by_id.get(str(widget.id))
        if item is None:
            continue
        catalog = CATALOG_BY_TYPE.get(widget.type)
        default_size = catalog["default_size"] if catalog else {"w": 4, "h": 3}
        widget.layout = normalize_layout(item, default_size)

    await session.commit()


async def build_widget_data(
    session: AsyncSession,
    user: User,
    widget: DashboardWidget,
    start: date,
    end: date,
) -> dict[str, Any]:
    primary = await get_primary_currency(session, user.id)
    secondary = await get_secondary_currency(session, user.id)
    if primary is None:
        return {"kind": "error", "message": "No primary currency is configured."}

    config = normalize_config(widget.type, widget.config)

    if widget.type == "net_worth":
        data = await build_dashboard(session, user)
        return {
            "kind": "number",
            "value": str(data.net_primary),
            "primary_currency": primary.code,
            "secondary_value": str(data.net_secondary) if data.net_secondary is not None else None,
            "secondary_currency": secondary.code if secondary else None,
            "complete": data.net_primary_complete and (not secondary or data.net_secondary_complete),
            "warnings": data.warnings,
        }

    if widget.type == "account_balances":
        data = await build_dashboard(session, user)
        labels = [view.account.name for view in data.accounts]
        values = [float(view.primary_balance) if view.primary_balance is not None else 0.0 for view in data.accounts]
        return {
            "kind": "chart",
            "chart": "bar",
            "labels": labels,
            "datasets": [{"label": f"Balance ({primary.code})", "data": values}],
            "currency": primary.code,
            "warnings": data.warnings,
        }

    if widget.type in {"income_expense", "cash_flow"}:
        labels, income, expenses, warnings = await income_expense_series(
            session,
            user,
            start,
            end,
            primary.code,
        )
        if widget.type == "income_expense":
            return {
                "kind": "chart",
                "chart": "bar",
                "labels": labels,
                "datasets": [
                    {"label": "Income", "data": [float(value) for value in income]},
                    {"label": "Expenses", "data": [float(value) for value in expenses]},
                ],
                "currency": primary.code,
                "warnings": warnings,
            }

        net = [income_value - expense_value for income_value, expense_value in zip(income, expenses, strict=True)]
        return {
            "kind": "chart",
            "chart": "line",
            "labels": labels,
            "datasets": [{"label": "Net cash flow", "data": [float(value) for value in net]}],
            "currency": primary.code,
            "warnings": warnings,
        }

    if widget.type == "recent_transactions":
        rows = await recent_transactions(session, user, start, end, int(config.get("limit", 8)))
        return {
            "kind": "table",
            "columns": ["Date", "Description", "Type", "Amount"],
            "rows": rows,
            "warnings": [],
        }

    if widget.type == "fx_rate":
        quote = secondary.code if secondary else ("EUR" if primary.code != "EUR" else "USD")
        service = ExchangeRateService(session)
        try:
            rate, rate_date = await service.get_rate(primary.code, quote, end)
        except RateUnavailableError:
            return {
                "kind": "number",
                "value": None,
                "base": primary.code,
                "quote": quote,
                "warning": f"No {primary.code}/{quote} rate for {end.isoformat()}.",
            }

        display_base, display_quote = primary.code, quote
        display_rate = rate
        if rate < 1:
            with localcontext() as context:
                context.prec = 38
                display_rate = Decimal("1") / rate
            display_base, display_quote = quote, primary.code

        return {
            "kind": "number",
            "value": str(display_rate),
            "base": display_base,
            "quote": display_quote,
            "rate_date": rate_date.isoformat(),
        }

    return {"kind": "error", "message": f"Unknown widget type: {widget.type}"}


def month_keys(start: date, end: date) -> tuple[list[str], list[str]]:
    labels: list[str] = []
    keys: list[str] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        keys.append(f"{year:04d}-{month:02d}")
        labels.append(date(year, month, 1).strftime("%b %Y"))
        month += 1
        if month > 12:
            month = 1
            year += 1
    return labels, keys


async def income_expense_series(
    session: AsyncSession,
    user: User,
    start: date,
    end: date,
    primary_code: str,
) -> tuple[list[str], list[Decimal], list[Decimal], list[str]]:
    labels, keys = month_keys(start, end)
    income = {key: Decimal("0") for key in keys}
    expenses = {key: Decimal("0") for key in keys}
    warnings: set[str] = set()

    statement = (
        select(
            TransactionJournal.date,
            TransactionJournal.type,
            TransactionJournal.currency_code,
            Transaction.amount,
        )
        .join(Transaction, Transaction.journal_id == TransactionJournal.id)
        .join(Account, Account.id == Transaction.account_id)
        .where(
            TransactionJournal.user_id == user.id,
            TransactionJournal.date >= start,
            TransactionJournal.date <= end,
            TransactionJournal.type.in_([JournalType.DEPOSIT.value, JournalType.WITHDRAWAL.value]),
            Account.type.in_([AccountType.ASSET.value, AccountType.CASH.value]),
        )
    )
    rows = (await session.execute(statement)).all()
    converter = ExchangeRateService(session)
    cache: dict[tuple[str, date], Decimal | None] = {}

    async def converted(amount: Decimal, currency: str, on_date: date) -> Decimal | None:
        if currency == primary_code:
            return amount
        key = (currency, on_date)
        if key not in cache:
            try:
                rate, _ = await converter.get_rate(currency, primary_code, on_date)
                cache[key] = rate
            except RateUnavailableError:
                cache[key] = None
                warnings.add(f"No {currency}/{primary_code} rate for {on_date.isoformat()}.")
        rate = cache[key]
        return None if rate is None else amount * rate

    for row in rows:
        month_key = row.date.strftime("%Y-%m")
        if month_key not in income:
            continue
        if row.type == JournalType.DEPOSIT.value and row.amount > 0:
            value = await converted(row.amount, row.currency_code, row.date)
            if value is not None:
                income[month_key] += value
        elif row.type == JournalType.WITHDRAWAL.value and row.amount < 0:
            value = await converted(-row.amount, row.currency_code, row.date)
            if value is not None:
                expenses[month_key] += value

    return (
        labels,
        [income[key] for key in keys],
        [expenses[key] for key in keys],
        sorted(warnings),
    )


async def recent_transactions(
    session: AsyncSession,
    user: User,
    start: date,
    end: date,
    limit: int,
) -> list[list[str]]:
    statement = (
        select(TransactionJournal)
        .where(
            TransactionJournal.user_id == user.id,
            TransactionJournal.date >= start,
            TransactionJournal.date <= end,
        )
        .options(selectinload(TransactionJournal.transactions))
        .order_by(TransactionJournal.date.desc(), TransactionJournal.created_at.desc())
        .limit(limit)
    )
    journals = list(await session.scalars(statement))
    rows: list[list[str]] = []
    for journal in journals:
        amount = max((abs(transaction.amount) for transaction in journal.transactions), default=Decimal("0"))
        rows.append(
            [
                journal.date.isoformat(),
                journal.description,
                journal.type,
                format_money(amount, journal.currency_code, user.locale),
            ]
        )
    return rows
