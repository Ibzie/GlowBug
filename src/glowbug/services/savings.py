from __future__ import annotations

import calendar
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from glowbug.models.savings import SavingsContribution, SavingsGoal
from glowbug.models.user import User
from glowbug.services.accounts import get_primary_currency, get_user_currencies
from glowbug.services.dashboard import build_dashboard
from glowbug.services.fx.exceptions import RateUnavailableError
from glowbug.services.fx.service import ExchangeRateService

GOAL_COLORS = [
    "#2f6fed",
    "#0f7b5f",
    "#b42318",
    "#8a5a00",
    "#7a5af8",
    "#0e7490",
    "#c11574",
    "#475467",
]
DEFAULT_GOAL_COLOR = GOAL_COLORS[0]
AVAILABLE_COLOR = "#94a3b8"
COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")


@dataclass
class GoalView:
    goal: SavingsGoal
    saved: Decimal
    remaining: Decimal
    progress: float
    next_contribution: date | None


@dataclass
class SavingsSummary:
    goals: list[GoalView] = field(default_factory=list)
    total_saved: Decimal = Decimal("0")
    total_target: Decimal = Decimal("0")
    monthly_commitment: Decimal = Decimal("0")
    currency_code: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class BalanceSection:
    label: str
    amount: Decimal
    color: str
    kind: str


@dataclass
class BalanceSections:
    sections: list[BalanceSection] = field(default_factory=list)
    total: Decimal = Decimal("0")
    saved: Decimal = Decimal("0")
    available: Decimal = Decimal("0")
    currency_code: str = ""
    complete: bool = True
    warnings: list[str] = field(default_factory=list)


def clamp_day(year: int, month: int, day: int) -> date:
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(max(day, 1), last_day))


def next_month(year: int, month: int) -> tuple[int, int]:
    if month == 12:
        return year + 1, 1
    return year, month + 1


def normalise_color(value: str | None) -> str:
    color = (value or "").strip()
    return color if COLOR_PATTERN.match(color) else DEFAULT_GOAL_COLOR


def _normalise_day(value: int) -> int:
    if value < 1 or value > 31:
        raise ValueError("Choose a day between 1 and 31.")
    return value


def _clean_name(value: str) -> str:
    name = value.strip()
    if not name:
        raise ValueError("Enter a name.")
    if len(name) > 120:
        raise ValueError("Keep the name under 120 characters.")
    return name


def _next_contribution_date(
    goal: SavingsGoal,
    today: date,
    periods: set[tuple[UUID, str]],
    saved: Decimal,
) -> date | None:
    if not goal.active:
        return None
    if goal.target_amount and goal.target_amount > 0 and saved >= goal.target_amount:
        return None

    year, month = today.year, today.month
    for _ in range(24):
        due = clamp_day(year, month, goal.contribution_day)
        period = f"{year:04d}-{month:02d}"
        if (
            due >= today
            and due >= goal.start_date
            and (goal.target_date is None or due <= goal.target_date)
            and (goal.id, period) not in periods
        ):
            return due
        year, month = next_month(year, month)
        if goal.target_date is not None and date(year, month, 1) > goal.target_date:
            return None
    return None


async def list_goal_views(
    session: AsyncSession,
    user: User,
    today: date,
    *,
    include_inactive: bool = True,
) -> list[GoalView]:
    statement = (
        select(SavingsGoal).where(SavingsGoal.user_id == user.id).order_by(SavingsGoal.created_at, SavingsGoal.name)
    )
    if not include_inactive:
        statement = statement.where(SavingsGoal.active.is_(True))
    goals = list(await session.scalars(statement))
    if not goals:
        return []

    contributions = list(
        await session.scalars(
            select(SavingsContribution).where(
                SavingsContribution.user_id == user.id,
                SavingsContribution.goal_id.in_([goal.id for goal in goals]),
            )
        )
    )
    saved: defaultdict[UUID, Decimal] = defaultdict(lambda: Decimal("0"))
    periods: set[tuple[UUID, str]] = set()
    for contribution in contributions:
        saved[contribution.goal_id] += contribution.amount
        if contribution.period:
            periods.add((contribution.goal_id, contribution.period))

    views: list[GoalView] = []
    for goal in goals:
        total = saved.get(goal.id, Decimal("0"))
        target = goal.target_amount or Decimal("0")
        remaining = max(target - total, Decimal("0")) if target > 0 else Decimal("0")
        progress = 0.0 if target <= 0 else min(100.0, float(total / target * 100))
        views.append(
            GoalView(
                goal=goal,
                saved=total,
                remaining=remaining,
                progress=progress,
                next_contribution=_next_contribution_date(goal, today, periods, total),
            )
        )
    return views


async def get_goal(session: AsyncSession, user: User, goal_id: UUID) -> SavingsGoal | None:
    return await session.scalar(
        select(SavingsGoal).where(
            SavingsGoal.id == goal_id,
            SavingsGoal.user_id == user.id,
        )
    )


async def _validate_currency(session: AsyncSession, user: User, currency_code: str) -> str:
    code = currency_code.strip().upper()
    currencies = {currency.code for currency in await get_user_currencies(session, user.id)}
    if code not in currencies:
        raise ValueError("Choose one of your enabled currencies.")
    return code


async def create_goal(
    session: AsyncSession,
    user: User,
    *,
    name: str,
    target_amount: Decimal,
    currency_code: str,
    monthly_contribution: Decimal,
    contribution_day: int,
    start_date: date,
    target_date: date | None,
    color: str | None,
    notes: str | None,
    active: bool = True,
) -> SavingsGoal:
    cleaned = _clean_name(name)
    if target_amount < 0 or monthly_contribution < 0:
        raise ValueError("Amounts cannot be negative.")
    if target_date is not None and target_date < start_date:
        raise ValueError("Target date cannot be before the start date.")

    existing = await session.scalar(
        select(SavingsGoal.id).where(
            SavingsGoal.user_id == user.id,
            SavingsGoal.name == cleaned,
        )
    )
    if existing is not None:
        raise ValueError("A savings goal with this name already exists.")

    goal = SavingsGoal(
        user_id=user.id,
        name=cleaned,
        target_amount=target_amount,
        currency_code=await _validate_currency(session, user, currency_code),
        monthly_contribution=monthly_contribution,
        contribution_day=_normalise_day(contribution_day),
        start_date=start_date,
        target_date=target_date,
        color=normalise_color(color),
        notes=(notes or "").strip() or None,
        active=active,
    )
    session.add(goal)
    await session.flush()
    return goal


async def update_goal(
    session: AsyncSession,
    user: User,
    goal: SavingsGoal,
    *,
    name: str,
    target_amount: Decimal,
    currency_code: str,
    monthly_contribution: Decimal,
    contribution_day: int,
    start_date: date,
    target_date: date | None,
    color: str | None,
    notes: str | None,
    active: bool,
) -> SavingsGoal:
    cleaned = _clean_name(name)
    if target_amount < 0 or monthly_contribution < 0:
        raise ValueError("Amounts cannot be negative.")
    if target_date is not None and target_date < start_date:
        raise ValueError("Target date cannot be before the start date.")

    existing = await session.scalar(
        select(SavingsGoal.id).where(
            SavingsGoal.user_id == user.id,
            SavingsGoal.name == cleaned,
            SavingsGoal.id != goal.id,
        )
    )
    if existing is not None:
        raise ValueError("A savings goal with this name already exists.")

    goal.name = cleaned
    goal.target_amount = target_amount
    goal.currency_code = await _validate_currency(session, user, currency_code)
    goal.monthly_contribution = monthly_contribution
    goal.contribution_day = _normalise_day(contribution_day)
    goal.start_date = start_date
    goal.target_date = target_date
    goal.color = normalise_color(color)
    goal.notes = (notes or "").strip() or None
    goal.active = active
    await session.flush()
    return goal


async def delete_goal(session: AsyncSession, goal: SavingsGoal) -> None:
    await session.delete(goal)
    await session.flush()


async def add_contribution(
    session: AsyncSession,
    user: User,
    goal: SavingsGoal,
    *,
    amount: Decimal,
    contributed_on: date,
    note: str | None = None,
    source: str = "manual",
) -> SavingsContribution:
    if amount <= 0:
        raise ValueError("Contribution must be greater than zero.")
    contribution = SavingsContribution(
        user_id=user.id,
        goal_id=goal.id,
        amount=amount,
        currency_code=goal.currency_code,
        contributed_on=contributed_on,
        source=source,
        note=(note or "").strip() or None,
    )
    session.add(contribution)
    await session.flush()
    return contribution


async def apply_due_contributions(
    session: AsyncSession,
    user: User,
    today: date,
) -> list[SavingsContribution]:
    """Earmark this month's scheduled contribution for every due goal."""
    goals = list(
        await session.scalars(
            select(SavingsGoal)
            .where(
                SavingsGoal.user_id == user.id,
                SavingsGoal.active.is_(True),
            )
            .order_by(SavingsGoal.created_at)
            .with_for_update()
        )
    )
    if not goals:
        return []

    contributions = list(
        await session.scalars(select(SavingsContribution).where(SavingsContribution.user_id == user.id))
    )
    saved: defaultdict[UUID, Decimal] = defaultdict(lambda: Decimal("0"))
    periods: set[tuple[UUID, str]] = set()
    for contribution in contributions:
        saved[contribution.goal_id] += contribution.amount
        if contribution.period:
            periods.add((contribution.goal_id, contribution.period))

    period = today.strftime("%Y-%m")
    created: list[SavingsContribution] = []
    for goal in goals:
        due = clamp_day(today.year, today.month, goal.contribution_day)
        if due > today or due < goal.start_date:
            continue
        if goal.target_date is not None and due > goal.target_date:
            continue
        if (goal.id, period) in periods:
            continue

        amount = goal.monthly_contribution or Decimal("0")
        if amount <= 0:
            continue
        if goal.target_amount and goal.target_amount > 0:
            remaining = goal.target_amount - saved[goal.id]
            if remaining <= 0:
                continue
            amount = min(amount, remaining)

        contribution = SavingsContribution(
            user_id=user.id,
            goal_id=goal.id,
            amount=amount,
            currency_code=goal.currency_code,
            contributed_on=due,
            source="auto",
            period=period,
        )
        session.add(contribution)
        created.append(contribution)
        saved[goal.id] += amount
        periods.add((goal.id, period))

    await session.flush()
    return created


async def savings_summary(session: AsyncSession, user: User, today: date) -> SavingsSummary:
    primary = await get_primary_currency(session, user.id)
    views = await list_goal_views(session, user, today)
    if primary is None:
        return SavingsSummary(goals=views, warnings=["No primary currency is configured."])

    converter = ExchangeRateService(session)
    summary = SavingsSummary(goals=views, currency_code=primary.code)
    for view in views:
        if not view.goal.active:
            continue
        rate = Decimal("1")
        if view.goal.currency_code != primary.code:
            try:
                rate, _ = await converter.get_rate(view.goal.currency_code, primary.code, today)
            except RateUnavailableError:
                summary.warnings.append(f"No {view.goal.currency_code}/{primary.code} rate for {today.isoformat()}.")
                continue
        summary.total_saved += view.saved * rate
        summary.total_target += (view.goal.target_amount or Decimal("0")) * rate
        summary.monthly_commitment += (view.goal.monthly_contribution or Decimal("0")) * rate
    return summary


async def balance_sections(session: AsyncSession, user: User, today: date) -> BalanceSections:
    """Split net worth into unallocated money plus one section per active goal."""
    primary = await get_primary_currency(session, user.id)
    if primary is None:
        return BalanceSections(warnings=["No primary currency is configured."])

    dashboard = await build_dashboard(session, user)
    views = await list_goal_views(session, user, today, include_inactive=False)
    converter = ExchangeRateService(session)

    sections = [BalanceSection("Unallocated", Decimal("0"), AVAILABLE_COLOR, "available")]
    warnings = list(dashboard.warnings)
    complete = dashboard.net_primary_complete
    saved_total = Decimal("0")

    for view in views:
        amount = view.saved
        if amount and view.goal.currency_code != primary.code:
            try:
                amount = await converter.convert(amount, view.goal.currency_code, primary.code, today)
            except RateUnavailableError:
                warnings.append(
                    f"{view.goal.name}: no {view.goal.currency_code}/{primary.code} rate for {today.isoformat()}."
                )
                complete = False
                continue
        saved_total += amount
        sections.append(BalanceSection(view.goal.name, amount, normalise_color(view.goal.color), "goal"))

    available = dashboard.net_primary - saved_total
    if available < 0:
        warnings.append("Savings goals are larger than your current balance; unallocated money is shown as zero.")
        complete = False
        available = Decimal("0")
    sections[0] = BalanceSection("Unallocated", available, AVAILABLE_COLOR, "available")

    return BalanceSections(
        sections=sections,
        total=dashboard.net_primary,
        saved=saved_total,
        available=available,
        currency_code=primary.code,
        complete=complete,
        warnings=warnings,
    )
