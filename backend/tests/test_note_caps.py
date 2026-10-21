import threading
from datetime import timedelta

import pytest
from conftest import PASSAGE, TOKEN, FixtureModel
from pydantic import ValidationError
from sqlalchemy import event, func, select

from open_learning.config import Settings
from open_learning.forget import purge_note_payloads
from open_learning.models import Job, now
from open_learning.worker import run_once

API = "/api/v1"
NOTE = (PASSAGE * 3).strip()
EXPIRED = "Note text expired; submit it again"


def add_job(factory, job_id, status="failed", text=NOTE, age_hours=0, kind="note"):
    note = kind == "note"
    with factory.begin() as db:
        db.add(
            Job(
                id=job_id,
                kind=kind,
                url=None if note else "https://a.example/" + job_id,
                status=status,
                payload_title="t" if note else None,
                payload_text=text if note else None,
                payload_label=None,
                created_at=now() - timedelta(hours=age_hours),
            )
        )


def job_count(factory):
    with factory() as db:
        return db.scalar(select(func.count()).select_from(Job))


def payload_ids(factory):
    with factory() as db:
        return set(db.scalars(select(Job.id).where(Job.payload_text.is_not(None))))


def post(client, text=NOTE):
    return client.post(API + "/ingest/text", json={"title": "n", "text": text})


@pytest.fixture
def small(settings):
    settings.note_max_pending_jobs = 2
    return settings


def test_pending_cap_429_with_retry_after_and_nothing_stored(client, factory, small):
    assert post(client).status_code == 202
    add_job(factory, "r1", status="running")
    before = job_count(factory)
    response = post(client, NOTE + " more")
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "30"
    assert "Too many notes" in response.json()["detail"]
    assert NOTE not in response.text
    assert job_count(factory) == before


def test_url_jobs_do_not_count_and_drain_reopens(client, factory, small):
    add_job(factory, "u1", status="queued", kind="url")
    add_job(factory, "q1", status="queued")
    assert post(client).status_code == 202
    assert post(client, NOTE + " b").status_code == 429
    with factory.begin() as db:
        db.get(Job, "q1").status = "completed"
    assert post(client, NOTE + " b").status_code == 202


def test_retry_blocked_at_cap(client, factory, small):
    add_job(factory, "f1")
    add_job(factory, "q1", status="queued")
    add_job(factory, "q2", status="queued")
    response = client.post(f"{API}/jobs/f1/retry")
    assert response.status_code == 429 and response.headers["Retry-After"] == "30"
    with factory() as db:
        assert db.get(Job, "f1").status == "failed"
    with factory.begin() as db:
        db.get(Job, "q2").status = "completed"
    assert client.post(f"{API}/jobs/f1/retry").status_code == 200


def test_purge_by_count_keeps_newest(factory, settings):
    settings.note_max_failed_payloads = 2
    for i, age in enumerate((5, 4, 3, 2)):
        add_job(factory, f"f{i}", age_hours=age)
    with factory.begin() as db:
        assert purge_note_payloads(db, settings) == 2
    assert payload_ids(factory) == {"f2", "f3"}
    with factory() as db:
        assert db.get(Job, "f0").error == EXPIRED and db.get(Job, "f0").payload_title is None


def test_purge_by_age(factory, settings):
    settings.note_failed_payload_hours = 2
    add_job(factory, "old", age_hours=3)
    add_job(factory, "new", age_hours=1)
    with factory.begin() as db:
        purge_note_payloads(db, settings)
    assert payload_ids(factory) == {"new"}


def test_purge_max_zero_and_only_failed_notes(factory, settings):
    settings.note_max_failed_payloads = 0
    add_job(factory, "f", age_hours=0)
    add_job(factory, "q", status="queued", age_hours=500)
    add_job(factory, "r", status="running", age_hours=500)
    add_job(factory, "u", status="failed", kind="url", age_hours=500)
    with factory.begin() as db:
        assert purge_note_payloads(db, settings) == 1
    assert payload_ids(factory) == {"q", "r"}
    with factory() as db:
        assert db.get(Job, "u").status == "failed"


def test_ingest_purges(client, factory, settings):
    settings.note_max_failed_payloads = 1
    add_job(factory, "a", age_hours=3)
    add_job(factory, "b", age_hours=2)
    assert post(client).status_code == 202
    ids = payload_ids(factory)
    assert "a" not in ids and "b" in ids


def test_worker_failure_and_idle_tick_purge(factory, settings):
    settings.note_max_failed_payloads = 1
    add_job(factory, "old", age_hours=3)
    add_job(factory, "q", status="queued")

    class Broken(FixtureModel):
        def insights(self, chunks):
            raise RuntimeError("boom")

    assert run_once(factory, settings, model=Broken())
    assert "old" not in payload_ids(factory)
    with factory() as db:
        assert db.get(Job, "q").status == "failed" and db.get(Job, "q").payload_text == NOTE
    settings.note_failed_payload_hours = 1
    add_job(factory, "older", age_hours=2)
    with factory.begin() as db:
        db.get(Job, "q").status = "completed"
    assert run_once(factory, settings) is False
    assert "older" not in payload_ids(factory)


@pytest.mark.parametrize(
    "name,bad,good",
    [
        ("note_max_pending_jobs", (0, 1001), (1, 1000)),
        ("note_max_failed_payloads", (-1, 1001), (0, 1000)),
        ("note_failed_payload_hours", (0, 8761), (1, 8760)),
    ],
)
def test_settings_ranges(name, bad, good):
    for value in bad:
        with pytest.raises(ValidationError):
            Settings(api_token=TOKEN, **{name: value})
    for value in good:
        assert getattr(Settings(api_token=TOKEN, **{name: value}), name) == value


def test_settings_defaults_and_env(monkeypatch):
    s = Settings(api_token=TOKEN)
    assert (s.note_max_pending_jobs, s.note_max_failed_payloads, s.note_failed_payload_hours) == (
        20,
        20,
        168,
    )
    monkeypatch.setenv("OL_NOTE_MAX_PENDING_JOBS", "7")
    monkeypatch.setenv("OL_NOTE_MAX_FAILED_PAYLOADS", "3")
    monkeypatch.setenv("OL_NOTE_FAILED_PAYLOAD_HOURS", "12")
    s = Settings(api_token=TOKEN)
    assert (s.note_max_pending_jobs, s.note_max_failed_payloads, s.note_failed_payload_hours) == (
        7,
        3,
        12,
    )


def require_postgres(factory):
    if factory.kw["bind"].dialect.name != "postgresql":
        pytest.skip("Advisory-lock races require OL_TEST_DATABASE_URL PostgreSQL")


def test_concurrent_ingest_respects_cap_on_postgresql(client, factory, settings):
    require_postgres(factory)
    settings.note_max_pending_jobs = 1
    barrier = threading.Barrier(2)
    reached = []

    @event.listens_for(factory.kw["bind"], "after_cursor_execute")
    def widen_window(conn, cursor, statement, *args):
        if statement.startswith("SELECT count(") and "jobs" in statement:
            reached.append(True)
            try:
                barrier.wait(timeout=1.5)  # the advisory lock keeps the second side out
            except threading.BrokenBarrierError:
                pass

    codes = []

    def submit(text):
        codes.append(post(client, text).status_code)

    threads = [threading.Thread(target=submit, args=(NOTE + str(i),)) for i in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(reached) == 2, "the count hook must have fired for both submissions"
    assert sorted(codes) == [202, 429]
    assert job_count(factory) == 1


def test_tutor_racing_delete_leaves_no_orphans_on_postgresql(client, factory, settings):
    require_postgres(factory)
    from test_note_forget import import_note, insight_of

    from open_learning.models import History, ReviewItem

    doc = import_note(client, factory, settings)
    insight = insight_of(factory, doc)
    barrier = threading.Barrier(2)

    class Gate(FixtureModel):
        def tutor(self, *args, **kwargs):
            barrier.wait(timeout=5)
            return super().tutor(*args, **kwargs)

    client.app.state.model = Gate()
    results = {}

    def tutor():
        results["tutor"] = client.post(
            f"{API}/tutor/chat", json={"insight_id": insight}
        ).status_code

    def delete():
        barrier.wait(timeout=5)
        results["delete"] = client.delete(f"{API}/notes/{doc}").status_code

    threads = [threading.Thread(target=tutor), threading.Thread(target=delete)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results["delete"] == 204 and results["tutor"] in (200, 404)
    with factory() as db:
        for model in (History, ReviewItem):
            assert db.scalar(select(func.count()).select_from(model)) == 0
