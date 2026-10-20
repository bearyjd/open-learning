import threading
from datetime import timedelta

import pytest
from sqlalchemy import event, func, select
from test_note_forget import MARKER, NOTE, failed_note_job, import_note, insight_of

from open_learning.models import (
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
from open_learning.services import RowVanished, serialize_insight, serialize_review

API = "/api/v1"


def require_postgres(factory):
    if factory.kw["bind"].dialect.name != "postgresql":
        pytest.skip("Lock-order races require OL_TEST_DATABASE_URL PostgreSQL")


def total(factory, model):
    with factory() as db:
        return db.scalar(select(func.count()).select_from(model))


def run_race(factory, hooks, actions):
    """Run named actions concurrently; hooks maps name -> predicate(statement, parameters)."""
    barrier = threading.Barrier(2)
    waited = set()
    broken = []
    engine = factory.kw["bind"]

    def rendezvous(conn, cursor, statement, parameters, *args):
        # Requests run on portal threads, so match each side by statement and bound ids.
        for name, matches in hooks.items():
            if name in waited or not matches(
                statement,
                tuple(parameters.values() if isinstance(parameters, dict) else parameters),
            ):
                continue
            waited.add(name)
            try:
                barrier.wait(timeout=1.5)
            except threading.BrokenBarrierError:
                broken.append(name)
            return

    event.listen(engine, "after_cursor_execute", rendezvous)
    results = {}

    def wrap(name, action):
        def target():
            try:
                results[name] = action()
            except Exception:  # an unhandled server error surfaces as a 500
                results[name] = 500

        return threading.Thread(target=target, name=name)

    threads = [wrap(name, action) for name, action in actions.items()]
    try:
        [t.start() for t in threads]
        [t.join() for t in threads]
    finally:
        event.remove(engine, "after_cursor_execute", rendezvous)
    assert waited == set(hooks), f"hooks never fired: {set(hooks) - waited}"
    assert not broken, f"barrier broke waiting for the other side: {broken}"
    return results


def locks(statement, table):
    return "FOR UPDATE" in statement and f"FROM {table}" in statement


def test_review_answer_racing_note_delete_no_deadlock_on_postgresql(client, factory, settings):
    require_postgres(factory)
    doc = import_note(client, factory, settings)
    insight = insight_of(factory, doc)
    client.post(f"{API}/insights/{insight}/save")
    review = client.get(API + "/reviews/due").json()[0]["id"]
    results = run_race(
        factory,
        {
            # Both sides meet before taking any insight lock, so the lock order is what is tested.
            "answer": lambda s, p: "FROM review_items" in s and "FOR UPDATE" not in s and review in p,
            "delete": lambda s, p: "FOR UPDATE OF documents" in s and doc in p,
        },
        {
            "answer": lambda: (
                client.post(f"{API}/reviews/{review}/answer", json={"rating": "easy"}).status_code
            ),
            "delete": lambda: client.delete(f"{API}/notes/{doc}").status_code,
        },
    )
    assert results["delete"] == 204 and results["answer"] in (200, 404), results
    for model in (Insight, ReviewItem, ReviewHistory, Interaction):
        assert total(factory, model) == 0, model


def test_feedback_racing_note_delete_on_postgresql(client, factory, settings):
    require_postgres(factory)
    doc = import_note(client, factory, settings)
    insight = insight_of(factory, doc)
    results = run_race(
        factory,
        {
            "feedback": lambda s, p: locks(s, "insights") and insight in p,
            "delete": lambda s, p: "FOR UPDATE OF documents" in s and doc in p,
        },
        {
            "feedback": lambda: (
                client.post(
                    f"{API}/insights/{insight}/feedback", json={"action": "dismiss"}
                ).status_code
            ),
            "delete": lambda: client.delete(f"{API}/notes/{doc}").status_code,
        },
    )
    assert results["delete"] == 204 and results["feedback"] in (204, 404), results
    for model in (Insight, Interaction):
        assert total(factory, model) == 0, model


def test_save_racing_note_delete_on_postgresql(client, factory, settings):
    require_postgres(factory)
    doc = import_note(client, factory, settings)
    insight = insight_of(factory, doc)
    results = run_race(
        factory,
        {
            # Meet right after the first insight read, before the request can take its lock.
            "save": lambda s, p: "FROM insights" in s and insight in p,
            "delete": lambda s, p: "FOR UPDATE OF documents" in s and doc in p,
        },
        {
            "save": lambda: client.post(f"{API}/insights/{insight}/save").status_code,
            "delete": lambda: client.delete(f"{API}/notes/{doc}").status_code,
        },
    )
    assert results["delete"] == 204 and results["save"] in (200, 404), results
    for model in (Insight, Interaction, SavedItem, ReviewItem):
        assert total(factory, model) == 0, model


def test_tutor_racing_note_delete_on_postgresql(client, factory, settings):
    require_postgres(factory)
    doc = import_note(client, factory, settings)
    insight = insight_of(factory, doc)
    results = run_race(
        factory,
        {
            # Meet right after the first insight read, before the request can take its lock.
            "tutor": lambda s, p: "FROM insights" in s and insight in p,
            "delete": lambda s, p: "FOR UPDATE OF documents" in s and doc in p,
        },
        {
            "tutor": lambda: (
                client.post(f"{API}/tutor/chat", json={"insight_id": insight}).status_code
            ),
            "delete": lambda: client.delete(f"{API}/notes/{doc}").status_code,
        },
    )
    assert results["delete"] == 204 and results["tutor"] in (200, 404), results
    for model in (Insight, Interaction, History, ReviewItem):
        assert total(factory, model) == 0, model


def require_sqlite(factory):
    if factory.kw["bind"].dialect.name != "sqlite":
        pytest.skip("Simulates vanished rows; PostgreSQL foreign keys forbid them")


def remove_rows(factory, model, *where):
    with factory.begin() as db:
        for row in db.scalars(select(model).where(*where)).all():
            db.delete(row)


def test_tutor_with_missing_chunk_is_404(client, factory, settings):
    require_sqlite(factory)
    doc = import_note(client, factory, settings)
    insight = insight_of(factory, doc)
    remove_rows(factory, Chunk)
    response = client.post(f"{API}/tutor/chat", json={"insight_id": insight})
    assert response.status_code == 404


def test_serialize_helpers_raise_lookup_error_for_missing_rows(client, factory, settings):
    require_sqlite(factory)
    doc = import_note(client, factory, settings)
    insight_id = insight_of(factory, doc)
    client.post(f"{API}/insights/{insight_id}/save")
    with factory() as db:
        insight = db.get(Insight, insight_id)
        review = db.scalar(select(ReviewItem))
    remove_rows(factory, Document)
    with factory() as db:
        with pytest.raises(RowVanished):
            serialize_insight(db, insight)
    remove_rows(factory, Insight)
    with factory() as db:
        with pytest.raises(RowVanished):
            serialize_review(db, review)


def test_request_paths_survive_rows_deleted_underneath(client, factory, settings):
    require_sqlite(factory)
    doc = import_note(client, factory, settings)
    insight = insight_of(factory, doc)
    client.post(f"{API}/insights/{insight}/save")
    review = client.get(API + "/reviews/due").json()[0]["id"]
    remove_rows(factory, Document)
    assert client.get(f"{API}/insights/{insight}").status_code == 404
    assert client.post(f"{API}/insights/{insight}/save").status_code == 404
    for path in ("/feed/today", "/library", "/search?q=retrieval"):
        response = client.get(API + path)
        assert response.status_code == 200 and response.json() == [], path
    remove_rows(factory, Insight)
    assert client.get(API + "/reviews/due").json() == []
    assert client.post(f"{API}/reviews/{review}/answer", json={"rating": "easy"}).status_code == 404


def seed_notes(factory, count):
    base = now()
    with factory.begin() as db:
        for i in range(count):
            source = Source(url=f"note:{i:064d}", kind="note")
            db.add(source)
            db.flush()
            db.add(
                Document(
                    id=f"d{i:03d}",
                    source_id=source.id,
                    title=f"n{i}",
                    text="t",
                    content_hash=f"{i:064d}",
                    created_at=base - timedelta(minutes=i // 2),
                )
            )


def test_notes_pagination_order_and_bounds(client, factory):
    seed_notes(factory, 6)
    ids = [n["document_id"] for n in client.get(API + "/notes").json()]
    # Same created_at pairs are ordered id desc; newest first.
    assert ids == ["d001", "d000", "d003", "d002", "d005", "d004"]
    page = client.get(API + "/notes", params={"limit": 2, "offset": 2}).json()
    assert [n["document_id"] for n in page] == ["d003", "d002"]
    assert client.get(API + "/notes", params={"offset": 99}).json() == []
    for params in ({"limit": 0}, {"limit": 501}, {"offset": -1}):
        assert client.get(API + "/notes", params=params).status_code == 422


def test_notes_default_limit_is_100(client, factory):
    seed_notes(factory, 102)
    assert len(client.get(API + "/notes").json()) == 100
    assert len(client.get(API + "/notes", params={"limit": 500}).json()) == 102


def test_jobs_listing_exposes_only_safe_fields(client, factory):
    failed_note_job(factory, NOTE + MARKER, "j1")
    failed_note_job(factory, NOTE, "j2", status="queued")
    with factory.begin() as db:
        db.add(Job(id="u1", url="https://a.example/x"))
    response = client.get(API + "/jobs", params={"kind": "note"})
    assert response.status_code == 200
    body = response.json()
    assert {j["id"] for j in body} == {"j1", "j2"}
    for job in body:
        assert set(job) == {"id", "status", "created_at"}
    assert MARKER not in response.text and "https://a.example" not in response.text
    assert [
        j["id"] for j in client.get(API + "/jobs", params={"kind": "note", "limit": 1}).json()
    ] == [body[0]["id"]]
    assert client.get(API + "/jobs", params={"kind": "note", "offset": 5}).json() == []


def test_jobs_listing_validation_auth_and_delete_flow(client, factory):
    failed_note_job(factory, NOTE, "j1")
    assert client.get(API + "/jobs").status_code == 422
    assert client.get(API + "/jobs", params={"kind": "url"}).status_code == 422
    for params in ({"limit": 0}, {"limit": 501}, {"offset": -1}):
        assert client.get(API + "/jobs", params={"kind": "note", **params}).status_code == 422
    found = client.get(API + "/jobs", params={"kind": "note"}).json()[0]["id"]
    assert client.delete(f"{API}/jobs/{found}").status_code == 204
    assert client.get(API + "/jobs", params={"kind": "note"}).json() == []
    client.headers.pop("Authorization")
    assert client.get(API + "/jobs", params={"kind": "note"}).status_code == 401


def test_unexpected_lookup_errors_from_the_model_are_not_404(client, factory, settings):
    doc = import_note(client, factory, settings)
    insight = insight_of(factory, doc)

    class Broken:
        def tutor(self, *args):
            raise KeyError("model bug")

    client.app.state.model = Broken()
    with pytest.raises(KeyError):
        client.post(f"{API}/tutor/chat", json={"insight_id": insight})
