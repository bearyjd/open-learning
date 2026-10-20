import hashlib
from datetime import timedelta

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .ai import ModelClient, ModelError, normalized
from .config import Settings
from .fetching import fetch_document
from .models import (
    Chunk,
    DailyFeed,
    Document,
    History,
    Insight,
    Interaction,
    Job,
    ReviewHistory,
    ReviewItem,
    SavedItem,
    Source,
    ident,
    now,
)


def chunk_text(text: str, size: int = 2400) -> list[str]:
    chunks = []
    while text:
        end = min(len(text), size)
        if end < len(text):
            boundary = text.rfind(" ", size // 2, end)
            if boundary > 0:
                end = boundary
        chunks.append(text[:end].strip())
        text = text[end:].strip()
    return chunks


def serialize_insight(db: Session, insight: Insight) -> dict:
    document = db.get(Document, insight.document_id)
    source = db.get(Source, document.source_id)
    return {
        "id": insight.id,
        "title": insight.title,
        "idea": insight.idea,
        "explanation": insight.explanation,
        "why_it_matters": insight.why_it_matters,
        "source_title": document.title,
        "source_url": source.url,
        "document_id": document.id,
        "evidence": insight.evidence,
        "tags": insight.tags,
        "saved": db.get(SavedItem, insight.id) is not None,
        "coverage": document.generation_coverage,
        "source_truncated": document.extraction_truncated,
        "model": insight.model,
        "model_endpoint": insight.model_endpoint,
    }


def serialize_review(db: Session, item: ReviewItem) -> dict:
    return {
        "id": item.id,
        "insight_id": item.insight_id,
        "question": item.question,
        "due_at": item.due_at.isoformat(),
        "interval_days": item.interval_days,
        "insight_title": db.get(Insight, item.insight_id).title,
    }


def today_feed(db: Session) -> list[dict]:
    day = now().date().isoformat()
    feed = db.get(DailyFeed, day, with_for_update=True)
    if feed is None:
        try:
            with db.begin_nested():
                feed = DailyFeed(day=day, insight_ids=[])
                db.add(feed)
                db.flush()
        except IntegrityError:
            # Another first request created today's row; keep this transaction usable.
            feed = db.get(DailyFeed, day, with_for_update=True)
            if feed is None:
                raise
    candidates = list(
        db.scalars(
            select(Insight)
            .where(Insight.dismissed.is_(False))
            .order_by(Insight.created_at.desc())
            .limit(10)
        )
    )
    valid_ids = [
        key
        for key in feed.insight_ids
        if (item := db.get(Insight, key)) is not None and not item.dismissed
    ]
    ids = list(dict.fromkeys(valid_ids + [item.id for item in candidates]))[:10]
    feed.insight_ids = ids
    db.commit()
    return [
        serialize_insight(db, item)
        for key in ids
        if (item := db.get(Insight, key)) is not None and not item.dismissed
    ]


def default_review_question(insight: Insight) -> str:
    return f"Explain in your own words: {insight.title}"


def ensure_review(db: Session, insight: Insight) -> ReviewItem:
    db.execute(select(Insight.id).where(Insight.id == insight.id).with_for_update())
    item = db.scalar(select(ReviewItem).where(ReviewItem.insight_id == insight.id))
    if item is None:
        item = ReviewItem(insight_id=insight.id, question=default_review_question(insight))
        db.add(item)
        db.flush()
    return item


def tutor_chat(db: Session, insight: Insight, message: str, model: ModelClient) -> dict:
    chunk = db.get(Chunk, insight.chunk_id)
    history = list(
        db.scalars(
            select(History)
            .where(History.insight_id == insight.id)
            .order_by(History.created_at.desc())
            .limit(8)
        )
    )
    turns = [{"role": entry.role, "message": entry.message} for entry in reversed(history)]
    previous = db.scalar(select(ReviewItem.question).where(ReviewItem.insight_id == insight.id))
    result = model.tutor(chunk.text, insight.title, message, turns, previous_question=previous)
    # No row lock may be held across model calls, so the repeat check uses the question
    # read above. A concurrent writer between that read and the lock can slip through; the
    # worst case is one repeated question, which is preferable to a lock across a slow call.
    baseline = previous if previous is not None else default_review_question(insight)
    if normalized(baseline).casefold() == normalized(result.question).casefold():
        result = model.tutor(
            chunk.text,
            insight.title,
            message,
            turns,
            previous_question=baseline,
            avoid_repeat=True,
        )
        if normalized(baseline).casefold() == normalized(result.question).casefold():
            raise ModelError("Model repeated the previous question")
    review = ensure_review(db, insight)
    review.question = result.question
    if message:
        db.add(History(insight_id=insight.id, role="user", message=message))
    db.add(History(insight_id=insight.id, role="assistant", message=result.answer))
    db.add(Interaction(insight_id=insight.id, action="tutor"))
    document = db.get(Document, insight.document_id)
    source = db.get(Source, document.source_id)
    db.commit()
    return {
        "answer": result.answer,
        "question": result.question,
        "assessment": result.assessment,
        "review_id": review.id,
        "citations": [
            {"document_id": document.id, "source_url": source.url, "passage": result.evidence}
        ],
    }


def answer_review(db: Session, review: ReviewItem, rating: str) -> dict:
    intervals = {
        "forgotten": 10 / 1440,
        "difficult": max(1, review.interval_days * 1.2),
        "remembered": max(3, review.interval_days * 2),
        "easy": max(7, review.interval_days * 2.5),
    }
    review.interval_days = intervals[rating]
    review.due_at = now() + timedelta(days=review.interval_days)
    db.add(
        ReviewHistory(
            review_id=review.id,
            rating=rating,
            interval_days=review.interval_days,
            due_at=review.due_at,
        )
    )
    db.add(Interaction(insight_id=review.insight_id, action="review:" + rating))
    db.commit()
    return serialize_review(db, review)


def claim_job(factory, lease_seconds: int) -> tuple[str, str, str] | None:
    with factory.begin() as db:
        timestamp = now()
        job = db.scalar(
            select(Job)
            .where(
                or_(
                    Job.status == "queued",
                    (Job.status == "running") & (Job.lease_until < timestamp),
                )
            )
            .order_by(Job.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job is None:
            return None
        job.status, job.lease_token = "running", ident()
        job.error = None
        job.lease_until = timestamp + timedelta(seconds=lease_seconds)
        job.attempts += 1
        return job.id, job.url, job.lease_token


def process_job(
    factory, settings: Settings, claim: tuple[str, str, str], fetcher=fetch_document, model=None
):
    job_id, url, token = claim
    article = fetcher(url, settings)
    texts = chunk_text(article.text)
    # Extraction stores the full bounded document; generation uses at most eight passages.
    batch = (model or ModelClient(settings, factory)).insights(texts[:8])
    with factory.begin() as db:
        job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if job is None or job.status != "running" or job.lease_token != token:
            return  # A reclaimed lease owns completion now.
        source = db.scalar(select(Source).where(Source.url == article.url).with_for_update())
        if source is None:
            try:
                with db.begin_nested():
                    source = Source(url=article.url, title=article.title)
                    db.add(source)
                    db.flush()
            except IntegrityError:
                source = db.scalar(
                    select(Source).where(Source.url == article.url).with_for_update()
                )
        digest = hashlib.sha256(article.text.encode()).hexdigest()
        existing = db.scalar(
            select(Document).where(Document.source_id == source.id, Document.content_hash == digest)
        )
        if existing is None:
            document = Document(
                source_id=source.id,
                title=article.title,
                text=article.text,
                content_hash=digest,
                extraction_truncated=article.truncated,
                generation_coverage="excerpt",
            )
            db.add(document)
            db.flush()
            chunks = [
                Chunk(document_id=document.id, position=i, text=text)
                for i, text in enumerate(texts)
            ]
            db.add_all(chunks)
            db.flush()
            for item in batch.insights:
                db.add(
                    Insight(
                        document_id=document.id,
                        chunk_id=chunks[item.chunk_index].id,
                        title=item.title,
                        idea=item.idea,
                        explanation=item.explanation,
                        why_it_matters=item.why_it_matters,
                        evidence=item.evidence,
                        tags=item.tags,
                        model=settings.effective_ai_model,
                        model_endpoint=settings.effective_ai_endpoint,
                    )
                )
        job.status, job.error, job.lease_until, job.lease_token = "completed", None, None, None
