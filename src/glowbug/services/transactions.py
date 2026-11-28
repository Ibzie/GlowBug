from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from glowbug.enums import AccountType, JournalType
from glowbug.models.account import Account
from glowbug.models.category import Category
from glowbug.models.transaction import Transaction, TransactionJournal
from glowbug.models.user import User
from glowbug.services.categorizer import assign_category, learn_merchant
from glowbug.services.ledger import Split, post_journal

ASSET_TYPES = {AccountType.ASSET.value, AccountType.CASH.value}
SYSTEM_ACCOUNT_LABELS = {
    AccountType.EXPENSE: "Expenses",
    AccountType.REVENUE: "Income",
}

TRANSACTION_TYPES = [
    (JournalType.WITHDRAWAL.value, "Withdrawal / expense"),
    (JournalType.DEPOSIT.value, "Deposit / income"),
    (JournalType.TRANSFER.value, "Transfer"),
]


@dataclass
class JournalView:
    journal: TransactionJournal
    amount: Decimal
    currency_code: str
    account_name: str | None


async def ensure_system_account(
    session: AsyncSession,
    user: User,
    account_type: AccountType,
    currency_code: str,
) -> Account:
    account = await session.scalar(
        select(Account).where(
            Account.user_id == user.id,
            Account.type == account_type.value,
            Account.currency_code == currency_code,
            Account.is_system.is_(True),
        )
    )
    if account is not None:
        return account

    label = SYSTEM_ACCOUNT_LABELS[account_type]
    account = Account(
        user_id=user.id,
        type=account_type.value,
        name=f"{label} ({currency_code})",
        currency_code=currency_code,
        opening_balance=Decimal("0"),
        opening_balance_date=date.today(),
        include_in_net_worth=False,
        is_system=True,
    )
    session.add(account)
    await session.flush()
    return account


async def create_transaction(
    session: AsyncSession,
    user: User,
    *,
    transaction_type: str,
    journal_date: date,
    description: str,
    amount: Decimal,
    currency_code: str,
    account_id: UUID,
    destination_account_id: UUID | None = None,
    category_id: UUID | None = None,
    notes: str | None = None,
) -> TransactionJournal:
    if amount <= 0:
        raise ValueError("Amount must be greater than zero.")

    account = await session.get(Account, account_id)
    if account is None or account.user_id != user.id or account.is_system or account.type not in ASSET_TYPES:
        raise ValueError("Choose a valid asset or cash account.")

    category: Category | None = None
    if category_id is not None:
        category = await session.scalar(
            select(Category).where(
                Category.id == category_id,
                Category.user_id == user.id,
            )
        )
        if category is None:
            raise ValueError("Category not found.")

    if transaction_type == JournalType.WITHDRAWAL.value:
        expense = await ensure_system_account(session, user, AccountType.EXPENSE, currency_code)
        journal_type = JournalType.WITHDRAWAL
        splits = [
            Split(account_id=account.id, amount=-amount),
            Split(account_id=expense.id, amount=amount),
        ]
    elif transaction_type == JournalType.DEPOSIT.value:
        income = await ensure_system_account(session, user, AccountType.REVENUE, currency_code)
        journal_type = JournalType.DEPOSIT
        splits = [
            Split(account_id=income.id, amount=-amount),
            Split(account_id=account.id, amount=amount),
        ]
    elif transaction_type == JournalType.TRANSFER.value:
        if destination_account_id is None:
            raise ValueError("Choose a destination account for transfers.")
        destination = await session.get(Account, destination_account_id)
        if (
            destination is None
            or destination.user_id != user.id
            or destination.is_system
            or destination.type not in ASSET_TYPES
        ):
            raise ValueError("Choose a valid destination account.")
        if destination.currency_code != currency_code:
            raise ValueError("Transfers between different currencies are not supported yet.")
        journal_type = JournalType.TRANSFER
        splits = [
            Split(account_id=account.id, amount=-amount),
            Split(account_id=destination.id, amount=amount),
        ]
    else:
        raise ValueError("Unknown transaction type.")

    journal = await post_journal(
        session,
        user,
        journal_type=journal_type,
        journal_date=journal_date,
        description=description,
        currency_code=currency_code,
        splits=splits,
        notes=notes,
    )

    if category is not None:
        journal.category_id = category.id
        journal.category_source = "user"
        journal.category_confidence = Decimal("1.0000")
        await session.flush()
        if description.strip():
            await learn_merchant(session, user, description, category.id)

    await session.commit()
    return journal


async def list_transactions(
    session: AsyncSession,
    user: User,
    start: date,
    end: date,
    *,
    category_id: UUID | None = None,
    uncategorized: bool = False,
    limit: int = 500,
) -> list[JournalView]:
    statement = (
        select(TransactionJournal)
        .where(
            TransactionJournal.user_id == user.id,
            TransactionJournal.date >= start,
            TransactionJournal.date <= end,
        )
        .options(
            selectinload(TransactionJournal.category),
            selectinload(TransactionJournal.transactions).selectinload(Transaction.account),
        )
        .order_by(TransactionJournal.date.desc(), TransactionJournal.created_at.desc())
        .limit(limit)
    )
    if category_id is not None:
        statement = statement.where(TransactionJournal.category_id == category_id)
    if uncategorized:
        statement = statement.where(TransactionJournal.category_id.is_(None))

    journals = list(await session.scalars(statement))
    views: list[JournalView] = []
    for journal in journals:
        amount = max((abs(transaction.amount) for transaction in journal.transactions), default=Decimal("0"))
        account_name = next(
            (
                transaction.account.name
                for transaction in journal.transactions
                if transaction.account is not None and transaction.account.type in ASSET_TYPES
            ),
            None,
        )
        views.append(
            JournalView(
                journal=journal,
                amount=amount,
                currency_code=journal.currency_code,
                account_name=account_name,
            )
        )
    return views


async def get_journal(session: AsyncSession, user: User, journal_id: UUID) -> TransactionJournal | None:
    return await session.scalar(
        select(TransactionJournal)
        .where(
            TransactionJournal.id == journal_id,
            TransactionJournal.user_id == user.id,
        )
        .options(selectinload(TransactionJournal.transactions))
    )


async def assign_journal_category(
    session: AsyncSession,
    user: User,
    journal: TransactionJournal,
    category_id: UUID,
    *,
    remember: bool,
) -> int:
    updated = await assign_category(session, user, journal, category_id, remember=remember)
    await session.commit()
    return updated
