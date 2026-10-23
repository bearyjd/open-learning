"""Forgetting notes: listing, hard deletion, and bounded retention of note text in jobs.

Global lock order (every transaction that takes more than one of these must follow it):

    1. the Document row (forget, PATCH, edit ingest and the worker's note edit exclusive; the
       worker's dedupe path, tutor, save, feedback and review_answer shared)
    2. note Job rows, by id
    3. the Source row (PATCH locks it right after the document; forget deletes it last; the
       worker's fresh import takes it after its own job, which is safe because nothing that
       holds a Source row waits for a document or job)
    4. Insight rows, by id
    5. ReviewItem rows, by id

Per path: forget = 1 (every version of the note, see _lock_lineage, or one version with
version_only),2,4,5 then deletes (including the Sources); PATCH edit_note = 1,3; worker
dedupe = 1,2; worker note edit (versions.py) = 1 (only the replaced document, never a second
one),2,3; worker fresh import = 2,3 and never waits for a document (if an identical document
appears it rolls back and restarts through the dedupe path); retry takes one job, and an edit
ingest the replaced Document (1, held until its new job is committed, so a forget either runs
first and the ingest answers 404, or waits and then cancels the new job), then each takes a
transaction-scoped advisory lock. That advisory lock is always the last lock taken, and its
holder never waits for a row afterwards. Tutor takes no lock across its model call, then 1
(shared, re-checking that the note version is still current), 4, 5; review_answer, save and
feedback take 1 (shared, refusing superseded versions), 4, 5. No path acquires an
earlier-numbered lock while holding a later one, so there is no cycle.

The dedupe-job versus forget race is closed in two halves: jobs that exist when forget runs are
cancelled by the hash pass (forget holds the document, so that pass cannot be overtaken), and a
job that appears later can only link the document by share-locking it first, which waits for
forget's commit and then finds the document gone (a fresh import). Responses and logs never
carry note text, except GET /notes/{id}/text, which returns it on purpose with Cache-Control:
no-store (see versions.py).
"""

import hashlib
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import delete, func, or_, select, update
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
EARLIER_VERSIONS = "This note has earlier versions; forget the whole note instead"


def _note_query(*where):
    return (
        select(
            Document.id,
            Document.title,
            Source.label,
            Document.created_at,
            func.length(Document.text),
            Document.version,
            Document.replaces_document_id,
            Document.superseded,
        )
        .join(Source, Source.id == Document.source_id)
        .where(Source.kind == "note", *where)
    )


def _note_dict(row) -> dict:
    id_, title, label, created_at, chars, version, replaces, superseded = row
    return {
        "document_id": id_,
        "title": title,
        "label": label,
        "created_at": created_at,
        "char_count": chars,
        "version": version,
        "replaces_document_id": replaces,
        "superseded": superseded,
    }


def list_notes(
    db: Session, limit: int = NOTE_LIMIT, offset: int = 0, include_superseded: bool = False
) -> list[dict]:
    where = () if include_superseded else (Document.superseded.is_(False),)
    query = _note_query(*where).order_by(Document.created_at.desc(), Document.id.desc())
    return [_note_dict(row) for row in db.execute(query.limit(limit).offset(offset))]


def edit_note(db: Session, document_id: str, changes: dict) -> dict:
    """Update a note's title and/or label in place; no new version, no model call."""
    locked = db.execute(
        select(Document.source_id)
        .join(Source, Source.id == Document.source_id)
        .where(Document.id == document_id, Source.kind == "note")
        .with_for_update(of=Document)
    ).first()
    if locked is None:
        db.rollback()
        raise HTTPException(404, "Note not found")
    document = db.get(Document, document_id)
    source = db.scalar(select(Source).where(Source.id == locked[0]).with_for_update())
    if document is None or source is None:  # Forgotten between the lock and the load (SQLite).
        db.rollback()
        raise HTTPException(404, "Note not found")
    if "title" in changes:
        document.title = source.title = changes["title"]
    if "label" in changes:
        source.label = changes["label"]
    db.commit()
    row = db.execute(_note_query(Document.id == document_id)).first()
    if row is None:  # Forgotten between commit and read.
        raise HTTPException(404, "Note not found")
    return _note_dict(row)


def list_note_jobs(db: Session, limit: int, offset: int) -> list[dict]:
    """Identify note jobs without exposing their text, title, label or error."""
    rows = db.execute(
        select(Job.id, Job.status, Job.created_at, Job.document_id, Job.replaces_document_id)
        .where(Job.kind == "note")
        .order_by(Job.created_at.desc(), Job.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return [
        {
            "id": id_,
            "status": status,
            "created_at": created,
            "document_id": document_id,
            "replaces_document_id": replaces,
        }
        for id_, status, created, document_id, replaces in rows
    ]


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _matching_job_ids(db: Session, content_hashes: set[str]) -> list[str]:
    rows = db.execute(
        select(Job.id, Job.payload_text).where(Job.kind == "note", Job.payload_text.is_not(None))
    )
    return [job_id for job_id, text in rows if _hash(text) in content_hashes]


def _edit_job_ids(db: Session, document_ids: set[str]) -> list[str]:
    """Unfinished edits of these versions; they carry new text, so the hash pass misses them."""
    return list(
        db.scalars(
            select(Job.id).where(
                Job.kind == "note",
                Job.replaces_document_id.in_(document_ids),
                Job.status != "completed",
            )
        )
    )


def _cancel_matching_jobs(db: Session, content_hashes: set[str], document_ids: set[str]) -> None:
    """Cancel jobs carrying this text and unfinished edits of these versions, in one locked pass."""
    ids = set(_matching_job_ids(db, content_hashes)) | set(_edit_job_ids(db, document_ids))
    if not ids:
        return
    jobs = db.scalars(select(Job).where(Job.id.in_(ids)).order_by(Job.id).with_for_update())
    for job in jobs:
        if job.status == "completed":
            continue  # Completed while we waited for the lock.
        is_edit = job.replaces_document_id in document_ids
        if not is_edit and job.payload_text is None:
            continue  # Purged while we waited for the lock.
        if not is_edit and _hash(job.payload_text) not in content_hashes:
            continue
        job.payload_title = job.payload_text = job.payload_label = None
        if job.status in PENDING_STATUSES:  # A failed or cancelled job keeps its outcome.
            job.status, job.error = "cancelled", "Note deleted"
            job.lease_token = job.lease_until = None


def _delete_linked_jobs(db: Session, document_ids: set[str]) -> None:
    """Delete completed note jobs that produced these documents (null-link jobs are untouched)."""
    jobs = db.scalars(
        select(Job)
        .where(Job.kind == "note", Job.status == "completed", Job.document_id.in_(document_ids))
        .order_by(Job.id)
        .with_for_update()
    ).all()
    for job in jobs:
        db.delete(job)


def _lock_graph(db: Session, document_ids: set[str]) -> None:
    insight_ids = select(Insight.id).where(Insight.document_id.in_(document_ids))
    db.execute(
        select(Insight.id)
        .where(Insight.document_id.in_(document_ids))
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


def _lock_note_documents(db: Session, *where) -> list:
    """Lock matching note documents in id order; rows are (id, source_id, content_hash)."""
    return db.execute(
        select(Document.id, Document.source_id, Document.content_hash)
        .join(Source, Source.id == Document.source_id)
        .where(Source.kind == "note", *where)
        .order_by(Document.id)
        .with_for_update(of=Document)
    ).all()


def _lock_lineage(db: Session, note_id: str) -> list:
    """Lock every version of a note, re-selecting until no newer version appears.

    Under READ COMMITTED a version committed while we waited (an edit that held the version it
    replaced) is missing from the first result, so we select again and lock the new ids. That
    later pass can lock a lower id after a higher one; it cannot deadlock because lineage forget
    is the only path that ever holds more than one document row.
    """
    held: dict[str, tuple] = {}
    while True:
        rows = _lock_note_documents(db, _lineage(note_id), Document.id.not_in(list(held)))
        if not rows:
            return list(held.values())
        held.update({row[0]: tuple(row) for row in rows})


def _lineage(note_key: str):
    return or_(Document.note_id == note_key, Document.id == note_key)


def _refuse_partial_forget(db: Session, document_id: str, note_key: str) -> None:
    """Forgetting only the current version must not silently keep its earlier versions' text."""
    superseded = db.scalar(select(Document.superseded).where(Document.id == document_id))
    others = db.scalar(
        select(Document.id).where(_lineage(note_key), Document.id != document_id).limit(1)
    )
    if not superseded and others is not None:
        db.rollback()
        raise HTTPException(409, EARLIER_VERSIONS)


def forget_note(db: Session, document_id: str, version_only: bool = False) -> None:
    """Hard-delete a note and everything derived from it in one transaction.

    By default every version of the note (its lineage) is forgotten; version_only forgets just
    this version and leaves the others as they are; it is refused (409) for a current version
    that still has earlier versions.
    """
    found = db.execute(
        select(Document.note_id)
        .join(Source, Source.id == Document.source_id)
        .where(Document.id == document_id, Source.kind == "note")
    ).first()
    if found is None:
        db.rollback()
        raise HTTPException(404, "Note not found")
    note_key = found[0] or document_id
    if version_only:
        rows = _lock_note_documents(db, Document.id == document_id)
    else:
        rows = _lock_lineage(db, note_key)
    ids = {row[0] for row in rows}
    if document_id not in ids:  # Forgotten between the lookup and the lock.
        db.rollback()
        raise HTTPException(404, "Note not found")
    if version_only:
        _refuse_partial_forget(db, document_id, note_key)
    _cancel_matching_jobs(db, {row[2] for row in rows}, ids)
    _delete_linked_jobs(db, ids)
    _lock_graph(db, ids)
    for id_, source_id, _ in rows:
        _delete_graph(db, id_, source_id)
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
