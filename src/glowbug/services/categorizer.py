from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal
from difflib import SequenceMatcher
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from glowbug.models.category import Category, MerchantMemory
from glowbug.models.transaction import TransactionJournal
from glowbug.models.user import User

NOISE_TOKENS = {
    "pos",
    "debit",
    "credit",
    "card",
    "purchase",
    "payment",
    "online",
    "txn",
    "ref",
    "auth",
    "visa",
    "mastercard",
    "contactless",
    "pending",
    "www",
    "com",
}

FUZZY_THRESHOLD = 0.86
MODEL_CONFIDENCE_THRESHOLD = 0.60
MODEL_MARGIN_THRESHOLD = 0.15


@dataclass(frozen=True)
class MerchantPrediction:
    category_id: UUID
    category_name: str
    confidence: float
    source: str
    needs_review: bool


@dataclass(frozen=True)
class LearnResult:
    memory: MerchantMemory
    updated_journals: int


def normalize_merchant(description: str) -> str:
    text = description.lower()
    cleaned = "".join(character if character.isalnum() or character.isspace() else " " for character in text)
    tokens: list[str] = []
    for token in cleaned.split():
        if token in NOISE_TOKENS:
            continue
        if token.isdigit() and len(token) >= 5:
            continue
        tokens.append(token)
    return " ".join(tokens)[:255].strip()


def _ngrams(key: str) -> list[str]:
    padded = f" {key} "
    grams: list[str] = []
    for size in range(2, 6):
        for index in range(len(padded) - size + 1):
            grams.append(padded[index : index + size])
    return grams


async def predict_category(
    session: AsyncSession,
    user: User,
    description: str,
) -> MerchantPrediction | None:
    key = normalize_merchant(description)
    if not key:
        return None

    exact = await session.scalar(
        select(MerchantMemory)
        .where(
            MerchantMemory.user_id == user.id,
            MerchantMemory.merchant_key == key,
            MerchantMemory.category_id.is_not(None),
        )
        .options(selectinload(MerchantMemory.category))
    )
    if exact is not None and exact.category_id is not None and exact.category is not None:
        return MerchantPrediction(
            category_id=exact.category_id,
            category_name=exact.category.name,
            confidence=1.0,
            source="memory",
            needs_review=False,
        )

    memories = list(
        await session.scalars(
            select(MerchantMemory)
            .where(
                MerchantMemory.user_id == user.id,
                MerchantMemory.category_id.is_not(None),
            )
            .options(selectinload(MerchantMemory.category))
        )
    )
    memories = [memory for memory in memories if memory.category is not None]
    if len(memories) < 2:
        return None

    best_memory: MerchantMemory | None = None
    best_ratio = 0.0
    for memory in memories:
        ratio = SequenceMatcher(None, key, memory.merchant_key).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_memory = memory

    if (
        best_memory is not None
        and best_memory.category_id is not None
        and best_memory.category is not None
        and best_ratio >= FUZZY_THRESHOLD
    ):
        return MerchantPrediction(
            category_id=best_memory.category_id,
            category_name=best_memory.category.name,
            confidence=best_ratio,
            source="memory",
            needs_review=best_ratio < 0.9,
        )

    category_examples: Counter[UUID] = Counter()
    ngram_counts: defaultdict[UUID, Counter[str]] = defaultdict(Counter)
    vocabulary: set[str] = set()
    category_names: dict[UUID, str] = {}

    for memory in memories:
        if memory.category_id is None or memory.category is None:
            continue
        category_examples[memory.category_id] += 1
        category_names[memory.category_id] = memory.category.name
        for gram in _ngrams(memory.merchant_key):
            ngram_counts[memory.category_id][gram] += 1
            vocabulary.add(gram)

    if len(category_examples) < 2:
        return None

    total_examples = sum(category_examples.values())
    vocabulary_size = max(1, len(vocabulary))
    alpha = 0.5
    grams = _ngrams(key)
    scores: dict[UUID, float] = {}

    for category_id, example_count in category_examples.items():
        total_ngrams = sum(ngram_counts[category_id].values())
        score = math.log(example_count / total_examples)
        denominator = total_ngrams + alpha * vocabulary_size
        for gram in grams:
            count = ngram_counts[category_id][gram]
            score += math.log((count + alpha) / denominator)
        scores[category_id] = score

    max_score = max(scores.values())
    exp_scores = {category_id: math.exp(score - max_score) for category_id, score in scores.items()}
    normalizer = sum(exp_scores.values())
    probabilities = {category_id: value / normalizer for category_id, value in exp_scores.items()}
    ranked = sorted(probabilities.items(), key=lambda item: item[1], reverse=True)
    best_category_id, best_probability = ranked[0]
    second_probability = ranked[1][1] if len(ranked) > 1 else 0.0

    return MerchantPrediction(
        category_id=best_category_id,
        category_name=category_names[best_category_id],
        confidence=best_probability,
        source="model",
        needs_review=best_probability < MODEL_CONFIDENCE_THRESHOLD
        or (best_probability - second_probability) < MODEL_MARGIN_THRESHOLD,
    )


async def learn_merchant(
    session: AsyncSession,
    user: User,
    description: str,
    category_id: UUID,
) -> LearnResult | None:
    key = normalize_merchant(description)
    if not key:
        return None

    category = await session.scalar(
        select(Category).where(
            Category.id == category_id,
            Category.user_id == user.id,
        )
    )
    if category is None:
        raise ValueError("Category not found.")

    memory = await session.scalar(
        select(MerchantMemory).where(
            MerchantMemory.user_id == user.id,
            MerchantMemory.merchant_key == key,
        )
    )
    if memory is None:
        memory = MerchantMemory(
            user_id=user.id,
            merchant_key=key,
            display_name=description.strip()[:255],
            category_id=category.id,
            source="user",
            hit_count=1,
        )
        session.add(memory)
    else:
        memory.category_id = category.id
        memory.display_name = description.strip()[:255]
        memory.source = "user"
        memory.hit_count += 1

    updated = 0
    journals = list(
        await session.scalars(
            select(TransactionJournal).where(
                TransactionJournal.user_id == user.id,
                TransactionJournal.description.is_not(None),
                TransactionJournal.category_id.is_(None),
            )
        )
    )
    for journal in journals:
        if normalize_merchant(journal.description) != key:
            continue
        journal.category_id = category.id
        journal.category_source = "memory"
        journal.category_confidence = Decimal("0.9000")
        updated += 1

    await session.flush()
    return LearnResult(memory=memory, updated_journals=updated)


async def assign_category(
    session: AsyncSession,
    user: User,
    journal: TransactionJournal,
    category_id: UUID,
    *,
    remember: bool = True,
) -> int:
    category = await session.scalar(
        select(Category).where(
            Category.id == category_id,
            Category.user_id == user.id,
        )
    )
    if category is None:
        raise ValueError("Category not found.")

    journal.category_id = category.id
    journal.category_source = "user"
    journal.category_confidence = Decimal("1.0000")

    updated = 0
    if remember and journal.description:
        result = await learn_merchant(session, user, journal.description, category_id)
        if result is not None:
            updated = result.updated_journals

    await session.flush()
    return updated
