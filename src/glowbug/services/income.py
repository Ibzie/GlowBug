from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from glowbug.enums import AccountType, JournalType
from glowbug.models.account import Account
from glowbug.models.category import Category
from glowbug.models.savings import IncomeSchedule
from glowbug.models.transaction import TransactionJournal
from glowbug.models.user import User
from glowbug.services.accounts import get_primary_currency
from glowbug.services.fx.exceptions import RateUnavailableError
from glowbug.services.fx.service import ExchangeRateService
from glowbug.services.ledger import Split, post_journal
from glowbug.services.savings import clamp_day, next_month
from glowbug.services.transactions import ensure_system_account

ASSET_TYPES = {AccountType.ASSET.value, AccountType.CASH.value}


@dataclass
class IncomeScheduleView:
    schedule: IncomeSchedule
    next_payment: date | None


@dataclass
class IncomeSummary:
    schedules: list[IncomeScheduleView] = field(default_factory=list)
    monthly_total: Decimal = Decimal("0")
    currency_code: str = ""
    warnings: list[str] = field(default_factory=list)


def next_payment_date(schedule: IncomeSchedule, today: date) -> date | None:
    if not schedule.active:
        return None

    year, month = today.year, today.month
    for _ in range(24):
        due = clamp_day(year, month, schedule.day_of_month)
        applied = schedule.last_applied_on is not None and schedule.last_applied_on >= due
        if (
            due >= today
            and due >= schedule.start_date
            and (schedule.end_date is None or due <= schedule.end_date)
            and not applied
        ):
            return due
        year, month = next_month(year, month)
        if schedule.end_date is not None and date(year, month, 1) > schedule.end_date:
            return None
    return None


async def list_schedule_views(session: AsyncSession, user: User, today: date) -> list[IncomeScheduleView]:
    schedules = list(
        await session.scalars(
            select(IncomeSchedule)
            .where(IncomeSchedule.user_id == user.id)
            .order_by(IncomeSchedule.created_at, IncomeSchedule.name)
        )
    )
    return [
        IncomeScheduleView(schedule=schedule, next_payment=next_payment_date(schedule, today)) for schedule in schedules
    ]


async def get_schedule(session: AsyncSession, user: User, schedule_id: UUID) -> IncomeSchedule | None:
    return await session.scalar(
        select(IncomeSchedule).where(
            IncomeSchedule.id == schedule_id,
            IncomeSchedule.user_id == user.id,
        )
    )


def _clean_name(value: str) -> str:
    name = value.strip()
    if not name:
        raise ValueError("Enter a name.")
    if len(name) > 120:
        raise ValueError("Keep the name under 120 characters.")
    return name


def _normalise_day(value: int) -> int:
    if value < 1 or value > 31:
        raise ValueError("Choose a day between 1 and 31.")
    return value


async def _resolve_account(session: AsyncSession, user: User, account_id: UUID) -> Account:
    account = await session.get(Account, account_id)
    if (
        account is None
        or account.user_id != user.id
        or account.is_system
        or account.archived
        or account.type not in ASSET_TYPES
    ):
        raise ValueError("Choose a valid asset or cash account.")
    return account


async def _resolve_category(
    session: AsyncSession,
    user: User,
    category_id: UUID | None,
) -> Category | None:
    if category_id is None:
        return None
    category = await session.scalar(
        select(Category).where(
            Category.id == category_id,
            Category.user_id == user.id,
        )
    )
    if category is None:
        raise ValueError("Category not found.")
    return category


async def create_schedule(
    session: AsyncSession,
    user: User,
    *,
    name: str,
    amount: Decimal,
    account_id: UUID,
    category_id: UUID | None,
    day_of_month: int,
    start_date: date,
    end_date: date | None,
    notes: str | None,
    active: bool = True,
) -> IncomeSchedule:
    cleaned = _clean_name(name)
    if amount <= 0:
        raise ValueError("Amount must be greater than zero.")
    if end_date is not None and end_date < start_date:
        raise ValueError("End date cannot be before the start date.")

    account = await _resolve_account(session, user, account_id)
    category = await _resolve_category(session, user, category_id)
    existing = await session.scalar(
        select(IncomeSchedule.id).where(
            IncomeSchedule.user_id == user.id,
            IncomeSchedule.name == cleaned,
        )
    )
    if existing is not None:
        raise ValueError("An income schedule with this name already exists.")

    schedule = IncomeSchedule(
        user_id=user.id,
        account_id=account.id,
        name=cleaned,
        amount=amount,
        currency_code=account.currency_code,
        category_id=category.id if category is not None else None,
        day_of_month=_normalise_day(day_of_month),
        start_date=start_date,
        end_date=end_date,
        notes=(notes or "").strip() or None,
        active=active,
    )
    session.add(schedule)
    await session.flush()
    return schedule


async def update_schedule(
    session: AsyncSession,
    user: User,
    schedule: IncomeSchedule,
    *,
    name: str,
    amount: Decimal,
    account_id: UUID,
    category_id: UUID | None,
    day_of_month: int,
    start_date: date,
    end_date: date | None,
    notes: str | None,
    active: bool,
) -> IncomeSchedule:
    cleaned = _clean_name(name)
    if amount <= 0:
        raise ValueError("Amount must be greater than zero.")
    if end_date is not None and end_date < start_date:
        raise ValueError("End date cannot be before the start date.")

    account = await _resolve_account(session, user, account_id)
    category = await _resolve_category(session, user, category_id)
    existing = await session.scalar(
        select(IncomeSchedule.id).where(
            IncomeSchedule.user_id == user.id,
            IncomeSchedule.name == cleaned,
            IncomeSchedule.id != schedule.id,
        )
    )
    if existing is not None:
        raise ValueError("An income schedule with this name already exists.")

    schedule.name = cleaned
    schedule.amount = amount
    schedule.account_id = account.id
    schedule.currency_code = account.currency_code
    schedule.category_id = category.id if category is not None else None
    schedule.day_of_month = _normalise_day(day_of_month)
    schedule.start_date = start_date
    schedule.end_date = end_date
    schedule.notes = (notes or "").strip() or None
    schedule.active = active
    await session.flush()
    return schedule


async def delete_schedule(session: AsyncSession, schedule: IncomeSchedule) -> None:
    await session.delete(schedule)
    await session.flush()


async def apply_due_income(
    session: AsyncSession,
    user: User,
    today: date,
) -> list[TransactionJournal]:
    """Post this month's scheduled income for every due schedule."""
    schedules = list(
        await session.scalars(
            select(IncomeSchedule)
            .where(
                IncomeSchedule.user_id == user.id,
                IncomeSchedule.active.is_(True),
            )
            .order_by(IncomeSchedule.created_at)
            .with_for_update()
        )
    )
    applied: list[TransactionJournal] = []
    for schedule in schedules:
        due = clamp_day(today.year, today.month, schedule.day_of_month)
        if due > today or due < schedule.start_date:
            continue
        if schedule.end_date is not None and due > schedule.end_date:
            continue
        if schedule.last_applied_on is not None and schedule.last_applied_on >= due:
            continue

        account = await session.get(Account, schedule.account_id)
        if (
            account is None
            or account.user_id != user.id
            or account.archived
            or account.is_system
            or account.type not in ASSET_TYPES
        ):
            continue

        income = await ensure_system_account(session, user, AccountType.REVENUE, account.currency_code)
        journal = await post_journal(
            session,
            user,
            journal_type=JournalType.DEPOSIT,
            journal_date=due,
            description=schedule.name,
            currency_code=account.currency_code,
            splits=[
                Split(account_id=income.id, amount=-schedule.amount),
                Split(account_id=account.id, amount=schedule.amount),
            ],
            notes=schedule.notes,
        )
        if schedule.category_id is not None:
            journal.category_id = schedule.category_id
            journal.category_source = "recurring"
        schedule.last_applied_on = due
        applied.append(journal)

    await session.flush()
    return applied


async def income_summary(session: AsyncSession, user: User, today: date) -> IncomeSummary:
    primary = await get_primary_currency(session, user.id)
    views = await list_schedule_views(session, user, today)
    if primary is None:
        return IncomeSummary(schedules=views, warnings=["No primary currency is configured."])

    converter = ExchangeRateService(session)
    summary = IncomeSummary(schedules=views, currency_code=primary.code)
    for view in views:
        if not view.schedule.active:
            continue
        if view.schedule.end_date is not None and view.schedule.end_date < today:
            continue
        amount = view.schedule.amount
        if view.schedule.currency_code != primary.code:
            try:
                amount = await converter.convert(amount, view.schedule.currency_code, primary.code, today)
            except RateUnavailableError:
                summary.warnings.append(
                    f"{view.schedule.name}: no {view.schedule.currency_code}/{primary.code} "
                    f"rate for {today.isoformat()}."
                )
                continue
        summary.monthly_total += amount
    return summary
