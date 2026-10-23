"""A note lineage is bounded so X-Forgotten-Document-Ids always fits in an HTTP header."""

import uuid

from test_note_forget import count, import_note
from test_note_version_races import queue_edit
from test_note_versions import EDITED, MARKER, edit, run_job

from open_learning.models import Document, Job
from open_learning.versions import MAX_NOTE_VERSIONS, NOTE_VERSION_LIMIT

API = "/api/v1"


def fill_lineage(factory, v1, total):
    """Give the note rooted at v1 `total` versions; v1 stays the current one."""
    with factory.begin() as db:
        source_id = db.get(Document, v1).source_id
        for number in range(total - 1):
            db.add(Document(
                source_id=source_id, title="old", text="old", note_id=v1, superseded=True,
                content_hash=uuid.uuid4().hex + uuid.uuid4().hex, version=number + 2,
            ))


def assert_limit_without_text(factory, job_id):
    with factory() as db:
        job = db.get(Job, job_id)
        assert (job.status, job.error) == ("failed", NOTE_VERSION_LIMIT)
        assert (job.payload_text, job.payload_title, job.payload_label) == (None, None, None)


def test_limit_is_bounded_for_the_header():
    assert MAX_NOTE_VERSIONS * 37 < 8 * 1024


def test_edit_is_refused_when_the_lineage_is_at_the_cap(client, factory, settings):
    v1 = import_note(client, factory, settings)
    fill_lineage(factory, v1, MAX_NOTE_VERSIONS)
    response = edit(client, v1, EDITED + MARKER)
    assert (response.status_code, response.json()) == (409, {"detail": NOTE_VERSION_LIMIT})
    assert MARKER not in response.text
    assert count(factory, Job, Job.replaces_document_id == v1) == 0


def test_edit_is_accepted_one_below_the_cap(client, factory, settings):
    v1 = import_note(client, factory, settings)
    fill_lineage(factory, v1, MAX_NOTE_VERSIONS - 1)
    assert edit(client, v1).status_code == 202
    run_job(factory, settings)
    assert count(factory, Document, Document.note_id == v1) == MAX_NOTE_VERSIONS


def test_worker_fails_an_edit_that_would_exceed_the_cap_and_drops_its_text(
    client, factory, settings
):
    v1 = import_note(client, factory, settings)
    queue_edit(factory, v1, EDITED + MARKER)  # Accepted while the lineage had room.
    fill_lineage(factory, v1, MAX_NOTE_VERSIONS)  # Concurrent edits filled it meanwhile.
    run_job(factory, settings)
    with factory() as db:
        pending = db.scalar(Job.__table__.select().with_only_columns(Job.id))
    assert_limit_without_text(factory, pending)
    assert count(factory, Document, Document.note_id == v1) == MAX_NOTE_VERSIONS


def test_forgetting_a_lineage_at_the_cap_returns_a_small_header(client, factory, settings):
    v1 = import_note(client, factory, settings)
    fill_lineage(factory, v1, MAX_NOTE_VERSIONS)
    response = client.delete(f"{API}/notes/{v1}")
    assert response.status_code == 204
    ids = response.headers["X-Forgotten-Document-Ids"].split(",")
    assert len(ids) == MAX_NOTE_VERSIONS and v1 in ids
    assert len(response.headers["X-Forgotten-Document-Ids"]) < 8 * 1024
    assert count(factory, Document) == 0
