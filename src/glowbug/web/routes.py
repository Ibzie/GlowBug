from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from babel import Locale
from babel.core import UnknownLocaleError
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import FormData

from glowbug.db.session import get_session
from glowbug.enums import ACCOUNT_TYPE_LABELS, AccountType
from glowbug.models.account import Account
from glowbug.models.currency import Currency
from glowbug.models.user import User
from glowbug.security import (
    get_session_user,
    hash_password,
    login_session,
    logout_session,
    verify_password,
)
from glowbug.services.accounts import (
    create_account,
    get_primary_currency,
    get_secondary_currency,
    get_user_currencies,
    set_user_currencies,
)
from glowbug.services.categories import seed_default_categories
from glowbug.services.dashboard import build_dashboard
from glowbug.services.dashboards import (
    VALID_RANGES,
    WIDGET_CATALOG,
    default_range,
    get_or_create_default_dashboard,
    serialize_dashboard,
)
from glowbug.services.fx.sync import get_last_sync
from glowbug.web.templating import templates

router = APIRouter()
SessionDep = Annotated[AsyncSession, Depends(get_session)]

ONBOARDING_ACCOUNT_TYPES = [
    (AccountType.ASSET.value, ACCOUNT_TYPE_LABELS[AccountType.ASSET]),
    (AccountType.CASH.value, ACCOUNT_TYPE_LABELS[AccountType.CASH]),
]

ALL_ACCOUNT_TYPES = [
    (account_type.value, ACCOUNT_TYPE_LABELS[account_type])
    for account_type in (
        AccountType.ASSET,
        AccountType.CASH,
        AccountType.EXPENSE,
        AccountType.REVENUE,
        AccountType.LIABILITY,
        AccountType.DEBT,
        AccountType.LOAN,
        AccountType.MORTGAGE,
    )
]

SETUP_FIELDS = (
    "email",
    "display_name",
    "timezone",
    "locale",
    "primary_currency",
    "secondary_currency",
)


def flash(request: Request, message: str, category: str = "info") -> None:
    request.session.setdefault("flashes", []).append({"message": message, "category": category})


def pop_flashes(request: Request) -> list[dict[str, str]]:
    return request.session.pop("flashes", [])


def render(
    request: Request,
    template: str,
    context: dict | None = None,
    *,
    status_code: int = 200,
) -> HTMLResponse:
    payload = dict(context or {})
    payload.setdefault("flashes", pop_flashes(request))
    return templates.TemplateResponse(request, template, payload, status_code=status_code)


async def user_count(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.count(User.id))) or 0)


async def has_accounts(session: AsyncSession, user_id) -> bool:
    return bool(await session.scalar(select(func.count(Account.id)).where(Account.user_id == user_id)))


async def active_currencies(session: AsyncSession) -> list[Currency]:
    statement = select(Currency).where(Currency.is_active.is_(True)).order_by(Currency.code)
    return list(await session.scalars(statement))


def normalize_timezone(value: str) -> str | None:
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError, ValueError:
        return None
    return value


def normalize_locale(value: str) -> str | None:
    normalized = value.replace("-", "_")
    try:
        Locale.parse(normalized, sep="_")
    except UnknownLocaleError, ValueError:
        return None
    return normalized


def parse_decimal(value: str) -> Decimal | None:
    cleaned = value.strip().replace(",", "")
    if not cleaned:
        return Decimal("0")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def form_values(form: FormData) -> dict[str, str]:
    return {field: str(form.get(field, "")) for field in SETUP_FIELDS}


async def currency_settings_context(session: AsyncSession, user: User) -> dict:
    currencies = await active_currencies(session)
    enabled = await get_user_currencies(session, user.id)
    primary = await get_primary_currency(session, user.id)
    secondary = await get_secondary_currency(session, user.id)

    return {
        "user": user,
        "currencies": currencies,
        "enabled_codes": {currency.code for currency in enabled},
        "primary_code": primary.code if primary else "",
        "secondary_code": secondary.code if secondary else "",
    }


@router.get("/setup", response_class=HTMLResponse, response_model=None)
async def setup_page(request: Request, session: SessionDep) -> HTMLResponse | RedirectResponse:
    if await user_count(session) > 0:
        return RedirectResponse("/login", status_code=303)

    return render(
        request,
        "setup.html",
        {"currencies": await active_currencies(session), "values": {}},
    )


@router.post("/setup", response_model=None)
async def setup_submit(request: Request, session: SessionDep) -> HTMLResponse | RedirectResponse:
    if await user_count(session) > 0:
        return RedirectResponse("/login", status_code=303)

    form = await request.form()
    values = form_values(form)
    errors: list[str] = []

    email = values["email"].strip().lower()
    display_name = values["display_name"].strip()
    password = str(form.get("password", ""))
    password_confirm = str(form.get("password_confirm", ""))
    timezone = normalize_timezone(values["timezone"].strip())
    locale = normalize_locale(values["locale"].strip())
    primary_code = values["primary_currency"].strip().upper()
    secondary_code = values["secondary_currency"].strip().upper() or None

    if "@" not in email:
        errors.append("Enter a valid email address.")
    if not display_name:
        errors.append("Enter a display name.")
    if len(password) < 8:
        errors.append("Password must be at least 8 characters.")
    if password != password_confirm:
        errors.append("Passwords do not match.")
    if timezone is None:
        errors.append("Enter a valid IANA timezone, for example Europe/Berlin.")
    if locale is None:
        errors.append("Enter a valid locale, for example en_US.")
    if not primary_code:
        errors.append("Choose a primary currency.")
    if secondary_code == primary_code:
        errors.append("Primary and secondary currency must be different.")

    currencies = await active_currencies(session)
    currency_codes = {currency.code for currency in currencies}
    if primary_code and primary_code not in currency_codes:
        errors.append("Choose a valid primary currency.")
    if secondary_code and secondary_code not in currency_codes:
        errors.append("Choose a valid secondary currency.")

    if errors:
        return render(
            request,
            "setup.html",
            {"currencies": currencies, "values": values, "error": " ".join(errors)},
            status_code=400,
        )

    user = User(
        email=email,
        password_hash=hash_password(password),
        display_name=display_name,
        timezone=timezone or "UTC",
        locale=locale or "en_US",
    )
    session.add(user)

    try:
        await session.flush()
        await set_user_currencies(
            session,
            user,
            enabled_codes=[primary_code] + ([secondary_code] if secondary_code else []),
            primary_code=primary_code,
            secondary_code=secondary_code,
        )
        await seed_default_categories(session, user)
        await session.commit()
    except (IntegrityError, ValueError) as exc:
        await session.rollback()
        return render(
            request,
            "setup.html",
            {"currencies": currencies, "values": values, "error": str(exc)},
            status_code=400,
        )

    login_session(request, user)
    flash(request, "Your account is ready. Add your first accounts.", "success")
    return RedirectResponse("/setup/accounts", status_code=303)


@router.get("/setup/accounts", response_class=HTMLResponse, response_model=None)
async def setup_accounts_page(request: Request, session: SessionDep) -> HTMLResponse | RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    if await has_accounts(session, user.id):
        return RedirectResponse("/", status_code=303)

    currencies = await get_user_currencies(session, user.id)
    primary = await get_primary_currency(session, user.id)
    if not currencies or primary is None:
        return RedirectResponse("/settings/currencies", status_code=303)

    rows = [
        {"name": "Bank account", "type": AccountType.ASSET.value, "currency": primary.code, "opening_balance": "0"},
        {"name": "Cash wallet", "type": AccountType.CASH.value, "currency": primary.code, "opening_balance": "0"},
        {"name": "Savings", "type": AccountType.ASSET.value, "currency": primary.code, "opening_balance": "0"},
    ]

    return render(
        request,
        "setup_accounts.html",
        {
            "user": user,
            "currencies": currencies,
            "rows": rows,
            "account_types": ONBOARDING_ACCOUNT_TYPES,
        },
    )


@router.post("/setup/accounts", response_model=None)
async def setup_accounts_submit(request: Request, session: SessionDep) -> HTMLResponse | RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    form = await request.form()
    names = [str(value).strip() for value in form.getlist("account_name")]
    types = [str(value) for value in form.getlist("account_type")]
    currency_codes = [str(value) for value in form.getlist("account_currency")]
    openings = [str(value) for value in form.getlist("account_opening_balance")]

    currencies = await get_user_currencies(session, user.id)
    allowed_currencies = {currency.code for currency in currencies}
    allowed_types = {value for value, _ in ONBOARDING_ACCOUNT_TYPES}
    rows: list[dict[str, str]] = []
    validated: list[dict] = []
    errors: list[str] = []

    for index, name in enumerate(names):
        row = {
            "name": name,
            "type": types[index] if index < len(types) else AccountType.ASSET.value,
            "currency": currency_codes[index] if index < len(currency_codes) else "",
            "opening_balance": openings[index] if index < len(openings) else "0",
        }
        rows.append(row)

        if not name:
            continue
        if row["type"] not in allowed_types:
            errors.append(f"{name}: choose a valid account type.")
            continue
        if row["currency"] not in allowed_currencies:
            errors.append(f"{name}: choose one of your enabled currencies.")
            continue
        opening_balance = parse_decimal(row["opening_balance"])
        if opening_balance is None:
            errors.append(f"{name}: opening balance is not a valid number.")
            continue

        validated.append(
            {
                "account_type": AccountType(row["type"]),
                "name": name,
                "currency_code": row["currency"],
                "opening_balance": opening_balance,
            }
        )

    if not validated and not errors:
        errors.append("Add at least one account.")

    if errors:
        return render(
            request,
            "setup_accounts.html",
            {
                "user": user,
                "currencies": currencies,
                "rows": rows,
                "account_types": ONBOARDING_ACCOUNT_TYPES,
                "error": " ".join(errors),
            },
            status_code=400,
        )

    for entry in validated:
        await create_account(session, user, **entry)
    await session.commit()

    flash(request, "Accounts created. Welcome to GlowBug.", "success")
    return RedirectResponse("/", status_code=303)


@router.get("/login", response_class=HTMLResponse, response_model=None)
async def login_page(request: Request, session: SessionDep) -> HTMLResponse | RedirectResponse:
    if await get_session_user(request, session) is not None:
        return RedirectResponse("/", status_code=303)

    return render(request, "login.html")


@router.post("/login", response_model=None)
async def login_submit(request: Request, session: SessionDep) -> HTMLResponse | RedirectResponse:
    form = await request.form()
    email = str(form.get("email", "")).strip().lower()
    password = str(form.get("password", ""))

    user = await session.scalar(select(User).where(User.email == email))
    if user is None or not verify_password(password, user.password_hash):
        return render(
            request,
            "login.html",
            {"error": "Email or password is incorrect.", "email": email},
            status_code=401,
        )

    login_session(request, user)
    flash(request, f"Welcome back, {user.display_name}.", "success")
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
async def logout(request: Request) -> RedirectResponse:
    logout_session(request)
    return RedirectResponse("/login", status_code=303)


@router.get("/", response_class=HTMLResponse, response_model=None)
async def dashboard(request: Request, session: SessionDep) -> HTMLResponse | RedirectResponse:
    if await user_count(session) == 0:
        return RedirectResponse("/setup", status_code=303)

    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    if not await has_accounts(session, user.id):
        return RedirectResponse("/setup/accounts", status_code=303)

    dashboard = await get_or_create_default_dashboard(session, user)
    range_key = request.query_params.get("range", "6M").upper()
    if range_key not in VALID_RANGES:
        range_key = "6M"
    start, end = default_range(user, range_key)

    return render(
        request,
        "dashboard.html",
        {
            "user": user,
            "dashboard": serialize_dashboard(dashboard),
            "catalog": WIDGET_CATALOG,
            "range_key": range_key,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "primary_currency": await get_primary_currency(session, user.id),
            "secondary_currency": await get_secondary_currency(session, user.id),
            "last_fx_sync": await get_last_sync(session),
        },
    )


@router.get("/accounts", response_class=HTMLResponse, response_model=None)
async def accounts_index(request: Request, session: SessionDep) -> HTMLResponse | RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    if not await has_accounts(session, user.id):
        return RedirectResponse("/setup/accounts", status_code=303)

    data = await build_dashboard(session, user)
    return render(
        request,
        "accounts/index.html",
        {"user": user, "data": data, "last_fx_sync": await get_last_sync(session)},
    )


@router.get("/accounts/new", response_class=HTMLResponse, response_model=None)
async def account_create_page(request: Request, session: SessionDep) -> HTMLResponse | RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    currencies = await get_user_currencies(session, user.id)
    primary = await get_primary_currency(session, user.id)

    return render(
        request,
        "accounts/create.html",
        {
            "user": user,
            "currencies": currencies,
            "account_types": ALL_ACCOUNT_TYPES,
            "primary_code": primary.code if primary else "",
            "values": {},
        },
    )


@router.post("/accounts", response_model=None)
async def account_create_submit(request: Request, session: SessionDep) -> HTMLResponse | RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    form = await request.form()
    name = str(form.get("name", "")).strip()
    account_type_value = str(form.get("type", AccountType.ASSET.value))
    currency_code = str(form.get("currency", "")).upper()
    opening_balance = parse_decimal(str(form.get("opening_balance", "0")))
    values = {
        "name": name,
        "type": account_type_value,
        "currency": currency_code,
        "opening_balance": str(form.get("opening_balance", "0")),
    }
    errors: list[str] = []

    allowed_types = {value for value, _ in ALL_ACCOUNT_TYPES}
    if not name:
        errors.append("Enter an account name.")
    if account_type_value not in allowed_types:
        errors.append("Choose a valid account type.")
    if opening_balance is None:
        errors.append("Opening balance is not a valid number.")

    currencies = await get_user_currencies(session, user.id)
    if currency_code not in {currency.code for currency in currencies}:
        errors.append("Choose one of your enabled currencies.")

    if errors:
        return render(
            request,
            "accounts/create.html",
            {
                "user": user,
                "currencies": currencies,
                "account_types": ALL_ACCOUNT_TYPES,
                "values": values,
                "error": " ".join(errors),
            },
            status_code=400,
        )

    await create_account(
        session,
        user,
        account_type=AccountType(account_type_value),
        name=name,
        currency_code=currency_code,
        opening_balance=opening_balance or Decimal("0"),
        opening_balance_date=date.today(),
    )
    await session.commit()

    flash(request, f"Account “{name}” created.", "success")
    return RedirectResponse("/accounts", status_code=303)


@router.get("/settings/currencies", response_class=HTMLResponse, response_model=None)
async def currency_settings_page(request: Request, session: SessionDep) -> HTMLResponse | RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    return render(request, "settings/currencies.html", await currency_settings_context(session, user))


@router.post("/settings/currencies", response_model=None)
async def currency_settings_submit(request: Request, session: SessionDep) -> HTMLResponse | RedirectResponse:
    user = await get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    form = await request.form()
    enabled_codes = [str(value).upper() for value in form.getlist("enabled_currencies")]
    primary_code = str(form.get("primary_currency", "")).upper()
    secondary_code = str(form.get("secondary_currency", "")).upper() or None

    if not primary_code:
        context = await currency_settings_context(session, user)
        context.update(
            {
                "error": "Choose a primary currency.",
                "enabled_codes": set(enabled_codes),
                "primary_code": primary_code,
                "secondary_code": secondary_code or "",
            }
        )
        return render(request, "settings/currencies.html", context, status_code=400)

    try:
        await set_user_currencies(
            session,
            user,
            enabled_codes=enabled_codes,
            primary_code=primary_code,
            secondary_code=secondary_code,
        )
        await session.commit()
    except ValueError as exc:
        await session.rollback()
        context = await currency_settings_context(session, user)
        context.update(
            {
                "error": str(exc),
                "enabled_codes": set(enabled_codes),
                "primary_code": primary_code,
                "secondary_code": secondary_code or "",
            }
        )
        return render(request, "settings/currencies.html", context, status_code=400)

    flash(request, "Currency settings saved.", "success")
    return RedirectResponse("/settings/currencies", status_code=303)
