"""Hardening of the note ingest path: body caps, error scrubbing, validation, engine, leases."""

import asyncio
import json
import logging

import pytest
from conftest import TOKEN, FixtureModel
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from test_notes import API, MARKER, NOTE, never_fetch, post_note

from open_learning import services
from open_learning.main import create_app
from open_learning.models import Document, Job
from open_learning.services import claim_job, process_job

NOTE_PATH = API + "/ingest/text"
JSON = {"content-type": "application/json"}
SMALL_CAP = 64 * 1024


def note_cap(settings):
    return 12 * settings.max_document_chars + 64 * 1024


def asgi_post(app, chunk_size, chunk_count, headers):
    """Drive the real app over raw ASGI (TestClient buffers whole bodies, hiding memory use).

    Returns (status, body, chunks_pulled_by_the_app).
    """
    pulled = 0
    sent = []

    async def receive():
        nonlocal pulled
        if pulled >= chunk_count:
            return {"type": "http.request", "body": b"", "more_body": False}
        pulled += 1
        return {"type": "http.request", "body": b"a" * chunk_size, "more_body": True}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
        "scheme": "http", "path": NOTE_PATH, "raw_path": NOTE_PATH.encode(), "root_path": "",
        "query_string": b"", "server": ("t", 80), "client": ("c", 1),
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers],
    }
    asyncio.run(app(scope, receive, send))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    return status, b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body"), pulled


@pytest.fixture
def app(client):
    return client.app


# A. error scrubbing behind a root_path ------------------------------------------------


@pytest.fixture
def rooted(factory, settings):
    app = create_app(settings, factory, FixtureModel())
    with TestClient(app, root_path="/ol") as client:
        client.headers["Authorization"] = "Bearer " + TOKEN
        yield client


@pytest.mark.parametrize(
    ("content", "headers"),
    [
        (f'{{"title":"t","text":"{MARKER}{"x" * 120}"}}', {"content-type": "text/plain"}),
        (f'{{"title":"t","text":"{NOTE}","label":["{MARKER}"]}}', JSON),
        (f'{{"title":"t","text":"{NOTE}","extra":"{MARKER}"}}', JSON),
    ],
)
def test_validation_errors_never_echo_note_behind_root_path(rooted, content, headers):
    response = rooted.post("/ol" + NOTE_PATH, content=content, headers=headers)
    assert response.status_code == 422
    assert MARKER not in response.text


# B. end-to-end 413 ---------------------------------------------------------------------


AUTH = ("authorization", "Bearer " + TOKEN)
JSON_HEADER = ("content-type", "application/json")


@pytest.mark.parametrize("auth", [[AUTH], []], ids=["token", "anonymous"])
def test_chunked_body_over_cap_is_413_and_bounded(app, settings, auth):
    chunk = 1024 * 1024
    status, body, pulled = asgi_post(app, chunk, 400, [JSON_HEADER, *auth])
    assert status == 413
    assert json.loads(body) == {"detail": "Request body too large"}
    assert pulled * chunk <= note_cap(settings) + chunk  # stopped at the first chunk over cap


def test_lying_content_length_is_413_and_bounded(app):
    status, _, pulled = asgi_post(app, 1024 * 1024, 400, [JSON_HEADER, AUTH, ("content-length", "10")])
    assert status == 413
    assert pulled < 5


def test_conflicting_content_lengths_use_the_largest(app):
    headers = [JSON_HEADER, AUTH, ("content-length", "1"), ("content-length", str(10**9))]
    status, _, pulled = asgi_post(app, 10, 1, headers)
    assert (status, pulled) == (413, 0)


def test_http_client_gets_413_for_oversize_note(client, settings):
    body = b'{"title":"t","text":"' + b"a" * note_cap(settings) + b'"}'
    assert client.post(NOTE_PATH, content=body, headers=JSON).status_code == 413


# C. small default cap for other routes -------------------------------------------------


def test_other_routes_have_small_cap(client):
    big = b"x" * (SMALL_CAP + 1)
    assert client.post(API + "/tutor/chat", content=big, headers=JSON).status_code == 413
    assert client.post(API + "/ingest/url", content=big, headers=JSON).status_code == 413
    assert client.put(API + "/preferences", content=big, headers=JSON).status_code == 413


def test_normal_requests_fit_small_cap(client):
    tutor = {"insight_id": "x", "message": "é" * 4000}
    assert client.post(API + "/tutor/chat", json=tutor).status_code != 413
    escaped = {"insight_id": "x", "message": '"' * 4000}  # worst-case JSON escaping
    assert client.post(API + "/tutor/chat", json=escaped).status_code != 413
    assert client.post(API + "/insights/x/feedback", json={"action": "dismiss"}).status_code != 413
    assert client.post(API + "/ingest/url", json={"url": "https://a.example/" + "p" * 2000}).status_code == 202


def test_note_route_keeps_large_cap_and_root_path(rooted, settings):
    text = "q" * settings.max_document_chars
    assert rooted.post("/ol" + NOTE_PATH, json={"title": "t", "text": text}).status_code == 202


# D. unicode validation -----------------------------------------------------------------

LINE_BREAKS = [" ", " "]
BIDI = ["‪", "‫", "‬", "‭", "‮", "⁦", "⁧", "⁨", "⁩"]


@pytest.mark.parametrize("bad", LINE_BREAKS + BIDI)
@pytest.mark.parametrize("field", ["title", "label"])
def test_single_line_fields_reject_separators_and_bidi(client, field, bad):
    response = post_note(client, **{field: "qzx" + bad + "wvu"})
    assert response.status_code == 422
    assert "qzx" not in response.text


@pytest.mark.parametrize("bad", BIDI)
def test_text_rejects_bidi_controls(client, bad):
    response = post_note(client, text=NOTE + bad + MARKER)
    assert response.status_code == 422
    assert MARKER not in response.text


@pytest.mark.parametrize("sep", LINE_BREAKS)
def test_text_allows_unicode_paragraph_separators(client, sep):
    assert post_note(client, text=NOTE + sep + "more").status_code == 202


def test_zwj_emoji_and_zero_width_still_accepted(client):
    family = "\U0001f469‍\U0001f469‍\U0001f467"
    assert post_note(client, title="Family " + family, label=family, text=NOTE + family).status_code == 202


# E. engine hides parameters ------------------------------------------------------------


def test_engine_hides_parameters_and_failures_leak_no_note_text(tmp_path, caplog):
    from sqlalchemy.exc import IntegrityError

    from open_learning.database import session_factory
    from open_learning.models import Base

    factory = session_factory("sqlite:///" + str(tmp_path / "e.db"))
    Base.metadata.create_all(factory.kw["bind"])
    assert factory.kw["bind"].hide_parameters is True
    caplog.set_level(logging.DEBUG)
    with pytest.raises(IntegrityError) as caught:
        with factory.begin() as db:  # kind violates ck_jobs_kind_payload
            db.add(Job(kind="url", url="https://a.example/x", payload_text=MARKER))
    assert MARKER not in str(caught.value)
    assert MARKER not in caplog.text


# H. lease loss on the duplicate path ---------------------------------------------------


def test_stale_lease_owner_cannot_complete_on_duplicate_path(client, factory, settings, monkeypatch):
    post_note(client)
    process_job(factory, settings, claim_job(factory, settings.job_lease_seconds),
                fetcher=never_fetch, model=FixtureModel())
    post_note(client, title="Again")  # same text: duplicate branch
    claim = claim_job(factory, settings.job_lease_seconds)
    real = services._note_imported

    def reclaimed_then_check(db, key, digest):
        with factory.begin() as other:  # Lease is reclaimed after the payload was loaded.
            other.get(Job, claim[0]).lease_token = "new-owner"
        return real(db, key, digest)

    monkeypatch.setattr(services, "_note_imported", reclaimed_then_check)
    process_job(factory, settings, claim, fetcher=never_fetch, model=FixtureModel())
    with factory() as db:
        job = db.get(Job, claim[0])
        assert job.status == "running"
        assert job.lease_token == "new-owner"
        assert job.payload_text is not None
        assert db.scalar(select(func.count()).select_from(Document)) == 1
