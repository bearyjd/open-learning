from datetime import datetime, timedelta

import pytest
from conftest import FixtureModel, article_fetcher
from sqlalchemy import func, select

from open_learning.ai import ModelError
from open_learning.models import Document, Insight, Job, ReviewHistory, now
from open_learning.services import claim_job, process_job
from open_learning.worker import run_once

API = "/api/v1"


def ingest(client, factory, settings):
    response = client.post(API + "/ingest/url", json={"url": "https://example.com/article"})
    assert response.status_code == 202
    job = response.json()
    assert job["status"] == "queued"
    assert run_once(factory, settings, article_fetcher, FixtureModel())
    assert client.get(API + "/jobs/" + job["id"]).json()["status"] == "completed"
    return client.get(API + "/feed/today").json()[0]


def test_learning_loop(client, factory, settings):
    assert client.get(API + "/feed/today").json() == []
    insight = ingest(client, factory, settings)
    assert insight["evidence"] in article_fetcher("", settings).text
    assert insight["model"] == settings.ai_model
    assert insight["coverage"] == "excerpt"
    assert client.get(API + "/search", params={"q": "retrieval"}).json() == []
    saved = client.post(API + "/insights/" + insight["id"] + "/save")
    assert saved.json()["saved"]
    assert len(client.get(API + "/library").json()) == 1
    assert len(client.get(API + "/search", params={"q": "retrieval"}).json()) == 1
    assert client.get(API + "/search", params={"q": "%"}).json() == []
    assert client.post(API + "/insights/" + insight["id"] + "/save").status_code == 200
    tutor = client.post(API + "/tutor/chat", json={"insight_id": insight["id"], "message": ""})
    assert tutor.status_code == 200
    assert tutor.json()["citations"][0]["document_id"] == insight["document_id"]
    assert (
        client.post(
            API + "/tutor/chat",
            json={"insight_id": insight["id"], "message": "I should recall first."},
        ).status_code
        == 200
    )
    reviews = client.get(API + "/reviews/due").json()
    assert len(reviews) == 1
    result = client.post(
        API + "/reviews/" + reviews[0]["id"] + "/answer", json={"rating": "remembered"}
    )
    assert result.json()["interval_days"] == 3
    assert client.get(API + "/reviews/due").json() == []
    with factory() as db:
        history = db.scalar(select(ReviewHistory))
        assert history.rating == "remembered"
        assert history.due_at.replace(tzinfo=None) == datetime.fromisoformat(
            result.json()["due_at"]
        ).replace(tzinfo=None)
    assert (
        client.post(
            API + "/insights/" + insight["id"] + "/feedback", json={"action": "dismiss"}
        ).status_code
        == 204
    )
    assert client.get(API + "/feed/today").json() == []


@pytest.mark.parametrize(
    "path",
    [
        "/feed/today",
        "/library",
        "/search?q=memory",
        "/reviews/due",
        "/preferences",
        "/history",
        "/jobs/missing",
    ],
)
def test_auth_required(client, path):
    client.headers.pop("Authorization")
    assert client.get(API + path).status_code == 401


def test_unsafe_ingest_rejected(client):
    for url in [
        "http://127.0.0.1/",
        "file:///etc/passwd",
        "http://user:pass@example.com",
        "https://example.com:22/",
    ]:
        assert client.post(API + "/ingest/url", json={"url": url}).status_code == 422


def test_failed_job_retry_and_no_partial_writes(client, factory, settings):
    class BrokenModel:
        def insights(self, chunks):
            raise ModelError("Model unavailable or returned invalid structured output")

    job = client.post(API + "/ingest/url", json={"url": "https://example.com"}).json()
    assert run_once(factory, settings, article_fetcher, BrokenModel())
    failed = client.get(API + "/jobs/" + job["id"]).json()
    assert failed["status"] == "failed" and failed["error"]
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Document)) == 0
    assert client.post(API + "/jobs/" + job["id"] + "/retry").json()["status"] == "queued"
    assert run_once(factory, settings, article_fetcher, FixtureModel())
    assert client.post(API + "/jobs/" + job["id"] + "/retry").status_code == 409


def test_expired_lease_reclaims_and_stale_owner_cannot_complete(client, factory, settings):
    job = client.post(API + "/ingest/url", json={"url": "https://example.com"}).json()
    first = claim_job(factory, 300)
    assert claim_job(factory, 300) is None
    with factory.begin() as db:
        db.get(Job, job["id"]).lease_until = now() - timedelta(seconds=1)
    second = claim_job(factory, 300)
    assert second[2] != first[2]
    process_job(factory, settings, first, article_fetcher, FixtureModel())
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Insight)) == 0
    process_job(factory, settings, second, article_fetcher, FixtureModel())
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Insight)) == 1


def test_reingestion_idempotent(client, factory, settings):
    ingest(client, factory, settings)
    ingest(client, factory, settings)
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Insight)) == 1


def test_missing_resources(client):
    assert client.get(API + "/insights/missing").status_code == 404
    assert client.post(API + "/reviews/missing/answer", json={"rating": "easy"}).status_code == 404


@pytest.mark.parametrize(
    "rating,days", [("forgotten", 10 / 1440), ("difficult", 1), ("remembered", 3), ("easy", 7)]
)
def test_review_ratings_schedule_and_record_history(client, factory, settings, rating, days):
    insight = ingest(client, factory, settings)
    client.post(API + "/insights/" + insight["id"] + "/save")
    review = client.get(API + "/reviews/due").json()[0]
    response = client.post(API + "/reviews/" + review["id"] + "/answer", json={"rating": rating})
    assert response.status_code == 200
    assert response.json()["interval_days"] == pytest.approx(days)
    with factory() as db:
        history = db.scalar(select(ReviewHistory))
        assert history.rating == rating
        assert history.interval_days == pytest.approx(days)


def test_tutor_chat_returns_model_assessment_to_service_callers(client, factory, settings):
    from open_learning.models import Insight
    from open_learning.services import tutor_chat

    insight = ingest(client, factory, settings)

    class Assessing(FixtureModel):
        def tutor(self, passage, title, message, history, **kwargs):
            return (
                super()
                .tutor(passage, title, message, history, **kwargs)
                .model_copy(update={"assessment": "partially_correct"})
            )

    with factory() as db:
        result = tutor_chat(db, db.get(Insight, insight["id"]), "Recall.", Assessing())
    assert result["assessment"] == "partially_correct"


def test_review_payloads_carry_additive_document_id(client, factory, settings):
    insight = ingest(client, factory, settings)
    client.post(API + "/insights/" + insight["id"] + "/save")
    due = client.get(API + "/reviews/due").json()
    assert len(due) == 1
    assert due[0]["document_id"] == insight["document_id"]
    # Fields existing clients rely on stay present alongside the new one.
    assert {"id", "insight_id", "question", "due_at", "interval_days", "insight_title"} <= set(due[0])
    answered = client.post(API + "/reviews/" + due[0]["id"] + "/answer", json={"rating": "easy"})
    assert answered.status_code == 200
    assert answered.json()["document_id"] == insight["document_id"]
