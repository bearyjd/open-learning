"""Note versions (E2b-4): editing a note's text publishes a new document version.

An edit is a note job with ``replaces_document_id``. When it completes, the new document gets
``version`` N+1 and ``replaces_document_id``, and the replaced document is marked
``superseded``. Superseded versions keep their text and derived rows but are hidden from the
feed, library, search, due reviews and the default notes list. Each version is forgotten on its
own; forgetting the latest version does NOT make the previous one current again (privacy-safe:
text the user replaced never reappears by itself).

Text identical to any existing note document (current or superseded, the same note or another)
cannot become a version: the note key is its content hash. Such jobs fail with a fixed message.
Responses, errors and logs never carry note text.
"""

import hashlib

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Document, Source

NOTE_CONFLICT = "This text matches another note or version; change it or forget that note first"
NOTE_STALE = "This note was changed or forgotten; edit its latest version"
NO_STORE = {"Cache-Control": "no-store"}


class NoteConflict(Exception):
    """A note job cannot be published; the message is fixed and safe to show."""


def current_documents():
    """Ids of documents that are not superseded, for ``Insight.document_id.in_(...)``."""
    return select(Document.id).where(Document.superseded.is_(False))


def _note_document(db: Session, document_id: str):
    return db.execute(
        select(Document, Source)
        .join(Source, Source.id == Document.source_id)
        .where(Document.id == document_id, Source.kind == "note")
    ).first()


def check_replaceable(db: Session, document_id: str, text: str) -> None:
    """Reject an edit early; the worker re-checks under a row lock before publishing."""
    row = _note_document(db, document_id)
    if row is None:
        raise HTTPException(404, "Note not found")
    document = row[0]
    if document.superseded:
        raise HTTPException(409, "Only the latest version of a note can be edited")
    if document.content_hash == hashlib.sha256(text.encode()).hexdigest():
        raise HTTPException(422, "Text is unchanged")


def is_current(db: Session, document_id: str) -> bool:
    return db.scalar(
        select(Document.id).where(Document.id == document_id, Document.superseded.is_(False))
    ) is not None


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
