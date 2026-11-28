from __future__ import annotations

import re
from collections import defaultdict
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from glowbug.models.category import Category
from glowbug.models.transaction import TransactionJournal
from glowbug.models.user import User

DEFAULT_CATEGORIES: list[tuple[str, str]] = [
    ("Bills", "#b42318"),
    ("Food & dining", "#f79009"),
    ("Groceries", "#0f7b5f"),
    ("Leisure", "#7a5af8"),
    ("Travel", "#0e7490"),
    ("Transport", "#2f6fed"),
    ("Health", "#d92d20"),
    ("Shopping", "#c11574"),
    ("Housing", "#8a5a00"),
    ("Utilities", "#475467"),
    ("Subscriptions", "#6941c6"),
    ("Education", "#027a48"),
    ("Gifts", "#ee46bc"),
    ("Taxes", "#344054"),
    ("Income", "#12b76a"),
    ("Transfer", "#667085"),
    ("Other", "#98a2b3"),
]

_SPACES = re.compile(r"\s+")


def normalize_category_name(name: str) -> str:
    return _SPACES.sub(" ", name.strip().lower())


async def seed_default_categories(session: AsyncSession, user: User) -> int:
    existing = set(await session.scalars(select(Category.normalized_name).where(Category.user_id == user.id)))
    added = 0
    for name, color in DEFAULT_CATEGORIES:
        normalized = normalize_category_name(name)
        if normalized in existing:
            continue
        session.add(
            Category(
                user_id=user.id,
                name=name,
                normalized_name=normalized,
                color=color,
                is_system=True,
            )
        )
        existing.add(normalized)
        added += 1
    await session.flush()
    return added


async def ensure_categories(session: AsyncSession, user: User) -> list[Category]:
    count = int(await session.scalar(select(func.count(Category.id)).where(Category.user_id == user.id)) or 0)
    if count == 0:
        await seed_default_categories(session, user)
        await session.commit()
    return await list_categories(session, user.id)


async def list_categories(session: AsyncSession, user_id: UUID) -> list[Category]:
    statement = select(Category).where(Category.user_id == user_id).order_by(Category.name)
    return list(await session.scalars(statement))


async def get_category(session: AsyncSession, user_id: UUID, category_id: UUID) -> Category | None:
    return await session.scalar(
        select(Category).where(
            Category.id == category_id,
            Category.user_id == user_id,
        )
    )


async def get_category_by_name(session: AsyncSession, user_id: UUID, name: str) -> Category | None:
    return await session.scalar(
        select(Category).where(
            Category.user_id == user_id,
            Category.normalized_name == normalize_category_name(name),
        )
    )


async def create_category(
    session: AsyncSession,
    user: User,
    name: str,
    color: str = "#2f6fed",
) -> Category:
    cleaned = name.strip()
    normalized = normalize_category_name(cleaned)
    if not normalized:
        raise ValueError("Category name cannot be empty.")
    if await get_category_by_name(session, user.id, cleaned) is not None:
        raise ValueError(f"Category “{cleaned}” already exists.")

    category = Category(
        user_id=user.id,
        name=cleaned,
        normalized_name=normalized,
        color=color.strip() or "#2f6fed",
        is_system=False,
    )
    session.add(category)
    await session.flush()
    return category


async def update_category(
    session: AsyncSession,
    category: Category,
    name: str,
    color: str,
) -> Category:
    cleaned = name.strip()
    normalized = normalize_category_name(cleaned)
    if not normalized:
        raise ValueError("Category name cannot be empty.")

    existing = await get_category_by_name(session, category.user_id, cleaned)
    if existing is not None and existing.id != category.id:
        raise ValueError(f"Category “{cleaned}” already exists.")

    category.name = cleaned
    category.normalized_name = normalized
    category.color = color.strip() or category.color
    await session.flush()
    return category


async def delete_category(session: AsyncSession, category: Category) -> None:
    await session.delete(category)
    await session.flush()


async def category_counts(session: AsyncSession, user_id: UUID) -> dict[UUID, int]:
    statement = (
        select(TransactionJournal.category_id, func.count(TransactionJournal.id))
        .where(
            TransactionJournal.user_id == user_id,
            TransactionJournal.category_id.is_not(None),
        )
        .group_by(TransactionJournal.category_id)
    )
    counts: defaultdict[UUID, int] = defaultdict(int)
    for category_id, count in (await session.execute(statement)).all():
        if category_id is None:
            continue
        counts[category_id] = int(count)
    return dict(counts)
