# SPDX-License-Identifier: GPL-3.0-only
"""Bounded private topic derivation. Only explicit pins are persisted."""

import hashlib
import json
import math
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import func, select, text

from .discovery import lock_sqlite_writes
from .models import Document, Insight, PinnedTopic, SavedItem, Source
from .versions import current_documents

CORPUS_LIMIT = 500
TEXT_LIMIT = 8000
INSIGHT_LIMIT = 500
PIN_LIMIT = 30
PIN_BOOST = 10.0
MAX_TERM_CHARS = 80
STOPWORDS = frozenset(
    """
a about above after again against all am an and any are as at be because been before being
below between both but by can could did do does doing down during each few for from further
had has have having he her here hers herself him himself his how i if in into is it its itself
just me more most my myself no nor not now of off on once only or other our ours ourselves
out over own same she should so some such than that the their theirs them themselves then
there these they this those through to too under until up very was we were what when where
which while who whom why will with would you your yours yourself yourselves also may might
""".split()
)


@dataclass(frozen=True)
class CorpusText:
    document_id: str
    text: str
    multiplier: int = 1


@dataclass(frozen=True)
class TopicWeight:
    term: str
    weight: float
    count: int


def words(value: str) -> tuple[str, ...]:
    """Linear Unicode scanning; combining marks stay attached to their base letter."""
    result, token = [], []
    normalized = unicodedata.normalize("NFKC", unicodedata.normalize("NFKC", value).lower())
    for char in normalized:
        if char.isalnum() or (token and unicodedata.category(char).startswith("M")):
            token.append(char)
        elif token:
            result.append("".join(token))
            token = []
    if token:
        result.append("".join(token))
    return tuple(result)


def terms(value: str) -> Counter:
    tokens = words(value)

    def valid(token):
        return 3 <= len(token) <= MAX_TERM_CHARS and token not in STOPWORDS

    found = Counter(token for token in tokens if valid(token))
    found.update(
        f"{a} {b}"
        for a, b in zip(tokens, tokens[1:])
        if valid(a) and valid(b) and len(a) + len(b) + 1 <= MAX_TERM_CHARS
    )
    return found


def normalize_pin(value: str) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= MAX_TERM_CHARS
        or any(unicodedata.category(char).startswith("C") for char in value)
    ):
        raise ValueError("Invalid pinned topic")
    tokens = words(value)
    normalized = " ".join(tokens)
    if not tokens or len(tokens) > 5 or not 3 <= len(normalized) <= MAX_TERM_CHARS:
        raise ValueError("Invalid pinned topic")
    return normalized


def load_corpus(db) -> tuple[CorpusText, ...]:
    """Project text slices in SQL so neither huge rows nor unbounded scans cross into Python."""
    documents = db.execute(
        select(
            Document.id,
            func.substr(Document.title, 1, 256),
            func.substr(Document.text, 1, TEXT_LIMIT),
            Source.kind,
        )
        .join(Source, Source.id == Document.source_id)
        .where(Document.id.in_(current_documents()))
        .order_by(Document.created_at.desc(), Document.id.desc())
        .limit(CORPUS_LIMIT)
    ).all()
    corpus = [CorpusText(row.id, f"{row[1]} {row[2]}") for row in documents if row.kind == "note"]
    ids = [row.id for row in documents]
    if ids:
        insights = db.execute(
            select(
                Insight.document_id,
                func.substr(Insight.title, 1, 256),
                func.substr(Insight.idea, 1, 1000),
                func.substr(Insight.explanation, 1, 1000),
                func.substr(Insight.why_it_matters, 1, 512),
                func.substr(Insight.evidence, 1, 1000),
                SavedItem.insight_id,
            )
            .outerjoin(SavedItem, SavedItem.insight_id == Insight.id)
            .where(
                Insight.document_id.in_(ids),
                Insight.document_id.in_(current_documents()),
                Insight.dismissed.is_(False),
            )
            .order_by(Insight.created_at.desc(), Insight.id.desc())
            .limit(INSIGHT_LIMIT)
        ).all()
        corpus.extend(
            CorpusText(row.document_id, " ".join(row[1:6]), 2 if row[6] else 1) for row in insights
        )
    return tuple(corpus)


def derive_topics(
    corpus: tuple[CorpusText, ...], pins: tuple[str, ...] = (), limit: int = 50
) -> tuple[TopicWeight, ...]:
    per_document = defaultdict(Counter)
    for row in corpus:
        extracted = terms(row.text)
        per_document[row.document_id].update(
            {key: count * row.multiplier for key, count in extracted.items()}
        )
    totals, frequency = Counter(), Counter()
    for counts in per_document.values():
        totals.update(counts)
        frequency.update(counts.keys())
    size = len(per_document)
    weights = {
        term: round((1 + math.log(count)) * (1 + math.log((1 + size) / (1 + frequency[term]))), 6)
        for term, count in totals.items()
    }
    for pin in pins:
        weights[pin] = round(weights.get(pin, 0) + PIN_BOOST, 6)
    result = (TopicWeight(term, weight, frequency[term]) for term, weight in weights.items())
    return tuple(sorted(result, key=lambda row: (-row.weight, -row.count, row.term))[:limit])


def pinned_topics(db):
    return tuple(db.scalars(select(PinnedTopic).order_by(PinnedTopic.created_at, PinnedTopic.id)))


def profile(db):
    corpus, pins = load_corpus(db), pinned_topics(db)
    topics = derive_topics(corpus, tuple(pin.term for pin in pins))
    # Include the corpus so a forgotten document invalidates old pagination even if its
    # remaining terms happen to have the same top weights. Only the digest leaves memory.
    signature = hashlib.sha256(
        json.dumps(
            [
                sorted((row.document_id, row.text, row.multiplier) for row in corpus),
                [(pin.id, pin.term) for pin in pins],
            ],
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return topics, pins, signature


def score_item(item, topics):
    title, snippet = words(item.title[:2048]), words(item.snippet[:500])
    weighted = []
    for topic in topics:
        phrase = tuple(topic.term.split())

        def contains(tokens):
            return any(
                tokens[i : i + len(phrase)] == phrase for i in range(len(tokens) - len(phrase) + 1)
            )

        weight = topic.weight * (3 * contains(title) + contains(snippet))
        if weight:
            weighted.append((topic.term, weight))
    weighted.sort(key=lambda row: (-row[1], row[0]))
    return round(sum(weight for _, weight in weighted), 6), [term for term, _ in weighted[:5]]


def pin_topic(db, term):
    lock_sqlite_writes(db)
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(746903122)"))
    existing = db.scalar(select(PinnedTopic).where(PinnedTopic.term == term))
    if existing:
        return existing
    if db.scalar(select(func.count()).select_from(PinnedTopic)) >= PIN_LIMIT:
        raise HTTPException(409, "Pinned topic limit reached")
    pin = PinnedTopic(term=term)
    db.add(pin)
    db.commit()
    return pin
