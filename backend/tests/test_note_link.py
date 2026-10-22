"""Job to document link, note char_count, /limits and PATCH /notes (PR E2b-1)."""

import logging
import threading

import pytest
from conftest import TOKEN, FixtureModel
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_note_forget import NOTE, OTHER, count, digest, import_note

from open_learning.config import Settings
from open_learning.main import create_app
from open_learning.models import Document, Job, Source
from open_learning.worker import run_once

API = "/api/v1"
MARKER = "quokka-private-marker-5521"


def queue_note(client, text=NOTE, title="My note"):
    response = client.post(API + "/ingest/text", json={"title": title, "text": text})
    assert response.status_code == 202
    return response.json()["id"]


def run_job(factory, settings):
    assert run_once(factory, settings, model=FixtureModel())


def doc_id(factory, text):
    with factory() as db:
        return db.scalar(select(Document.id).where(Document.content_hash == digest(text)))


def test_document_id_set_on_fresh_import(client, factory, settings):
    job_id = queue_note(client)
    assert client.get(f"{API}/jobs/{job_id}").json()["document_id"] is None
    run_job(factory, settings)
    body = client.get(f"{API}/jobs/{job_id}").json()
    assert body["status"] == "completed"
    assert body["document_id"] == doc_id(factory, NOTE)


def test_document_id_set_on_dedupe_completion(client, factory, settings):
    first = queue_note(client)
    run_job(factory, settings)
    second = queue_note(client, title="Same text again")
    run_job(factory, settings)
    expected = doc_id(factory, NOTE)
    assert client.get(f"{API}/jobs/{first}").json()["document_id"] == expected
    assert client.get(f"{API}/jobs/{second}").json()["document_id"] == expected
    assert count(factory, Document) == 1


def test_job_list_exposes_document_id(client, factory, settings):
    queue_note(client)
    run_job(factory, settings)
    items = client.get(f"{API}/jobs", params={"kind": "note"}).json()
    assert items[0]["document_id"] == doc_id(factory, NOTE)
    queue_note(client, text=OTHER * 2)
    pending = client.get(f"{API}/jobs", params={"kind": "note"}).json()
    assert pending[0]["status"] == "queued" and pending[0]["document_id"] is None


def test_retry_response_has_document_id_field(client, factory):
    with factory.begin() as db:
        db.add(Job(id="f1", kind="note", url=None, status="failed", payload_title="t",
                   payload_text=NOTE, payload_label=None))
    assert client.post(f"{API}/jobs/f1/retry").json()["document_id"] is None


def test_forget_removes_only_linked_completed_jobs(client, factory, settings):
    linked = queue_note(client)
    run_job(factory, settings)
    other = queue_note(client, text=OTHER * 2)
    run_job(factory, settings)
    with factory.begin() as db:
        db.add(Job(id="legacy", kind="note", url=None, status="completed"))  # Null link.
        db.add(Job(id="queued-same", kind="note", url=None, status="queued",
                   payload_title="t", payload_text=NOTE))
    assert client.delete(f"{API}/notes/{doc_id(factory, NOTE)}").status_code == 204
    assert client.get(f"{API}/jobs/{linked}").status_code == 404
    assert client.get(f"{API}/jobs/{other}").status_code == 200
    assert client.get(f"{API}/jobs/legacy").status_code == 200
    with factory() as db:
        queued = db.get(Job, "queued-same")
        assert (queued.status, queued.payload_text) == ("cancelled", None)


def test_forget_keeps_failed_job_that_links_nothing(client, factory, settings):
    queue_note(client)
    run_job(factory, settings)
    with factory.begin() as db:
        db.add(Job(id="failed-other", kind="note", url=None, status="failed",
                   payload_title="t", payload_text=OTHER))
    client.delete(f"{API}/notes/{doc_id(factory, NOTE)}")
    assert client.get(f"{API}/jobs/failed-other").status_code == 200


def test_char_count_counts_characters_not_bytes_or_utf16(client, factory, settings):
    text = NOTE + " " + "\U0001F600" * 30 + " é中"
    queue_note(client, text=text)
    run_job(factory, settings)
    notes = client.get(f"{API}/notes").json()
    stored = text.strip()
    assert notes[0]["char_count"] == len(stored)
    assert len(stored.encode("utf-16-le")) // 2 != len(stored)  # Non-BMP really is present.


def test_limits_reflect_settings_and_require_auth(factory):
    custom = Settings(api_token=TOKEN, max_document_chars=2000)
    with TestClient(create_app(custom, factory, FixtureModel())) as anon:
        assert anon.get(API + "/limits").status_code == 401
        anon.headers["Authorization"] = "Bearer " + TOKEN
        body = anon.get(API + "/limits").json()
    assert body == {
        "max_document_chars": 2000,
        "min_text_chars": 100,
        "max_title_chars": 500,
        "max_label_chars": 100,
    }


def test_limits_default(client):
    assert client.get(API + "/limits").json()["max_document_chars"] == 60_000


def imported(client, factory, settings):
    return import_note(client, factory, settings)


def test_patch_updates_title_and_label_without_new_version(client, factory, settings):
    doc = imported(client, factory, settings)
    response = client.patch(f"{API}/notes/{doc}", json={"title": "  New title ", "label": " Bio "})
    assert response.status_code == 200
    body = response.json()
    assert (body["document_id"], body["title"], body["label"]) == (doc, "New title", "Bio")
    assert body["char_count"] == len(NOTE)
    assert client.get(f"{API}/notes").json()[0]["title"] == "New title"
    assert count(factory, Document) == 1
    with factory() as db:
        assert db.scalar(select(Source.label).join(Document).where(Document.id == doc)) == "Bio"
        assert db.scalar(select(Source.title).join(Document).where(Document.id == doc)) == "New title"
        assert db.scalar(select(Document.title).where(Document.id == doc)) == "New title"


def test_patch_partial_keeps_other_field(client, factory, settings):
    doc = imported(client, factory, settings)
    assert client.patch(f"{API}/notes/{doc}", json={"title": "Only title"}).json()["label"] == "cls"
    assert client.patch(f"{API}/notes/{doc}", json={"label": "Only label"}).json()["title"] == "Only title"


@pytest.mark.parametrize("label", [None, "", "   "])
def test_patch_label_clears(client, factory, settings, label):
    doc = imported(client, factory, settings)
    response = client.patch(f"{API}/notes/{doc}", json={"label": label})
    assert response.status_code == 200 and response.json()["label"] is None
    assert client.get(f"{API}/notes").json()[0]["label"] is None


def test_patch_idempotent(client, factory, settings):
    doc = imported(client, factory, settings)
    first = client.patch(f"{API}/notes/{doc}", json={"title": "Same", "label": "L"})
    second = client.patch(f"{API}/notes/{doc}", json={"title": "Same", "label": "L"})
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()


BAD_BODIES = [
    {"title": ""},
    {"title": "   "},
    {"title": None},
    {"title": "x" * 501},
    {"title": "a\tb"},
    {"title": "a\nb"},
    {"title": "a‮b"},
    {"title": "a b"},
    {"label": "x" * 101},
    {"label": "a\x00b"},
    {"label": "a⁧b"},
    {"title": "ok", "extra": 1},
    {"text": "new text"},
    {},
    {"title": 5},
]


@pytest.mark.parametrize("body", BAD_BODIES)
def test_patch_rejects_invalid(client, factory, settings, body):
    doc = imported(client, factory, settings)
    assert client.patch(f"{API}/notes/{doc}", json=body).status_code == 422
    assert client.get(f"{API}/notes").json()[0]["title"] == "My note"


def test_patch_rejects_non_object_body(client, factory, settings):
    doc = imported(client, factory, settings)
    assert client.patch(f"{API}/notes/{doc}", json=["x"]).status_code == 422


def test_patch_404_unknown_and_url_document(client, factory, settings):
    assert client.patch(f"{API}/notes/nope", json={"title": "T"}).json() == {"detail": "Note not found"}
    with factory.begin() as db:
        source = Source(url="https://a.example/x", title="t")
        db.add(source)
        db.flush()
        db.add(Document(id="url-doc", source_id=source.id, title="t", text="x", content_hash="h"))
    response = client.patch(f"{API}/notes/url-doc", json={"title": "T"})
    assert (response.status_code, response.json()) == (404, {"detail": "Note not found"})


def test_patch_requires_auth(client, factory, settings):
    doc = imported(client, factory, settings)
    del client.headers["Authorization"]
    assert client.patch(f"{API}/notes/{doc}", json={"title": "T"}).status_code == 401


@pytest.mark.parametrize(
    "body",
    [{"title": MARKER + "\x00"}, {"title": MARKER * 200}, {"label": MARKER + "\n"},
     {"title": MARKER, "extra": MARKER}, {"title": 7, "label": MARKER}],
)
def test_patch_errors_and_logs_never_echo_input(client, factory, settings, caplog, body):
    doc = imported(client, factory, settings)
    with caplog.at_level(logging.DEBUG):
        response = client.patch(f"{API}/notes/{doc}", json=body)
    assert response.status_code == 422
    assert MARKER not in response.text
    assert MARKER not in caplog.text


@pytest.mark.skipif(
    not __import__("os").environ.get("OL_TEST_DATABASE_URL", "").startswith("postgresql"),
    reason="PostgreSQL only",
)
def test_concurrent_patch_and_forget_never_500(factory, settings):
    for _ in range(8):
        with TestClient(create_app(settings, factory, FixtureModel())) as client:
            client.headers["Authorization"] = "Bearer " + TOKEN
            doc = import_note(client, factory, settings)
            barrier = threading.Barrier(2)
            codes = []

            def call(method):
                barrier.wait()
                if method == "patch":
                    codes.append(client.patch(f"{API}/notes/{doc}", json={"title": "Racy"}).status_code)
                else:
                    codes.append(client.delete(f"{API}/notes/{doc}").status_code)

            threads = [threading.Thread(target=call, args=(m,)) for m in ("patch", "delete")]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            assert sorted(codes) in ([200, 204], [204, 404]), codes
            assert count(factory, Document) == 0
            with factory.begin() as db:
                for job in db.scalars(select(Job)):
                    db.delete(job)


def dangling_links(factory):
    with factory() as db:
        linked = db.scalars(select(Job.document_id).where(Job.document_id.is_not(None))).all()
        alive = set(db.scalars(select(Document.id)))
    return [link for link in linked if link not in alive]


def test_patch_when_document_vanishes_after_locked_select_is_404(client, factory, settings, monkeypatch):
    # SQLite has no row locks, so simulate a forget committing between the select and the load.
    doc = imported(client, factory, settings)
    from sqlalchemy.orm import Session

    real_get = Session.get

    def vanished(self, entity, *args, **kwargs):
        return None if entity is Document else real_get(self, entity, *args, **kwargs)

    monkeypatch.setattr(Session, "get", vanished)
    response = client.patch(f"{API}/notes/{doc}", json={"title": "Racy"})
    assert response.status_code == 404
    assert response.json()["detail"] == "Note not found"
    monkeypatch.undo()
    with factory() as db:
        assert db.scalar(select(Source.title).join(Document).where(Document.id == doc)) == "My note"


def require_postgres(factory):
    if factory.kw["bind"].dialect.name != "postgresql":
        pytest.skip("Lock-order races require OL_TEST_DATABASE_URL PostgreSQL")


def test_dedupe_job_after_hash_cancel_cannot_link_a_deleted_note(client, factory, settings):
    """A duplicate submitted after forget's hash-cancel pass must not link the doomed document."""
    require_postgres(factory)
    from sqlalchemy import event

    engine = factory.kw["bind"]
    for round_ in range(5):
        text = f"{NOTE} round {round_}"
        doc = import_note(client, factory, settings, text=text)
        paused, worker_done, fired = threading.Event(), threading.Event(), []

        def gate(conn, cursor, statement, parameters, *args):
            # forget's linked-job delete runs right after its hash-cancel pass, still holding locks.
            if "FOR UPDATE" in statement and "jobs.document_id" in statement and not fired:
                fired.append(True)
                paused.set()
                worker_done.wait(3)

        event.listen(engine, "after_cursor_execute", gate)
        codes, errors = [], []
        forget = threading.Thread(target=lambda: codes.append(client.delete(f"{API}/notes/{doc}").status_code))
        try:
            forget.start()
            assert paused.wait(5)
            second = queue_note(client, text=text, title="Again")

            def work():
                try:
                    run_job(factory, settings)
                except Exception as error:  # surfaced below
                    errors.append(error)

            worker = threading.Thread(target=work)
            worker.start()
            worker.join(2)  # With the fix the worker waits on the document lock here.
            worker_done.set()
            worker.join(10)
            forget.join(10)
        finally:
            worker_done.set()
            event.remove(engine, "after_cursor_execute", gate)
        assert not errors and codes == [204], (errors, codes)
        assert dangling_links(factory) == []
        body = client.get(f"{API}/jobs/{second}").json()
        assert body["status"] == "completed" and body["document_id"] != doc


def test_forget_racing_dedupe_completion_has_no_dangling_link_or_deadlock(client, factory, settings):
    require_postgres(factory)
    from test_review_fixes import run_race

    for round_ in range(6):
        text = f"{NOTE} race {round_}"
        doc = import_note(client, factory, settings, text=text)
        second = queue_note(client, text=text, title="Again")
        results = run_race(
            factory,
            {
                # The worker has seen the document; forget already holds its lock.
                "worker": lambda s, p: "JOIN sources" in s and "FOR" not in s and "note:" in str(p),
                "delete": lambda s, p: "FOR UPDATE OF documents" in s and doc in p,
            },
            {
                "worker": lambda: run_once(factory, settings, model=FixtureModel()),
                "delete": lambda: client.delete(f"{API}/notes/{doc}").status_code,
            },
        )
        assert results["delete"] == 204 and results["worker"] is True, results
        assert dangling_links(factory) == []
        body = client.get(f"{API}/jobs/{second}").json()
        assert body["status"] in ("completed", "cancelled")
