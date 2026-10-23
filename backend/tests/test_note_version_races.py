"""Note edits racing forget, publication and the tutor (PR E2b-4 review fixes)."""

import threading
import time

import pytest
from conftest import FixtureModel
from sqlalchemy import create_engine, event, select, text
from test_note_forget import count, import_note, insight_of
from test_note_versions import EDITED, MARKER, STALE, edit, run_job

from open_learning import main
from open_learning.forget import forget_note
from open_learning.models import Document, History, Interaction, Job, ReviewItem
from open_learning.worker import run_once

API = "/api/v1"


def require_postgres(factory):
    if factory.kw["bind"].dialect.name != "postgresql":
        pytest.skip("Lock races require OL_TEST_DATABASE_URL PostgreSQL")


def test_forget_between_edit_check_and_job_insert_leaves_no_edit_job(
    client, factory, settings, monkeypatch
):
    """Forget committing after the request starts but before its job insert: 404, no job."""
    v1 = import_note(client, factory, settings)
    original = main.purge_note_payloads

    def forget_first(db, settings):
        with factory() as other:
            forget_note(other, v1)
        return original(db, settings)

    monkeypatch.setattr(main, "purge_note_payloads", forget_first)
    response = edit(client, v1, EDITED + MARKER)
    assert response.status_code == 404
    assert response.json() == {"detail": "Note not found"}
    assert count(factory, Job, Job.payload_text.is_not(None)) == 0
    assert count(factory, Job, Job.replaces_document_id == v1) == 0


def wait_for_lock_waiter(url, timeout=10.0):
    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    deadline = time.monotonic() + timeout
    try:
        with engine.connect() as connection:
            while time.monotonic() < deadline:
                waiting = connection.execute(
                    text(
                        "SELECT COUNT(*) FROM pg_stat_activity "
                        "WHERE wait_event_type = 'Lock' AND datname = current_database()"
                    )
                ).scalar()
                if waiting:
                    return True
                time.sleep(0.02)
    finally:
        engine.dispose()
    return False


def test_forget_waiting_on_edit_lock_cancels_the_new_job_on_postgresql(client, factory, settings):
    """The edit request holds the replaced document while inserting; forget then sees the job."""
    require_postgres(factory)
    v1 = import_note(client, factory, settings)
    engine = factory.kw["bind"]
    results, fired = {}, []

    def forget():
        with factory() as db:
            try:
                forget_note(db, v1)
                results["forget"] = 204
            except Exception as error:  # surfaced in the assertion below
                results["forget"] = repr(error)

    thread = threading.Thread(target=forget)

    def after_lock(conn, cursor, statement, parameters, *args):
        values = parameters.values() if isinstance(parameters, dict) else parameters
        if fired or "FOR UPDATE OF documents" not in statement or v1 not in tuple(values):
            return
        fired.append(1)
        thread.start()
        assert wait_for_lock_waiter(engine.url.render_as_string(hide_password=False))

    event.listen(engine, "after_cursor_execute", after_lock)
    try:
        response = edit(client, v1, EDITED + MARKER)
    finally:
        event.remove(engine, "after_cursor_execute", after_lock)
    thread.join(timeout=30)
    assert fired, "the edit request never locked the replaced document"
    assert response.status_code == 202, response.text
    assert results["forget"] == 204, results
    with factory() as db:
        job = db.get(Job, response.json()["id"])
        assert job.status == "cancelled"
        assert (job.payload_text, job.payload_title, job.payload_label) == (None, None, None)
    assert not run_once(factory, settings)
    assert count(factory, Document) == 0


def test_stale_edit_job_drops_its_text_immediately(client, factory, settings):
    v1 = import_note(client, factory, settings)
    first = edit(client, v1).json()["id"]
    second = edit(client, v1, EDITED + MARKER).json()["id"]
    run_job(factory, settings)
    run_job(factory, settings)
    with factory() as db:
        assert db.get(Job, first).status == "completed"
        stale = db.get(Job, second)
        assert stale.status == "failed"
        assert (stale.payload_text, stale.payload_title, stale.payload_label) == (None, None, None)
    retried = client.post(f"{API}/jobs/{second}/retry")
    assert retried.status_code == 409


def test_edit_job_whose_note_was_forgotten_drops_its_text(client, factory, settings):
    v1 = import_note(client, factory, settings)
    pending = edit(client, v1, EDITED + MARKER).json()["id"]
    with factory.begin() as db:  # Simulate a forget that missed the job (pre-fix data).
        db.execute(Document.__table__.update().where(Document.id == v1).values(superseded=True))
    run_job(factory, settings)
    with factory() as db:
        job = db.get(Job, pending)
        assert job.status == "failed"
        assert job.payload_text is None and job.payload_title is None
        assert db.scalar(select(Job.id).where(Job.payload_text.is_not(None))) is None


def delete_document_graph(factory, document_id):
    """Delete a note version and its rows without touching jobs (a forget that missed the job)."""
    from open_learning.forget import _delete_graph, _lock_graph

    with factory.begin() as db:
        source_id = db.get(Document, document_id).source_id
        _lock_graph(db, {document_id})
        _delete_graph(db, document_id, source_id)


def assert_stale_without_text(factory, job_id):
    with factory() as db:
        job = db.get(Job, job_id)
        assert (job.status, job.error) == ("failed", STALE)
        assert (job.payload_text, job.payload_title, job.payload_label) == (None, None, None)


def test_edit_job_whose_target_was_deleted_before_it_ran_drops_its_text(client, factory, settings):
    v1 = import_note(client, factory, settings)
    pending = edit(client, v1, EDITED + MARKER).json()["id"]
    delete_document_graph(factory, v1)
    run_job(factory, settings)
    assert_stale_without_text(factory, pending)


def test_edit_job_whose_target_was_deleted_during_inference_drops_its_text(
    client, factory, settings
):
    v1 = import_note(client, factory, settings)
    pending = edit(client, v1, EDITED + MARKER).json()["id"]

    class Deleting(FixtureModel):
        def insights(self, chunks):
            delete_document_graph(factory, v1)
            return super().insights(chunks)

    assert run_once(factory, settings, model=Deleting())
    assert_stale_without_text(factory, pending)
    assert count(factory, Document) == 0


def queue_edit(factory, replaces, text=EDITED):
    with factory.begin() as db:
        db.add(Job(kind="note", url=None, payload_title="t", payload_text=text,
                   replaces_document_id=replaces))


def test_tutor_writes_nothing_when_an_edit_publishes_during_inference(client, factory, settings):
    v1 = import_note(client, factory, settings)
    insight = insight_of(factory, v1)
    model = client.app.state.model
    original = model.tutor

    def publishing_tutor(*args, **kwargs):
        queue_edit(factory, v1)
        assert run_once(factory, settings, model=FixtureModel())
        return original(*args, **kwargs)

    model.tutor = publishing_tutor
    response = client.post(API + "/tutor/chat", json={"insight_id": insight, "message": "Why?"})
    assert (response.status_code, response.json()) == (404, {"detail": "Insight not found"})
    with factory() as db:
        assert db.get(Document, v1).superseded is True
    for table in (History, Interaction, ReviewItem):
        assert count(factory, table, table.insight_id == insight) == 0, table


def test_identical_edits_racing_past_dedupe_both_complete_on_one_version(
    client, factory, settings, monkeypatch
):
    """B passes its final dedupe check, then A publishes before B locks the replaced version."""
    from open_learning import services

    v1 = import_note(client, factory, settings)
    first = edit(client, v1).json()["id"]
    second = edit(client, v1).json()["id"]
    claim_a = services.claim_job(factory, settings.job_lease_seconds)
    claim_b = services.claim_job(factory, settings.job_lease_seconds)
    original = services.lock_replaced

    def a_publishes_first(db, document_id):
        monkeypatch.setattr(services, "lock_replaced", original)
        services.process_job(factory, settings, claim_a, model=FixtureModel())
        return original(db, document_id)

    monkeypatch.setattr(services, "lock_replaced", a_publishes_first)
    services.process_job(factory, settings, claim_b, model=FixtureModel())
    with factory() as db:
        a, b = db.get(Job, first), db.get(Job, second)
        assert a.status == "completed" and a.document_id
        assert (b.status, b.error, b.document_id) == ("completed", None, a.document_id)
        assert b.payload_text is None
    assert count(factory, Document) == 2
