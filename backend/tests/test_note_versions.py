"""Note resubmit as a new version (PR E2b-4): API, worker and forget behaviour."""

import pytest
from conftest import PASSAGE, FixtureModel
from test_note_forget import NOTE, OTHER, count, import_note, url_job

from open_learning.models import Document, Job
from open_learning.worker import run_once

API = "/api/v1"
EDITED = (PASSAGE * 4).strip()
EDITED_AGAIN = (PASSAGE * 5).strip()
MARKER = "wombat-private-marker-9043"
CONFLICT = "This text matches another note or version; change it or forget that note first"
STALE = "This note was changed or forgotten; edit its latest version"
EARLIER_VERSIONS = "This note has earlier versions; forget the whole note instead"


def edit(client, document_id, text=EDITED, title="Edited", label="cls2"):
    body = {"title": title, "text": text, "replaces_document_id": document_id}
    if label is not None:
        body["label"] = label
    return client.post(API + "/ingest/text", json=body)


def run_job(factory, settings):
    assert run_once(factory, settings, model=FixtureModel())


def job(client, job_id):
    return client.get(f"{API}/jobs/{job_id}").json()


def document(factory, document_id):
    with factory() as db:
        return db.get(Document, document_id)


def edit_and_run(client, factory, settings, document_id, text=EDITED):
    response = edit(client, document_id, text)
    assert response.status_code == 202, response.text
    run_job(factory, settings)
    body = job(client, response.json()["id"])
    assert body["status"] == "completed", body
    return body["document_id"]


def test_edit_creates_linked_version_and_supersedes_old(client, factory, settings):
    v1 = import_note(client, factory, settings)
    response = edit(client, v1)
    assert response.status_code == 202
    assert response.json()["replaces_document_id"] == v1
    run_job(factory, settings)
    v2 = job(client, response.json()["id"])["document_id"]
    assert v2 and v2 != v1
    new, old = document(factory, v2), document(factory, v1)
    assert (new.version, new.replaces_document_id, new.superseded) == (2, v1, False)
    assert (old.version, old.superseded) == (1, True)
    with factory() as db:
        assert db.get(Job, response.json()["id"]).replaces_document_id == v1


def test_version_chain_increments(client, factory, settings):
    v1 = import_note(client, factory, settings)
    v2 = edit_and_run(client, factory, settings, v1)
    v3 = edit_and_run(client, factory, settings, v2, EDITED_AGAIN)
    assert document(factory, v3).version == 3
    assert document(factory, v3).replaces_document_id == v2


def test_new_version_uses_submitted_title_and_label(client, factory, settings):
    v1 = import_note(client, factory, settings)
    v2 = edit_and_run(client, factory, settings, v1)
    listed = client.get(API + "/notes").json()
    assert [(n["document_id"], n["title"], n["label"]) for n in listed] == [(v2, "Edited", "cls2")]


@pytest.mark.parametrize("bad", ["", "x", "0" * 37, "not-a-uuid-but-36-characters-long!!!", 5])
def test_malformed_replaces_id_is_422_without_echo(client, factory, bad):
    body = {"title": "t", "text": NOTE + MARKER, "replaces_document_id": bad}
    response = client.post(API + "/ingest/text", json=body)
    assert response.status_code == 422
    assert MARKER not in response.text
    assert count(factory, Job) == 0


def test_unknown_or_url_document_is_404(client, factory, settings):
    unknown = "00000000-0000-4000-8000-000000000000"
    assert edit(client, unknown).status_code == 404
    article = url_job(factory, settings)
    response = edit(client, article)
    assert response.status_code == 404
    assert response.json() == {"detail": "Note not found"}
    assert count(factory, Job, Job.kind == "note") == 0


def test_editing_superseded_version_is_409(client, factory, settings):
    v1 = import_note(client, factory, settings)
    edit_and_run(client, factory, settings, v1)
    response = edit(client, v1, EDITED_AGAIN)
    assert response.status_code == 409
    assert response.json() == {"detail": "Only the latest version of a note can be edited"}


def test_unchanged_text_is_422(client, factory, settings):
    v1 = import_note(client, factory, settings)
    response = edit(client, v1, NOTE + MARKER)  # Different text is accepted...
    assert response.status_code == 202
    response = edit(client, v1, "  " + NOTE + "\r\n")  # ...normalised identical text is not.
    assert response.status_code == 422
    assert response.json() == {"detail": "Text is unchanged"}


def test_edit_matching_another_note_fails_with_fixed_message(client, factory, settings, caplog):
    v1 = import_note(client, factory, settings)
    other = OTHER * 2 + MARKER
    import_note(client, factory, settings, text=other)
    response = edit(client, v1, other)
    run_job(factory, settings)
    body = job(client, response.json()["id"])
    assert (body["status"], body["error"], body["document_id"]) == ("failed", CONFLICT, None)
    assert MARKER not in str(body)
    assert MARKER not in caplog.text
    assert document(factory, v1).superseded is False


def test_edit_back_to_old_version_text_fails(client, factory, settings):
    v1 = import_note(client, factory, settings)
    v2 = edit_and_run(client, factory, settings, v1)
    response = edit(client, v2, NOTE)
    run_job(factory, settings)
    assert job(client, response.json()["id"])["error"] == CONFLICT
    assert document(factory, v2).superseded is False


def test_plain_ingest_matching_superseded_version_fails(client, factory, settings):
    v1 = import_note(client, factory, settings)
    edit_and_run(client, factory, settings, v1)
    response = client.post(API + "/ingest/text", json={"title": "Again", "text": NOTE})
    run_job(factory, settings)
    body = job(client, response.json()["id"])
    assert (body["status"], body["error"], body["document_id"]) == ("failed", CONFLICT, None)


def test_plain_ingest_matching_current_note_still_dedupes(client, factory, settings):
    v1 = import_note(client, factory, settings)
    v2 = edit_and_run(client, factory, settings, v1)
    response = client.post(API + "/ingest/text", json={"title": "Again", "text": EDITED})
    run_job(factory, settings)
    assert job(client, response.json()["id"])["document_id"] == v2


def test_second_queued_edit_of_same_version_fails_stale(client, factory, settings):
    v1 = import_note(client, factory, settings)
    first = edit(client, v1).json()["id"]
    second = edit(client, v1, EDITED_AGAIN).json()["id"]
    run_job(factory, settings)
    run_job(factory, settings)
    assert job(client, first)["status"] == "completed"
    assert (job(client, second)["status"], job(client, second)["error"]) == ("failed", STALE)
    assert count(factory, Document) == 2


def test_forget_original_cancels_pending_edit(client, factory, settings):
    v1 = import_note(client, factory, settings)
    pending = edit(client, v1, EDITED + MARKER).json()["id"]
    assert client.delete(f"{API}/notes/{v1}").status_code == 204
    with factory() as db:
        row = db.get(Job, pending)
        assert row.status == "cancelled"
        assert (row.payload_text, row.payload_title, row.payload_label) == (None, None, None)
    assert not run_once(factory, settings, model=FixtureModel())
    assert count(factory, Document) == 0


def test_version_only_forget_of_latest_with_predecessors_is_refused(client, factory, settings):
    v1 = import_note(client, factory, settings)
    v2 = edit_and_run(client, factory, settings, v1)
    response = client.delete(f"{API}/notes/{v2}", params={"version_only": True})
    assert (response.status_code, response.json()) == (409, {"detail": EARLIER_VERSIONS})
    assert document(factory, v1).superseded is True and document(factory, v2) is not None
    # Once the earlier version is gone, the latest is the only one and may be forgotten alone,
    # and nothing comes back.
    assert client.delete(f"{API}/notes/{v1}", params={"version_only": True}).status_code == 204
    assert client.delete(f"{API}/notes/{v2}", params={"version_only": True}).status_code == 204
    assert client.get(API + "/notes", params={"include_superseded": True}).json() == []
    assert count(factory, Document) == 0


def test_forget_old_version_keeps_latest(client, factory, settings):
    v1 = import_note(client, factory, settings)
    v2 = edit_and_run(client, factory, settings, v1)
    assert client.delete(f"{API}/notes/{v1}", params={"version_only": True}).status_code == 204
    listed = client.get(API + "/notes").json()
    assert [n["document_id"] for n in listed] == [v2]
    assert listed[0]["version"] == 2 and listed[0]["replaces_document_id"] == v1


def test_notes_list_version_fields(client, factory, settings):
    v1 = import_note(client, factory, settings)
    v2 = edit_and_run(client, factory, settings, v1)
    rows = client.get(API + "/notes", params={"include_superseded": True}).json()
    fields = {r["document_id"]: (r["version"], r["replaces_document_id"], r["superseded"]) for r in rows}
    assert fields == {v1: (1, None, True), v2: (2, v1, False)}


def test_job_list_exposes_replaces_document_id(client, factory, settings):
    v1 = import_note(client, factory, settings)
    edit(client, v1)
    items = client.get(API + "/jobs", params={"kind": "note"}).json()
    assert items[0]["replaces_document_id"] == v1
    assert items[1]["replaces_document_id"] is None



def test_retried_identical_edit_completes_on_the_same_version(client, factory, settings):
    v1 = import_note(client, factory, settings)
    first = edit(client, v1).json()["id"]
    retry = edit(client, v1).json()["id"]  # A client retry before the first one ran.
    run_job(factory, settings)
    run_job(factory, settings)
    v2 = job(client, first)["document_id"]
    body = job(client, retry)
    assert (body["status"], body["error"], body["document_id"]) == ("completed", None, v2)
    assert count(factory, Document) == 2
    with factory() as db:
        assert db.get(Job, retry).payload_text is None


def test_retried_edit_after_a_newer_version_fails_stale(client, factory, settings):
    v1 = import_note(client, factory, settings)
    first = edit(client, v1).json()["id"]
    retry = edit(client, v1).json()["id"]
    run_job(factory, settings)
    v2 = job(client, first)["document_id"]
    with factory.begin() as db:  # The duplicate failed transiently and waits for a retry.
        db.get(Job, retry).status = "failed"
    edit_and_run(client, factory, settings, v2, EDITED_AGAIN)
    assert client.post(f"{API}/jobs/{retry}/retry").status_code == 200
    run_job(factory, settings)
    body = job(client, retry)
    assert (body["status"], body["error"], body["document_id"]) == ("failed", STALE, None)
