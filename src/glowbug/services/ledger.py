from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from glowbug.enums import JournalType
from glowbug.models.account import Account
from glowbug.models.transaction import Transaction, TransactionJournal
from glowbug.models.user import User


class LedgerError(ValueError):
    """Raised when a journal would break the double-entry ledger."""


@dataclass(frozen=True)
class Split:
    account_id: UUID
    amount: Decimal


async def post_journal(
    session: AsyncSession,
    user: User,
    *,
    journal_type: JournalType,
    journal_date: date,
    description: str,
    currency_code: str,
    splits: list[Split],
    notes: str | None = None,
) -> TransactionJournal:
    if len(splits) < 2:
        raise LedgerError("A journal needs at least two splits.")

    total = sum((split.amount for split in splits), Decimal("0"))
    if total != 0:
        raise LedgerError(f"Splits must balance to zero; got {total}.")

    account_ids = {split.account_id for split in splits}
    accounts = list(
        await session.scalars(
            select(Account).where(
                Account.id.in_(account_ids),
                Account.user_id == user.id,
            )
        )
    )
    if len(accounts) != len(account_ids):
        raise LedgerError("Every split must reference an account owned by this user.")

    for account in accounts:
        if account.currency_code != currency_code:
            raise LedgerError(
                f"Account {account.name} uses {account.currency_code}, but the journal is in {currency_code}."
            )

    journal = TransactionJournal(
        user_id=user.id,
        type=journal_type.value,
        date=journal_date,
        description=description.strip(),
        currency_code=currency_code,
        notes=notes,
    )
    session.add(journal)
    await session.flush()

    for split in splits:
        session.add(
            Transaction(
                journal_id=journal.id,
                account_id=split.account_id,
                amount=split.amount,
                currency_code=currency_code,
            )
        )

    await session.flush()
    return journal
