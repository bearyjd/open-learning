"""Whole-note forget: DELETE /notes/{id} forgets every version of the note (lineage)."""

import threading

import pytest
from conftest import FixtureModel
from sqlalchemy import event, select
from test_note_forget import OTHER, count, import_note
from test_note_version_races import require_postgres, wait_for_lock_waiter
from test_note_versions import EARLIER_VERSIONS, EDITED, EDITED_AGAIN, MARKER, edit, edit_and_run

from open_learning.forget import forget_note
from open_learning.models import Chunk, Document, Insight, Job, Source
from open_learning.services import claim_job, process_job

API = "/api/v1"
THIRD = EDITED + " Third version."


def three_versions(client, factory, settings):
    v1 = import_note(client, factory, settings)
    v2 = edit_and_run(client, factory, settings, v1)
    v3 = edit_and_run(client, factory, settings, v2, EDITED_AGAIN)
    return v1, v2, v3


def note_documents(factory):
    with factory() as db:
        return set(db.scalars(select(Document.id).join(Source).where(Source.kind == "note")))


def test_versions_share_the_root_note_id(client, factory, settings):
    v1, v2, v3 = three_versions(client, factory, settings)
    with factory() as db:
        assert {db.get(Document, d).note_id for d in (v1, v2, v3)} == {v1}


@pytest.mark.parametrize("which", [0, 1, 2])
def test_forgetting_any_version_forgets_the_whole_note(client, factory, settings, which):
    versions = three_versions(client, factory, settings)
    other = import_note(client, factory, settings, text=OTHER)
    assert client.delete(f"{API}/notes/{versions[which]}").status_code == 204
    assert note_documents(factory) == {other}
    with factory() as db:
        assert db.scalar(select(Insight.id).where(Insight.document_id.in_(versions))) is None
        assert db.scalar(select(Chunk.id).where(Chunk.document_id.in_(versions))) is None
    assert count(factory, Source, Source.kind == "note") == 1
    assert count(factory, Job, Job.document_id.in_(versions)) == 0
    for version in versions:
        assert client.get(f"{API}/notes/{version}/text").status_code == 404


def test_whole_note_forget_cancels_pending_edit_of_latest(client, factory, settings):
    v1, v2, v3 = three_versions(client, factory, settings)
    pending = edit(client, v3, THIRD + MARKER).json()["id"]
    assert client.delete(f"{API}/notes/{v1}").status_code == 204
    with factory() as db:
        job = db.get(Job, pending)
        assert job.status == "cancelled"
        assert (job.payload_text, job.payload_title, job.payload_label) == (None, None, None)
    assert note_documents(factory) == set()


def test_version_only_forget_keeps_other_versions(client, factory, settings):
    v1, v2, v3 = three_versions(client, factory, settings)
    response = client.delete(f"{API}/notes/{v2}", params={"version_only": True})
    assert response.status_code == 204
    assert note_documents(factory) == {v1, v3}


def test_version_only_forget_of_current_version_with_predecessors_is_refused(
    client, factory, settings
):
    versions = three_versions(client, factory, settings)
    response = client.delete(f"{API}/notes/{versions[2]}", params={"version_only": True})
    assert (response.status_code, response.json()) == (409, {"detail": EARLIER_VERSIONS})
    assert note_documents(factory) == set(versions)


def test_version_only_forget_of_an_only_version_is_allowed(client, factory, settings):
    v1 = import_note(client, factory, settings)
    assert client.delete(f"{API}/notes/{v1}", params={"version_only": True}).status_code == 204
    assert note_documents(factory) == set()


def test_whole_note_forget_after_root_was_forgotten_alone(client, factory, settings):
    v1, v2, v3 = three_versions(client, factory, settings)
    client.delete(f"{API}/notes/{v1}", params={"version_only": True})
    assert client.delete(f"{API}/notes/{v2}").status_code == 204
    assert note_documents(factory) == set()


def test_whole_note_forget_unknown_and_article_are_404(client, factory, settings):
    assert client.delete(f"{API}/notes/00000000-0000-4000-8000-000000000000").status_code == 404
    response = client.delete(f"{API}/notes/nope", params={"version_only": True})
    assert response.status_code == 404


def test_forget_waiting_on_a_publishing_edit_also_forgets_the_new_version_on_postgresql(
    client, factory, settings
):
    """A version committed while forget waits for the lineage locks is found and forgotten."""
    require_postgres(factory)
    v1 = import_note(client, factory, settings)
    v2 = edit_and_run(client, factory, settings, v1)
    edit(client, v2, EDITED_AGAIN)
    claim = claim_job(factory, settings.job_lease_seconds)
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

    def after_worker_lock(conn, cursor, statement, parameters, *args):
        values = parameters.values() if isinstance(parameters, dict) else parameters
        if fired or "FOR UPDATE" not in statement or "FROM documents" not in statement:
            return
        if v2 not in tuple(values) or threading.current_thread() is thread:
            return
        fired.append(1)  # The worker holds v2 (the replaced version) until it publishes v3.
        thread.start()
        assert wait_for_lock_waiter(engine.url.render_as_string(hide_password=False))

    event.listen(engine, "after_cursor_execute", after_worker_lock)
    try:
        process_job(factory, settings, claim, model=FixtureModel())
    finally:
        event.remove(engine, "after_cursor_execute", after_worker_lock)
    thread.join(timeout=30)
    assert fired, "the worker never locked the replaced version"
    assert results["forget"] == 204, results
    assert note_documents(factory) == set()
    assert count(factory, Insight) == 0


def test_overlapping_whole_note_forgets_with_a_publish_do_not_deadlock_on_postgresql(
    client, factory, settings, monkeypatch
):
    """Forget A waits on the version an edit is replacing; the edit publishes a lower-id version;
    forget B starts. Without per-note serialisation B locks the new version and waits on A,
    while A's second pass waits on B (a deadlock)."""
    require_postgres(factory)
    from fastapi import HTTPException

    from open_learning import services

    planned = iter(f"00000000-0000-4000-8000-00000000000{n}" for n in (2, 3, 1))
    real_ident = services.ident

    def ordered_ident():
        caller = __import__("inspect").stack()[1].function
        return next(planned) if caller == "_publish" else real_ident()

    monkeypatch.setattr(services, "ident", ordered_ident)
    v1 = import_note(client, factory, settings)
    v2 = edit_and_run(client, factory, settings, v1)
    edit(client, v2, EDITED_AGAIN)
    claim = claim_job(factory, settings.job_lease_seconds)
    engine = factory.kw["bind"]
    url = engine.url.render_as_string(hide_password=False)
    results, fired = {}, set()

    def forget(name, document_id):
        def target():
            with factory() as db:
                try:
                    forget_note(db, document_id)
                    results[name] = 204
                except HTTPException as error:
                    results[name] = error.status_code
                except Exception as error:  # a deadlock surfaces here
                    results[name] = repr(error)

        return threading.Thread(target=target, name=name)

    forget_a, forget_b = forget("a", v1), forget("b", v2)

    def hook(conn, cursor, statement, parameters, *args):
        values = tuple(parameters.values() if isinstance(parameters, dict) else parameters)
        current = threading.current_thread()
        if "FOR UPDATE" not in statement or "FROM documents" not in statement:
            return
        if current is forget_a and "a" not in fired:
            fired.add("a")  # A holds v1 and v2 now; v3 was published while it waited.
            forget_b.start()
            assert wait_for_lock_waiter(url)
        elif current not in (forget_a, forget_b) and v2 in values and "worker" not in fired:
            fired.add("worker")  # The worker holds v2 until it publishes v3.
            forget_a.start()
            assert wait_for_lock_waiter(url)

    event.listen(engine, "after_cursor_execute", hook)
    try:
        process_job(factory, settings, claim, model=FixtureModel())
        forget_a.join(timeout=30)
        forget_b.join(timeout=30)
    finally:
        event.remove(engine, "after_cursor_execute", hook)
    assert fired == {"a", "worker"}
    assert results["a"] == 204 and results["b"] in (204, 404), results
    assert note_documents(factory) == set()


def test_forget_clears_forgotten_ids_from_edit_jobs(client, factory, settings):
    v1 = import_note(client, factory, settings)
    publishing = edit(client, v1).json()["id"]
    stale = edit(client, v1, EDITED + MARKER).json()["id"]
    from test_note_versions import run_job

    run_job(factory, settings)
    run_job(factory, settings)  # The second edit of v1 fails stale.
    with factory() as db:
        v2 = db.get(Job, publishing).document_id
    pending = edit(client, v2, EDITED_AGAIN).json()["id"]
    assert client.delete(f"{API}/notes/{v1}", params={"version_only": True}).status_code == 204
    with factory() as db:
        assert db.get(Job, publishing).replaces_document_id is None  # Completed, v1 forgotten.
        assert db.get(Job, stale).replaces_document_id is None  # Failed edit of v1.
        assert db.get(Job, pending).replaces_document_id == v2  # v2 still exists.
    assert client.delete(f"{API}/notes/{v2}").status_code == 204
    with factory() as db:
        job = db.get(Job, pending)
        assert (job.status, job.replaces_document_id) == ("cancelled", None)
        assert db.scalar(select(Job.id).where(Job.replaces_document_id.is_not(None))) is None
