from __future__ import annotations

import calendar
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from glowbug.enums import AccountType, JournalType
from glowbug.models.account import Account
from glowbug.models.category import Category
from glowbug.models.subscription import Subscription
from glowbug.models.transaction import Transaction, TransactionJournal
from glowbug.models.user import User
from glowbug.services.categorizer import normalize_merchant

FREQUENCIES = [
    ("weekly", "Weekly"),
    ("monthly", "Monthly"),
    ("quarterly", "Quarterly"),
    ("yearly", "Yearly"),
]
FREQUENCY_VALUES = {value for value, _ in FREQUENCIES}

ASSET_TYPES = {AccountType.ASSET.value, AccountType.CASH.value}


@dataclass(frozen=True)
class SubscriptionCandidate:
    name: str
    merchant_key: str
    amount: Decimal
    currency_code: str
    frequency: str
    next_due_date: date
    account_id: UUID | None
    category_id: UUID | None
    occurrences: int
    last_date: date


def add_months(start: date, months: int) -> date:
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def advance_date(start: date, frequency: str, count: int = 1) -> date:
    if frequency == "weekly":
        return start + timedelta(days=7 * count)
    if frequency == "monthly":
        return add_months(start, count)
    if frequency == "quarterly":
        return add_months(start, 3 * count)
    if frequency == "yearly":
        return add_months(start, 12 * count)
    raise ValueError("Unknown subscription frequency.")


def frequency_from_interval(days: int) -> str | None:
    if 5 <= days <= 9:
        return "weekly"
    if 26 <= days <= 35:
        return "monthly"
    if 80 <= days <= 100:
        return "quarterly"
    if 350 <= days <= 380:
        return "yearly"
    return None


def _median(values: list[Decimal | int]) -> Decimal | int:
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


async def detect_candidates(
    session: AsyncSession,
    user: User,
    *,
    min_occurrences: int = 3,
) -> list[SubscriptionCandidate]:
    statement = (
        select(TransactionJournal)
        .where(
            TransactionJournal.user_id == user.id,
            TransactionJournal.type == JournalType.WITHDRAWAL.value,
        )
        .options(
            selectinload(TransactionJournal.category),
            selectinload(TransactionJournal.transactions).selectinload(Transaction.account),
        )
        .order_by(TransactionJournal.date)
    )
    journals = list(await session.scalars(statement))
    groups: defaultdict[str, list[dict]] = defaultdict(list)

    for journal in journals:
        key = normalize_merchant(journal.description)
        if not key:
            continue
        amount = max((abs(transaction.amount) for transaction in journal.transactions), default=Decimal("0"))
        if amount <= 0:
            continue
        asset_account = next(
            (
                transaction.account
                for transaction in journal.transactions
                if transaction.account is not None and transaction.account.type in ASSET_TYPES
            ),
            None,
        )
        groups[key].append(
            {
                "date": journal.date,
                "description": journal.description.strip(),
                "amount": amount,
                "currency": journal.currency_code,
                "account_id": asset_account.id if asset_account is not None else None,
                "category_id": journal.category_id,
            }
        )

    existing = set(
        await session.scalars(
            select(Subscription.merchant_key).where(
                Subscription.user_id == user.id,
                Subscription.merchant_key.is_not(None),
            )
        )
    )

    candidates: list[SubscriptionCandidate] = []
    for key, entries in groups.items():
        if len(entries) < min_occurrences or key in existing:
            continue
        entries.sort(key=lambda entry: entry["date"])
        currencies = {entry["currency"] for entry in entries}
        if len(currencies) != 1:
            continue

        median_amount = _median([entry["amount"] for entry in entries])
        if not isinstance(median_amount, Decimal) or median_amount <= 0:
            continue
        tolerance = median_amount * Decimal("0.10")
        if any(abs(entry["amount"] - median_amount) > tolerance for entry in entries):
            continue

        intervals = [(entries[index + 1]["date"] - entries[index]["date"]).days for index in range(len(entries) - 1)]
        median_interval = _median(intervals)
        if not isinstance(median_interval, int):
            continue
        frequency = frequency_from_interval(median_interval)
        if frequency is None:
            continue

        descriptions = Counter(entry["description"] for entry in entries)
        accounts = Counter(entry["account_id"] for entry in entries if entry["account_id"] is not None)
        categories = Counter(entry["category_id"] for entry in entries if entry["category_id"] is not None)

        last_date = entries[-1]["date"]
        candidates.append(
            SubscriptionCandidate(
                name=descriptions.most_common(1)[0][0][:120],
                merchant_key=key,
                amount=median_amount,
                currency_code=entries[0]["currency"],
                frequency=frequency,
                next_due_date=advance_date(last_date, frequency),
                account_id=accounts.most_common(1)[0][0] if accounts else None,
                category_id=categories.most_common(1)[0][0] if categories else None,
                occurrences=len(entries),
                last_date=last_date,
            )
        )

    return sorted(candidates, key=lambda candidate: candidate.next_due_date)


async def list_subscriptions(session: AsyncSession, user: User) -> list[Subscription]:
    statement = (
        select(Subscription)
        .where(Subscription.user_id == user.id)
        .options(
            selectinload(Subscription.account),
            selectinload(Subscription.category),
        )
        .order_by(Subscription.active.desc(), Subscription.next_due_date)
    )
    return list(await session.scalars(statement))


async def get_subscription(
    session: AsyncSession,
    user: User,
    subscription_id: UUID,
) -> Subscription | None:
    return await session.scalar(
        select(Subscription).where(
            Subscription.id == subscription_id,
            Subscription.user_id == user.id,
        )
    )


async def create_subscription(
    session: AsyncSession,
    user: User,
    *,
    name: str,
    amount: Decimal,
    currency_code: str,
    frequency: str,
    next_due_date: date,
    account_id: UUID | None = None,
    category_id: UUID | None = None,
    merchant_key: str | None = None,
    source: str = "manual",
) -> tuple[Subscription | None, bool]:
    if frequency not in FREQUENCY_VALUES:
        raise ValueError("Choose a valid frequency.")
    if amount <= 0:
        raise ValueError("Amount must be greater than zero.")
    if not name.strip():
        raise ValueError("Enter a subscription name.")

    if merchant_key:
        existing = await session.scalar(
            select(Subscription).where(
                Subscription.user_id == user.id,
                Subscription.merchant_key == merchant_key,
                Subscription.amount == amount,
                Subscription.currency_code == currency_code,
                Subscription.frequency == frequency,
            )
        )
        if existing is not None:
            return existing, False

    if account_id is not None:
        account = await session.get(Account, account_id)
        if account is None or account.user_id != user.id:
            raise ValueError("Account not found.")
    if category_id is not None:
        category = await session.scalar(
            select(Category).where(
                Category.id == category_id,
                Category.user_id == user.id,
            )
        )
        if category is None:
            raise ValueError("Category not found.")

    subscription = Subscription(
        user_id=user.id,
        name=name.strip()[:120],
        merchant_key=merchant_key,
        amount=amount,
        currency_code=currency_code,
        frequency=frequency,
        next_due_date=next_due_date,
        account_id=account_id,
        category_id=category_id,
        active=True,
        source=source,
    )
    session.add(subscription)
    await session.flush()
    return subscription, True


async def update_subscription(
    session: AsyncSession,
    subscription: Subscription,
    *,
    name: str,
    amount: Decimal,
    currency_code: str,
    frequency: str,
    next_due_date: date,
    account_id: UUID | None,
    category_id: UUID | None,
    active: bool,
) -> Subscription:
    if frequency not in FREQUENCY_VALUES:
        raise ValueError("Choose a valid frequency.")
    if amount <= 0:
        raise ValueError("Amount must be greater than zero.")

    subscription.name = name.strip()[:120] or subscription.name
    subscription.amount = amount
    subscription.currency_code = currency_code
    subscription.frequency = frequency
    subscription.next_due_date = next_due_date
    subscription.account_id = account_id
    subscription.category_id = category_id
    subscription.active = active
    await session.flush()
    return subscription


async def delete_subscription(session: AsyncSession, subscription: Subscription) -> None:
    await session.delete(subscription)
    await session.flush()
