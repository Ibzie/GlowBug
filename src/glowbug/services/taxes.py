from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from glowbug.enums import JournalType
from glowbug.models.category import Category
from glowbug.models.currency import Currency
from glowbug.models.tax import TaxPayment, TaxProfile
from glowbug.models.transaction import Transaction, TransactionJournal
from glowbug.models.user import User
from glowbug.services.accounts import get_primary_currency
from glowbug.services.fx.exceptions import RateUnavailableError
from glowbug.services.fx.service import ExchangeRateService

TAX_QUANTUM = Decimal("0.01")


@dataclass
class TaxCategoryTotal:
    category: Category
    income: Decimal
    tax: Decimal


@dataclass
class TaxProfileSummary:
    profile: TaxProfile
    taxable_income: Decimal
    estimated_tax: Decimal
    paid: Decimal
    remaining: Decimal
    category_totals: list[TaxCategoryTotal] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class TaxSummary:
    year: int
    primary_currency: Currency | None
    profiles: list[TaxProfileSummary] = field(default_factory=list)
    taxable_income: Decimal = Decimal("0")
    estimated_tax: Decimal = Decimal("0")
    paid: Decimal = Decimal("0")
    remaining: Decimal = Decimal("0")
    total_income: Decimal = Decimal("0")
    warnings: list[str] = field(default_factory=list)


class ConversionCache:
    def __init__(self, session: AsyncSession) -> None:
        self.service = ExchangeRateService(session)
        self.cache: dict[tuple[str, str, date], Decimal | None] = {}
        self.warnings: set[str] = set()

    async def convert(
        self,
        amount: Decimal,
        from_code: str,
        to_code: str,
        on_date: date,
    ) -> Decimal | None:
        if from_code == to_code:
            return amount
        key = (from_code, to_code, on_date)
        if key not in self.cache:
            try:
                rate, _ = await self.service.get_rate(from_code, to_code, on_date)
                self.cache[key] = rate
            except RateUnavailableError:
                self.cache[key] = None
                self.warnings.add(f"No {from_code}/{to_code} rate for {on_date.isoformat()}.")
        rate = self.cache[key]
        return None if rate is None else amount * rate


def quantize_tax(amount: Decimal) -> Decimal:
    return amount.quantize(TAX_QUANTUM, rounding=ROUND_HALF_UP)


async def list_profiles(
    session: AsyncSession,
    user: User,
    year: int,
) -> list[TaxProfile]:
    statement = (
        select(TaxProfile)
        .where(
            TaxProfile.user_id == user.id,
            TaxProfile.tax_year == year,
        )
        .options(
            selectinload(TaxProfile.categories),
            selectinload(TaxProfile.payments),
        )
        .execution_options(populate_existing=True)
        .order_by(TaxProfile.name)
    )
    return list(await session.scalars(statement))


async def get_profile(
    session: AsyncSession,
    user: User,
    profile_id: UUID,
) -> TaxProfile | None:
    return await session.scalar(
        select(TaxProfile)
        .where(
            TaxProfile.id == profile_id,
            TaxProfile.user_id == user.id,
        )
        .options(
            selectinload(TaxProfile.categories),
            selectinload(TaxProfile.payments),
        )
        .execution_options(populate_existing=True)
    )


async def _resolve_categories(
    session: AsyncSession,
    user_id: UUID,
    category_ids: list[UUID],
) -> list[Category]:
    unique_ids = list(dict.fromkeys(category_ids))
    if not unique_ids:
        raise ValueError("Choose at least one taxable category.")
    categories = list(
        await session.scalars(
            select(Category).where(
                Category.user_id == user_id,
                Category.id.in_(unique_ids),
            )
        )
    )
    if len(categories) != len(unique_ids):
        raise ValueError("One or more categories were not found.")
    return categories


async def create_profile(
    session: AsyncSession,
    user: User,
    *,
    name: str,
    rate: Decimal,
    currency_code: str,
    tax_year: int,
    category_ids: list[UUID],
    is_active: bool = True,
) -> TaxProfile:
    cleaned = name.strip()
    if not cleaned:
        raise ValueError("Enter a tax profile name.")
    if rate < 0 or rate > 100:
        raise ValueError("Tax rate must be between 0 and 100.")
    if tax_year < 1900 or tax_year > 2200:
        raise ValueError("Enter a valid tax year.")

    existing = await session.scalar(
        select(TaxProfile).where(
            TaxProfile.user_id == user.id,
            TaxProfile.name == cleaned,
            TaxProfile.tax_year == tax_year,
        )
    )
    if existing is not None:
        raise ValueError("A tax profile with this name already exists for that year.")

    categories = await _resolve_categories(session, user.id, category_ids)
    profile = TaxProfile(
        user_id=user.id,
        name=cleaned,
        rate=rate,
        currency_code=currency_code,
        tax_year=tax_year,
        is_active=is_active,
    )
    profile.categories = categories
    session.add(profile)
    await session.flush()
    return profile


async def update_profile(
    session: AsyncSession,
    profile: TaxProfile,
    *,
    name: str,
    rate: Decimal,
    currency_code: str,
    tax_year: int,
    category_ids: list[UUID],
    is_active: bool,
) -> TaxProfile:
    cleaned = name.strip()
    if not cleaned:
        raise ValueError("Enter a tax profile name.")
    if rate < 0 or rate > 100:
        raise ValueError("Tax rate must be between 0 and 100.")
    if tax_year < 1900 or tax_year > 2200:
        raise ValueError("Enter a valid tax year.")

    existing = await session.scalar(
        select(TaxProfile).where(
            TaxProfile.user_id == profile.user_id,
            TaxProfile.name == cleaned,
            TaxProfile.tax_year == tax_year,
            TaxProfile.id != profile.id,
        )
    )
    if existing is not None:
        raise ValueError("A tax profile with this name already exists for that year.")

    categories = await _resolve_categories(session, profile.user_id, category_ids)
    profile.name = cleaned
    profile.rate = rate
    profile.currency_code = currency_code
    profile.tax_year = tax_year
    profile.is_active = is_active
    profile.categories = categories
    await session.flush()
    return profile


async def delete_profile(session: AsyncSession, profile: TaxProfile) -> None:
    await session.delete(profile)
    await session.flush()


async def record_payment(
    session: AsyncSession,
    user: User,
    profile: TaxProfile,
    *,
    amount: Decimal,
    paid_on: date,
    note: str | None,
) -> TaxPayment:
    if amount <= 0:
        raise ValueError("Payment amount must be greater than zero.")
    payment = TaxPayment(
        user_id=user.id,
        tax_profile_id=profile.id,
        amount=amount,
        currency_code=profile.currency_code,
        paid_on=paid_on,
        note=note,
    )
    session.add(payment)
    await session.flush()
    return payment


async def get_payment(
    session: AsyncSession,
    user: User,
    payment_id: UUID,
) -> TaxPayment | None:
    return await session.scalar(
        select(TaxPayment).where(
            TaxPayment.id == payment_id,
            TaxPayment.user_id == user.id,
        )
    )


async def delete_payment(session: AsyncSession, payment: TaxPayment) -> None:
    await session.delete(payment)
    await session.flush()


async def calculate_tax(session: AsyncSession, user: User, year: int) -> TaxSummary:
    primary = await get_primary_currency(session, user.id)
    start = date(year, 1, 1)
    end = date(year, 12, 31)
    summary = TaxSummary(year=year, primary_currency=primary)

    journals = list(
        await session.scalars(
            select(TransactionJournal)
            .where(
                TransactionJournal.user_id == user.id,
                TransactionJournal.type == JournalType.DEPOSIT.value,
                TransactionJournal.date >= start,
                TransactionJournal.date <= end,
            )
            .options(
                selectinload(TransactionJournal.category),
                selectinload(TransactionJournal.transactions).selectinload(Transaction.account),
            )
            .order_by(TransactionJournal.date)
        )
    )
    income_entries = []
    for journal in journals:
        amount = max((abs(transaction.amount) for transaction in journal.transactions), default=Decimal("0"))
        if amount <= 0:
            continue
        income_entries.append(
            {
                "date": journal.date,
                "amount": amount,
                "currency": journal.currency_code,
                "category": journal.category,
            }
        )

    profiles = await list_profiles(session, user, year)
    top_cache = ConversionCache(session)
    rate_date = date.today() if year >= date.today().year else end

    if primary is not None:
        for entry in income_entries:
            converted = await top_cache.convert(
                entry["amount"],
                entry["currency"],
                primary.code,
                entry["date"],
            )
            if converted is not None:
                summary.total_income += converted

    for profile in profiles:
        selected_ids = {category.id for category in profile.categories}
        income_by_category: dict[UUID, Decimal] = {}
        profile_warnings: set[str] = set()
        profile_cache = ConversionCache(session)

        for entry in income_entries:
            category = entry["category"]
            if category is None or category.id not in selected_ids:
                continue
            converted = await profile_cache.convert(
                entry["amount"],
                entry["currency"],
                profile.currency_code,
                entry["date"],
            )
            if converted is None:
                continue
            income_by_category[category.id] = income_by_category.get(category.id, Decimal("0")) + converted

        taxable = sum(income_by_category.values(), Decimal("0"))
        estimated = quantize_tax(taxable * profile.rate / 100)
        paid = sum((payment.amount for payment in profile.payments), Decimal("0"))
        remaining = quantize_tax(estimated - paid)

        category_totals = []
        for category in profile.categories:
            income = income_by_category.get(category.id, Decimal("0"))
            if income == 0:
                continue
            category_totals.append(
                TaxCategoryTotal(
                    category=category,
                    income=income,
                    tax=quantize_tax(income * profile.rate / 100),
                )
            )
        category_totals.sort(key=lambda item: item.income, reverse=True)

        if not selected_ids:
            profile_warnings.add("No taxable categories selected.")
        profile_warnings.update(profile_cache.warnings)

        summary.profiles.append(
            TaxProfileSummary(
                profile=profile,
                taxable_income=taxable,
                estimated_tax=estimated,
                paid=paid,
                remaining=remaining,
                category_totals=category_totals,
                warnings=sorted(profile_warnings),
            )
        )

        if primary is not None and profile.is_active:
            taxable_primary = await top_cache.convert(
                taxable,
                profile.currency_code,
                primary.code,
                rate_date,
            )
            estimated_primary = await top_cache.convert(
                estimated,
                profile.currency_code,
                primary.code,
                rate_date,
            )
            paid_primary = await top_cache.convert(
                paid,
                profile.currency_code,
                primary.code,
                rate_date,
            )
            if taxable_primary is not None:
                summary.taxable_income += taxable_primary
            if estimated_primary is not None:
                summary.estimated_tax += estimated_primary
            if paid_primary is not None:
                summary.paid += paid_primary

    summary.remaining = quantize_tax(summary.estimated_tax - summary.paid)
    summary.warnings = sorted(top_cache.warnings)
    return summary
