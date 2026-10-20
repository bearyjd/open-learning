"""Forgetting notes: listing, hard deletion, and bounded retention of note text in jobs.

Lock order (every writer must follow it): note jobs by id, the document, its insights by id,
then their review items by id. Responses and logs never carry note text.
"""

import hashlib
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from .config import Settings
from .models import (
    Chunk,
    Document,
    History,
    Insight,
    Interaction,
    Job,
    ReviewHistory,
    ReviewItem,
    SavedItem,
    Source,
    now,
)

NOTE_LIMIT = 500
PENDING_STATUSES = ("queued", "running")
CAPACITY_LOCK_KEY = 0x4F4C4E4F  # Arbitrary constant serialising note admission on PostgreSQL.
CAPACITY_MESSAGE = "Too many notes are waiting to be processed; try again shortly"
RETRY_AFTER_SECONDS = "30"
EXPIRED_MESSAGE = "Note text expired; submit it again"


def list_notes(db: Session, limit: int = NOTE_LIMIT, offset: int = 0) -> list[dict]:
    rows = db.execute(
        select(Document.id, Document.title, Source.label, Document.created_at)
        .join(Source, Source.id == Document.source_id)
        .where(Source.kind == "note")
        .order_by(Document.created_at.desc(), Document.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return [
        {"document_id": id_, "title": title, "label": label, "created_at": created_at}
        for id_, title, label, created_at in rows
    ]


def list_note_jobs(db: Session, limit: int, offset: int) -> list[dict]:
    """Identify note jobs without exposing their text, title, label or error."""
    rows = db.execute(
        select(Job.id, Job.status, Job.created_at)
        .where(Job.kind == "note")
        .order_by(Job.created_at.desc(), Job.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return [{"id": id_, "status": status, "created_at": created} for id_, status, created in rows]


def _matching_job_ids(db: Session, content_hash: str) -> list[str]:
    rows = db.execute(
        select(Job.id, Job.payload_text).where(Job.kind == "note", Job.payload_text.is_not(None))
    )
    return [
        job_id for job_id, text in rows if hashlib.sha256(text.encode()).hexdigest() == content_hash
    ]


def _cancel_matching_jobs(db: Session, content_hash: str) -> None:
    ids = _matching_job_ids(db, content_hash)
    if not ids:
        return
    jobs = db.scalars(select(Job).where(Job.id.in_(ids)).order_by(Job.id).with_for_update())
    for job in jobs:
        if job.payload_text is None:
            continue  # Completed or purged while we waited for the lock.
        if hashlib.sha256(job.payload_text.encode()).hexdigest() != content_hash:
            continue
        job.status, job.error = "cancelled", "Note deleted"
        job.payload_title = job.payload_text = job.payload_label = None
        job.lease_token = job.lease_until = None


def _lock_graph(db: Session, document_id: str) -> None:
    insight_ids = select(Insight.id).where(Insight.document_id == document_id)
    db.execute(
        select(Insight.id)
        .where(Insight.document_id == document_id)
        .order_by(Insight.id)
        .with_for_update()
    ).all()
    db.execute(
        select(ReviewItem.id)
        .where(ReviewItem.insight_id.in_(insight_ids))
        .order_by(ReviewItem.id)
        .with_for_update()
    ).all()


def _delete_graph(db: Session, document_id: str, source_id: str) -> None:
    insights = select(Insight.id).where(Insight.document_id == document_id)
    reviews = select(ReviewItem.id).where(ReviewItem.insight_id.in_(insights))
    db.execute(delete(ReviewHistory).where(ReviewHistory.review_id.in_(reviews)))
    db.execute(delete(ReviewItem).where(ReviewItem.insight_id.in_(insights)))
    for model in (History, Interaction, SavedItem):
        db.execute(delete(model).where(model.insight_id.in_(insights)))
    db.execute(delete(Insight).where(Insight.document_id == document_id))
    db.execute(delete(Chunk).where(Chunk.document_id == document_id))
    db.execute(delete(Document).where(Document.id == document_id))
    db.execute(delete(Source).where(Source.id == source_id))


def forget_note(db: Session, document_id: str) -> None:
    """Hard-delete a note document and everything derived from it in one transaction."""
    found = db.execute(
        select(Document.content_hash)
        .join(Source, Source.id == Document.source_id)
        .where(Document.id == document_id, Source.kind == "note")
    ).first()
    if found is None:
        raise HTTPException(404, "Note not found")
    _cancel_matching_jobs(db, found[0])
    locked = db.execute(
        select(Document.source_id)
        .join(Source, Source.id == Document.source_id)
        .where(Document.id == document_id, Source.kind == "note")
        .with_for_update(of=Document)
    ).first()
    if locked is None:
        db.rollback()
        raise HTTPException(404, "Note not found")
    _lock_graph(db, document_id)
    _delete_graph(db, document_id, locked[0])
    db.commit()


def forget_note_job(db: Session, job_id: str) -> None:
    job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if job is None:
        raise HTTPException(404, "Job not found")
    if job.kind != "note":
        raise HTTPException(409, "Only note jobs can be deleted")
    db.delete(job)
    db.commit()


def purge_note_payloads(db: Session, settings: Settings) -> int:
    """Clear retained text of failed note jobs that are too old or beyond the newest N."""
    cutoff = now() - timedelta(hours=settings.note_failed_payload_hours)
    rows = db.execute(
        select(Job.id, Job.created_at)
        .where(Job.kind == "note", Job.status == "failed", Job.payload_text.is_not(None))
        .order_by(Job.created_at.desc(), Job.id.desc())
    ).all()
    stale = [
        job_id
        for index, (job_id, created_at) in enumerate(rows)
        if index >= settings.note_max_failed_payloads or _aged(created_at, cutoff)
    ]
    if not stale:
        return 0
    locked = db.scalars(
        select(Job.id)
        .where(Job.id.in_(stale), Job.status == "failed")
        .order_by(Job.id)
        .with_for_update(skip_locked=True)
    ).all()
    db.execute(
        update(Job)
        .where(Job.id.in_(locked))
        .values(payload_title=None, payload_text=None, payload_label=None, error=EXPIRED_MESSAGE)
    )
    return len(locked)


def _aged(created_at, cutoff) -> bool:
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=cutoff.tzinfo)
    return created_at < cutoff


def note_capacity_check(db: Session, settings: Settings) -> None:
    """Raise 429 when too many note jobs are queued or running."""
    if db.get_bind().dialect.name == "postgresql":
        db.execute(select(func.pg_advisory_xact_lock(CAPACITY_LOCK_KEY)))
    pending = db.scalar(
        select(func.count())
        .select_from(Job)
        .where(Job.kind == "note", Job.status.in_(PENDING_STATUSES))
    )
    if pending >= settings.note_max_pending_jobs:
        raise HTTPException(429, CAPACITY_MESSAGE, headers={"Retry-After": RETRY_AFTER_SECONDS})
