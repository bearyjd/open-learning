import hashlib

import pytest
from conftest import PASSAGE, TOKEN, FixtureModel, article_fetcher
from sqlalchemy import event, func, select

from open_learning.models import (
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
)
from open_learning.services import claim_job, process_job
from open_learning.worker import run_once

API = "/api/v1"
NOTE = (PASSAGE * 3).strip()
OTHER = (
    "Spaced repetition schedules reviews just before forgetting happens, which saves time. " * 2
).strip()
MARKER = "zebra-quokka-marker-7731"


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def count(factory, model, *where):
    with factory() as db:
        return db.scalar(select(func.count()).select_from(model).where(*where))


def import_note(client, factory, settings, text=NOTE, title="My note"):
    response = client.post(
        API + "/ingest/text", json={"title": title, "text": text, "label": "cls"}
    )
    assert response.status_code == 202
    assert run_once(factory, settings, model=FixtureModel())
    with factory() as db:
        return db.scalar(select(Document.id).where(Document.content_hash == digest(text)))


def insight_of(factory, document_id):
    with factory() as db:
        return db.scalar(select(Insight.id).where(Insight.document_id == document_id))


def url_job(factory, settings):
    with factory.begin() as db:
        db.add(Job(id="url-job", url="https://a.example/x"))
    claim = claim_job(factory, settings.job_lease_seconds)
    process_job(factory, settings, claim, fetcher=article_fetcher, model=FixtureModel())
    with factory() as db:
        return db.scalar(select(Document.id).join(Source).where(Source.kind == "url"))


def failed_note_job(factory, text, job_id, status="failed"):
    with factory.begin() as db:
        db.add(
            Job(
                id=job_id,
                kind="note",
                url=None,
                status=status,
                payload_title="t",
                payload_text=text,
                payload_label="l",
            )
        )


def test_list_notes_shape_and_no_internal_key(client, factory, settings):
    doc = import_note(client, factory, settings)
    url_job(factory, settings)
    response = client.get(API + "/notes")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert set(body[0]) == {"document_id", "title", "label", "created_at", "char_count"}
    assert body[0]["document_id"] == doc and body[0]["label"] == "cls"
    assert "note:" not in response.text and NOTE[:40] not in response.text


def test_list_notes_capped_at_500(client, factory):
    with factory.begin() as db:
        for i in range(502):
            source = Source(url=f"note:{i:064d}", kind="note")
            db.add(source)
            db.flush()
            db.add(Document(source_id=source.id, title=f"n{i}", text="t", content_hash=f"{i:064d}"))
    assert len(client.get(API + "/notes", params={"limit": 500}).json()) == 500


def test_all_endpoints_require_auth(client):
    client.headers.pop("Authorization")
    assert client.get(API + "/notes").status_code == 401
    assert client.delete(API + "/notes/x").status_code == 401
    assert client.delete(API + "/jobs/x").status_code == 401


def enable_sqlite_foreign_keys(connection, _record):
    connection.execute("PRAGMA foreign_keys=ON")


def test_full_graph_delete_leaves_no_children_and_url_doc_intact(client, factory, settings):
    if factory.kw["bind"].dialect.name == "sqlite":  # PostgreSQL always enforces foreign keys.
        event.listen(factory.kw["bind"], "connect", enable_sqlite_foreign_keys)
        factory.kw["bind"].dispose()
    doc = import_note(client, factory, settings)
    url_doc = url_job(factory, settings)
    insight = insight_of(factory, doc)
    assert client.post(f"{API}/insights/{insight}/save").status_code == 200
    assert (
        client.post(f"{API}/tutor/chat", json={"insight_id": insight, "message": "hi"}).status_code
        == 200
    )
    review = client.get(API + "/reviews/due").json()[0]["id"]
    assert client.post(f"{API}/reviews/{review}/answer", json={"rating": "easy"}).status_code == 200
    assert client.get(API + "/feed/today").status_code == 200
    assert count(factory, ReviewHistory) == 1

    assert client.delete(f"{API}/notes/{doc}").status_code == 204

    for model in (ReviewHistory, ReviewItem, History, Interaction, SavedItem):
        assert count(factory, model) == 0
    assert count(factory, Insight, Insight.document_id == doc) == 0
    assert count(factory, Chunk, Chunk.document_id == doc) == 0
    assert count(factory, Document, Document.id == doc) == 0
    assert count(factory, Source, Source.kind == "note") == 0
    assert count(factory, Document, Document.id == url_doc) == 1
    assert count(factory, Insight) == 1 and count(factory, Chunk) == 1
    assert count(factory, Source, Source.kind == "url") == 1
    assert count(factory, DailyFeed) == 1


def test_repeat_url_and_unknown_are_404(client, factory, settings):
    doc = import_note(client, factory, settings)
    url_doc = url_job(factory, settings)
    assert client.delete(f"{API}/notes/{doc}").status_code == 204
    for target in (doc, url_doc, "missing"):
        response = client.delete(f"{API}/notes/{target}")
        assert response.status_code == 404
        assert response.json()["detail"] == "Note not found"
    assert count(factory, Document, Document.id == url_doc) == 1


def test_matching_jobs_cancelled_and_payload_cleared_others_untouched(client, factory, settings):
    doc = import_note(client, factory, settings)
    failed_note_job(factory, NOTE, "same-failed")
    failed_note_job(factory, NOTE, "same-queued", status="queued")
    failed_note_job(factory, OTHER, "other-failed")
    assert client.delete(f"{API}/notes/{doc}").status_code == 204
    with factory() as db:
        for job_id in ("same-failed", "same-queued"):
            job = db.get(Job, job_id)
            assert (job.status, job.error) == ("cancelled", "Note deleted")
            assert job.payload_text is job.payload_title is job.payload_label is None
            assert job.lease_token is None and job.lease_until is None
        other = db.get(Job, "other-failed")
        assert other.status == "failed" and other.payload_text == OTHER


def test_delete_during_generation_is_not_recreated(client, factory, settings):
    client.post(API + "/ingest/text", json={"title": "n", "text": NOTE})
    claim = claim_job(factory, settings.job_lease_seconds)
    state = {}

    class Racing(FixtureModel):
        def insights(self, chunks):
            # A second import of the same text publishes while the first still generates.
            failed_note_job(factory, NOTE, "second", status="queued")
            second = claim_job(factory, settings.job_lease_seconds)
            process_job(factory, settings, second, model=FixtureModel())
            state["doc"] = count(factory, Document)
            doc_id = factory().scalar(select(Document.id))
            assert client.delete(f"{API}/notes/{doc_id}").status_code == 204
            return super().insights(chunks)

    process_job(factory, settings, claim, model=Racing())
    assert state["doc"] == 1
    assert count(factory, Document) == 0 and count(factory, Source) == 0
    with factory() as db:
        job = db.get(Job, claim[0])
        assert job.status == "cancelled" and job.payload_text is None


def test_reimport_after_delete_makes_fresh_rows(client, factory, settings):
    doc = import_note(client, factory, settings)
    old_insight = insight_of(factory, doc)
    assert client.delete(f"{API}/notes/{doc}").status_code == 204
    new_doc = import_note(client, factory, settings)
    assert new_doc != doc and insight_of(factory, new_doc) != old_insight
    assert count(factory, Document) == 1 and count(factory, Source) == 1


def test_reads_after_delete_do_not_fail(client, factory, settings):
    doc = import_note(client, factory, settings)
    insight = insight_of(factory, doc)
    client.post(f"{API}/insights/{insight}/save")
    client.post(f"{API}/tutor/chat", json={"insight_id": insight, "message": "x"})
    assert len(client.get(API + "/feed/today").json()) == 1
    assert client.delete(f"{API}/notes/{doc}").status_code == 204
    for path in (
        "/feed/today",
        "/library",
        "/search?q=retrieval",
        "/reviews/due",
        "/history",
        "/notes",
    ):
        response = client.get(API + path)
        assert response.status_code == 200, path
        assert response.json() == []
    assert client.get(f"{API}/insights/{insight}").status_code == 404


def test_tutor_save_feedback_and_review_on_deleted_note_are_404(client, factory, settings):
    doc = import_note(client, factory, settings)
    insight = insight_of(factory, doc)
    client.post(f"{API}/insights/{insight}/save")
    review = client.get(API + "/reviews/due").json()[0]["id"]
    assert client.delete(f"{API}/notes/{doc}").status_code == 204
    assert client.post(f"{API}/tutor/chat", json={"insight_id": insight}).status_code == 404
    assert client.post(f"{API}/insights/{insight}/save").status_code == 404
    assert (
        client.post(f"{API}/insights/{insight}/feedback", json={"action": "dismiss"}).status_code
        == 404
    )
    assert client.post(f"{API}/reviews/{review}/answer", json={"rating": "easy"}).status_code == 404


def test_tutor_loses_race_with_delete_gets_404(client, factory, settings):
    doc = import_note(client, factory, settings)
    insight = insight_of(factory, doc)

    class Deleting(FixtureModel):
        def tutor(self, passage, title, message, history, **kwargs):
            assert client.delete(f"{API}/notes/{doc}").status_code == 204
            return super().tutor(passage, title, message, history)

    client.app.state.model = Deleting()
    response = client.post(f"{API}/tutor/chat", json={"insight_id": insight})
    assert response.status_code == 404
    assert count(factory, History) == 0 and count(factory, ReviewItem) == 0


@pytest.mark.parametrize("status", ["failed", "queued", "completed", "cancelled"])
def test_delete_note_job_any_status(client, factory, status):
    failed_note_job(factory, NOTE, "j1", status=status)
    assert client.delete(API + "/jobs/j1").status_code == 204
    assert client.get(API + "/jobs/j1").status_code == 404


def test_delete_job_unknown_and_url_job(client, factory):
    assert client.delete(API + "/jobs/missing").status_code == 404
    with factory.begin() as db:
        db.add(Job(id="u1", url="https://a.example/x"))
    response = client.delete(API + "/jobs/u1")
    assert response.status_code == 409
    assert response.json()["detail"] == "Only note jobs can be deleted"
    assert count(factory, Job) == 1


def test_delete_running_job_blocks_completion(client, factory, settings):
    client.post(API + "/ingest/text", json={"title": "n", "text": NOTE})
    claim = claim_job(factory, settings.job_lease_seconds)

    class Deleting(FixtureModel):
        def insights(self, chunks):
            assert client.delete(f"{API}/jobs/{claim[0]}").status_code == 204
            return super().insights(chunks)

    process_job(factory, settings, claim, model=Deleting())
    assert count(factory, Document) == 0 and count(factory, Source) == 0
    assert count(factory, Job) == 0


def test_no_note_text_in_responses_or_logs(client, factory, settings, caplog):
    text = NOTE + MARKER
    doc = import_note(client, factory, settings, text=text)
    failed_note_job(factory, text, "j1")
    responses = [
        client.get(API + "/notes"),
        client.delete(f"{API}/notes/{doc}"),
        client.delete(f"{API}/notes/{doc}"),
        client.delete(API + "/jobs/j1"),
    ]
    for response in responses:
        assert MARKER not in response.text
    assert MARKER not in caplog.text and TOKEN not in caplog.text
