import hashlib
from datetime import timedelta

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .ai import ModelClient, ModelError, normalized
from .config import Settings
from .fetching import fetch_document, validate_url
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
from .notes import NOTE_KEY_PREFIX
from .versions import (
    MAX_NOTE_VERSIONS,
    NOTE_CONFLICT,
    NOTE_STALE,
    NOTE_VERSION_LIMIT,
    NoteConflict,
    NoteStale,
    NoteVersionLimit,
    current_documents,
    is_current,
    lock_current,
    lock_replaced,
    version_count,
)


class RowVanished(LookupError):
    """A row this request depended on was deleted (e.g. a note forgotten) while it ran."""


def enqueue_url(db: Session, url: str) -> Job:
    """Stage a validated URL job; the caller commits its entire transaction."""
    job = Job(url=validate_url(url))
    db.add(job)
    db.flush()
    return job


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


def source_fields(source: Source) -> dict:
    """Client-facing source attribution. A note's internal key is never exposed."""
    is_note = source.kind == "note"
    return {
        "source_url": "" if is_note else source.url,
        "source_kind": source.kind,
        "source_label": (source.label or "") if is_note else "",
    }


def serialize_insight(db: Session, insight: Insight) -> dict:
    document = db.get(Document, insight.document_id)
    source = document and db.get(Source, document.source_id)
    if document is None or source is None:
        raise RowVanished("Insight not found")  # Deleted while we were working.
    return {
        "id": insight.id,
        "title": insight.title,
        "idea": insight.idea,
        "explanation": insight.explanation,
        "why_it_matters": insight.why_it_matters,
        "source_title": document.title,
        **source_fields(source),
        "document_id": document.id,
        "source_version": document.version,
        "evidence": insight.evidence,
        "tags": insight.tags,
        "saved": db.get(SavedItem, insight.id) is not None,
        "coverage": document.generation_coverage,
        "source_truncated": document.extraction_truncated,
        "model": insight.model,
        "model_endpoint": insight.model_endpoint,
    }


def serialize_review(db: Session, item: ReviewItem) -> dict:
    insight = db.get(Insight, item.insight_id)
    if insight is None:
        raise RowVanished("Review not found")  # Deleted while we were working.
    return {
        "id": item.id,
        "insight_id": item.insight_id,
        "question": item.question,
        "due_at": item.due_at.isoformat(),
        "interval_days": item.interval_days,
        "insight_title": insight.title,
        "document_id": insight.document_id,
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
            .where(Insight.dismissed.is_(False), Insight.document_id.in_(current_documents()))
            .order_by(Insight.created_at.desc())
            .limit(10)
        )
    )
    # Cached ids drop out once dismissed, forgotten, or superseded by a newer note version.
    keep = set(
        db.scalars(
            select(Insight.id).where(
                Insight.id.in_(feed.insight_ids),
                Insight.dismissed.is_(False),
                Insight.document_id.in_(current_documents()),
            )
        )
    )
    valid_ids = [key for key in feed.insight_ids if key in keep]
    ids = list(dict.fromkeys(valid_ids + [item.id for item in candidates]))[:10]
    feed.insight_ids = ids
    db.commit()
    items = (db.get(Insight, key) for key in ids)
    result = []
    for item in items:
        if item is None or item.dismissed:
            continue
        try:
            result.append(serialize_insight(db, item))
        except RowVanished:
            continue  # Deleted while we were working.
    return result


def default_review_question(insight: Insight) -> str:
    return f"Explain in your own words: {insight.title}"


def ensure_review(db: Session, insight: Insight) -> ReviewItem:
    locked = db.execute(select(Insight.id).where(Insight.id == insight.id).with_for_update())
    if locked.first() is None:
        raise RowVanished("Insight not found")  # Deleted while we were working.
    item = db.scalar(select(ReviewItem).where(ReviewItem.insight_id == insight.id))
    if item is None:
        item = ReviewItem(insight_id=insight.id, question=default_review_question(insight))
        db.add(item)
        db.flush()
    return item


def tutor_chat(db: Session, insight: Insight, message: str, model: ModelClient) -> dict:
    if not is_current(db, insight.document_id):
        # Superseded note versions are not cited; same answer as a forgotten note, no writes.
        raise RowVanished("Insight not found")
    chunk = db.get(Chunk, insight.chunk_id)
    if chunk is None:
        raise RowVanished("Insight not found")  # Deleted while we were working.
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
    if not lock_current(db, insight.document_id):
        # An edit published a newer version (or a forget ran) during inference: no writes.
        raise RowVanished("Insight not found")
    review = ensure_review(db, insight)  # Locks the insight before any write, after the model calls.
    review.question = result.question
    if message:
        db.add(History(insight_id=insight.id, role="user", message=message))
    db.add(History(insight_id=insight.id, role="assistant", message=result.answer))
    db.add(Interaction(insight_id=insight.id, action="tutor"))
    document = db.get(Document, insight.document_id)
    source = document and db.get(Source, document.source_id)
    if source is None:
        raise RowVanished("Insight not found")  # Deleted while we were working.
    db.commit()
    return {
        "answer": result.answer,
        "question": result.question,
        "assessment": result.assessment,
        "review_id": review.id,
        "citations": [
            {
                "document_id": document.id,
                **source_fields(source),
                "source_version": document.version,
                "passage": result.evidence,
            }
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
    result = serialize_review(db, review)  # May raise RowVanished; nothing is committed yet.
    db.commit()
    return result


def claim_job(factory, lease_seconds: int) -> tuple[str, str | None, str] | None:
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


def _lease_holder(db: Session, job_id: str, token: str) -> Job | None:
    job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if job is None or job.status != "running" or job.lease_token != token:
        return None  # A reclaimed lease owns completion now.
    return job


def _complete(job: Job) -> None:
    job.status, job.error, job.lease_until, job.lease_token = "completed", None, None, None
    job.payload_title = job.payload_text = job.payload_label = None


def _upsert_source(db: Session, key: str, **fields) -> Source:
    source = db.scalar(select(Source).where(Source.url == key).with_for_update())
    if source is None:
        try:
            with db.begin_nested():
                source = Source(url=key, **fields)
                db.add(source)
                db.flush()
        except IntegrityError:
            source = db.scalar(select(Source).where(Source.url == key).with_for_update())
    return source


def _publish(
    db: Session, settings: Settings, source: Source, *, title, text, digest, texts, batch, truncated,
    on_existing: type[Exception] | None = None, replaces: Document | None = None,
) -> str:
    """Store the document and its insights; return the document id (existing one on a race).

    With on_existing set, raise it instead of returning an existing document: the caller holds
    row locks in an order that does not allow locking that document now.
    """
    existing = db.scalar(
        select(Document.id).where(Document.source_id == source.id, Document.content_hash == digest)
    )
    if existing is not None:
        if on_existing is not None:
            raise on_existing
        return existing
    document_id = ident()
    document = Document(
        id=document_id,
        note_id=document_id if replaces is None else (replaces.note_id or replaces.id),
        source_id=source.id,
        title=title,
        text=text,
        content_hash=digest,
        extraction_truncated=truncated,
        generation_coverage="excerpt",
        version=1 if replaces is None else replaces.version + 1,
        replaces_document_id=None if replaces is None else replaces.id,
    )
    if replaces is not None:
        replaces.superseded = True
    db.add(document)
    db.flush()
    chunks = [Chunk(document_id=document.id, position=i, text=part) for i, part in enumerate(texts)]
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
    return document.id


def _load_note(factory, job_id: str, token: str):
    """Return (title, text, label, replaces_document_id), or None if the lease was lost."""
    with factory() as db:
        job = db.scalar(select(Job).where(Job.id == job_id))
        if job is None or job.status != "running" or job.lease_token != token:
            return None
        if job.payload_text is None or job.payload_title is None:
            raise RuntimeError("Note payload is missing")
        return job.payload_title, job.payload_text, job.payload_label, job.replaces_document_id


def _note_imported(db: Session, key: str, digest: str) -> str | None:
    return db.scalar(
        select(Document.id)
        .join(Source, Source.id == Document.source_id)
        .where(Source.url == key, Document.content_hash == digest)
    )


def _published_by_same_edit(db: Session, replaces: str, key: str, digest: str) -> str | None:
    """The current version that an identical edit of ``replaces`` already published, if any.

    Called while holding the replaced version's lock, so no second document is locked: a
    whole-note forget must lock the replaced version too, and a version-only forget of a
    current version with predecessors is refused.
    """
    return db.scalar(
        select(Document.id)
        .join(Source, Source.id == Document.source_id)
        .where(
            Source.url == key,
            Document.content_hash == digest,
            Document.replaces_document_id == replaces,
            Document.superseded.is_(False),
        )
    )


class _DocumentAppeared(Exception):
    """A matching document was committed after the dedupe check; retry from the lock order."""


def _complete_from_existing(
    db: Session, job_id: str, token: str, key: str, digest: str, replaces: str | None
) -> bool:
    """Complete a note job from an already imported identical note; False means import afresh.

    The document is share-locked before the job so a concurrent forget (which holds the document
    first) either finishes before we look, or waits for us and then deletes the job we linked.
    An edit, or a match on a superseded version, raises NoteConflict instead: the text already
    exists as a document and cannot become a new version (or silently land on a hidden one).
    The exception is a repeated edit (a client retry): if the match is the version this same
    edit already published, the job completes on it, or fails stale once it is superseded.
    The match is the only document locked here, before the job, as on the dedupe path.
    """
    existing = _note_imported(db, key, digest)
    if existing is None:
        return False
    row = db.execute(
        select(Document.superseded, Document.replaces_document_id)
        .where(Document.id == existing)
        .with_for_update(read=True)
    ).first()
    if row is None:
        return False  # Forgotten while we waited for the lock: treat as a fresh import.
    superseded, existing_replaces = row
    if replaces is not None and existing_replaces != replaces:
        raise NoteConflict(NOTE_CONFLICT)
    if superseded:
        raise NoteStale(NOTE_STALE) if replaces is not None else NoteConflict(NOTE_CONFLICT)
    if (job := _lease_holder(db, job_id, token)) is not None:
        _complete(job)
        job.document_id = existing
    return True


def _process_note(factory, settings: Settings, claim, model) -> None:
    job_id, _, token = claim
    note = _load_note(factory, job_id, token)
    if note is None:
        return
    title, text, label, replaces = note
    digest = hashlib.sha256(text.encode()).hexdigest()
    key = NOTE_KEY_PREFIX + digest
    with factory.begin() as db:
        if _complete_from_existing(db, job_id, token, key, digest, replaces):
            return  # Same content: first import wins, no model call.
        if replaces is not None and not is_current(db, replaces):
            raise NoteStale(NOTE_STALE)  # Fail before spending a model call.
    texts = chunk_text(text)
    batch = (model or ModelClient(settings, factory)).insights(texts[:8])
    for _ in range(2):
        try:
            with factory.begin() as db:
                if _complete_from_existing(db, job_id, token, key, digest, replaces):
                    return  # Another worker imported it while the model ran.
                # Lock order: the replaced document, then the job, then the source.
                replaced = lock_replaced(db, replaces) if replaces is not None else None
                job = _lease_holder(db, job_id, token)
                if job is None:
                    return  # Cancelled (e.g. its note was forgotten) or lease lost.
                if replaces is not None and (replaced is None or replaced.superseded):
                    successor = replaced and _published_by_same_edit(db, replaces, key, digest)
                    if successor is None:
                        raise NoteStale(NOTE_STALE)
                    job.document_id = successor  # An identical edit won the race: idempotent.
                    _complete(job)
                    return
                if replaced is not None and version_count(db, replaced) >= MAX_NOTE_VERSIONS:
                    raise NoteVersionLimit(NOTE_VERSION_LIMIT)
                source = _upsert_source(db, key, kind="note", title=title, label=label)
                job.document_id = _publish(
                    db, settings, source, title=title, text=text, digest=digest, texts=texts,
                    batch=batch, truncated=False, on_existing=_DocumentAppeared,
                    replaces=replaced,
                )
                _complete(job)
            return
        except _DocumentAppeared:
            continue  # Rolled back: the next pass share-locks the document before the job.
    raise RuntimeError("Note import kept racing another import")


def process_job(
    factory,
    settings: Settings,
    claim: tuple[str, str | None, str],
    fetcher=fetch_document,
    model=None,
):
    job_id, url, token = claim
    if url is None:
        _process_note(factory, settings, claim, model)
        return
    article = fetcher(url, settings)
    texts = chunk_text(article.text)
    # Extraction stores the full bounded document; generation uses at most eight passages.
    batch = (model or ModelClient(settings, factory)).insights(texts[:8])
    with factory.begin() as db:
        job = _lease_holder(db, job_id, token)
        if job is None:
            return
        source = _upsert_source(db, article.url, title=article.title)
        digest = hashlib.sha256(article.text.encode()).hexdigest()
        _publish(
            db, settings, source, title=article.title, text=article.text, digest=digest,
            texts=texts, batch=batch, truncated=article.truncated,
        )
        _complete(job)
