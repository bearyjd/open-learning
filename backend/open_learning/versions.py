"""Note versions (E2b-4): editing a note's text publishes a new document version.

An edit is a note job with ``replaces_document_id``. When it completes, the new document gets
``version`` N+1 and ``replaces_document_id``, and the replaced document is marked
``superseded``. Superseded versions keep their text and derived rows but are hidden from the
feed, library, search, due reviews and the default notes list. Every version carries the root
version's id as ``note_id``; forgetting a note forgets all of its versions unless the caller
asks for one version only. Forgetting only the latest version does NOT make the previous one
current again (privacy-safe: text the user replaced never reappears by itself).

Text identical to any existing note document (current or superseded, the same note or another)
cannot become a version: the note key is its content hash. Such jobs fail with a fixed message.
Responses, errors and logs never carry note text.
"""

import hashlib

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .models import Document, Source

NOTE_CONFLICT = "This text matches another note or version; change it or forget that note first"
NOTE_STALE = "This note was changed or forgotten; edit its latest version"
# One lineage never exceeds this many versions, so the X-Forgotten-Document-Ids header of a
# whole-note forget (36 chars + comma per id, about 3.7 KB at 100) always fits an HTTP header.
MAX_NOTE_VERSIONS = 100
NOTE_VERSION_LIMIT = "This note has reached the version limit; forget it or start a new note"
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


class NoteConflict(Exception):
    """A note job cannot be published; the message is fixed and safe to show."""


class NoteStale(NoteConflict):
    """The version an edit replaces is gone or superseded; the job's text is dropped at once."""


class NoteVersionLimit(NoteStale):
    """The note already has MAX_NOTE_VERSIONS versions; the job's text is dropped at once."""


def version_count(db: Session, document: Document) -> int:
    """Versions in the lineage of a (locked) document, current and superseded."""
    key = document.note_id or document.id
    return db.scalar(
        select(func.count()).select_from(Document).where(
            or_(Document.note_id == key, Document.id == key)
        )
    )


def current_documents():
    """Ids of documents that are not superseded, for ``Insight.document_id.in_(...)``."""
    return select(Document.id).where(Document.superseded.is_(False))


def _note_document(db: Session, document_id: str):
    return db.execute(
        select(Document, Source)
        .join(Source, Source.id == Document.source_id)
        .where(Document.id == document_id, Source.kind == "note")
    ).first()


def lock_replaceable(db: Session, document_id: str, text: str) -> None:
    """Lock the note version an edit replaces and reject a bad edit (lock level 1).

    The caller keeps the lock until its edit job is committed, so a concurrent forget either
    finishes first (404 here) or waits and then cancels the new job. The worker re-checks under
    its own lock before publishing.
    """
    document = db.scalar(
        select(Document)
        .join(Source, Source.id == Document.source_id)
        .where(Document.id == document_id, Source.kind == "note")
        .with_for_update(of=Document)
    )
    if document is None:
        raise HTTPException(404, "Note not found")
    if document.superseded:
        raise HTTPException(409, "Only the latest version of a note can be edited")
    if version_count(db, document) >= MAX_NOTE_VERSIONS:
        raise HTTPException(409, NOTE_VERSION_LIMIT)
    if document.content_hash == hashlib.sha256(text.encode()).hexdigest():
        raise HTTPException(422, "Text is unchanged")


def is_current(db: Session, document_id: str) -> bool:
    return db.scalar(
        select(Document.id).where(Document.id == document_id, Document.superseded.is_(False))
    ) is not None


def lock_current(db: Session, document_id: str) -> bool:
    """Share-lock a document (lock level 1) and report whether it is still current.

    Writers that act on an insight call this before locking the insight, so an edit cannot
    publish a newer version (or a forget delete it) between this check and their commit.
    """
    superseded = db.scalar(
        select(Document.superseded).where(Document.id == document_id).with_for_update(read=True)
    )
    return superseded is False


def lock_replaced(db: Session, document_id: str) -> Document | None:
    """Lock the replaced document (lock level 1, before the job); None if it is gone."""
    return db.scalar(select(Document).where(Document.id == document_id).with_for_update())


def note_text(db: Session, document_id: str) -> dict:
    """The stored text of any note version, current or superseded."""
    row = _note_document(db, document_id)
    if row is None:
        raise HTTPException(404, "Note not found", headers=NO_STORE)
    document, source = row
    return {
        "document_id": document.id,
        "title": document.title,
        "label": source.label,
        "version": document.version,
        "text": document.text,
    }
