"""Superseded note versions are hidden from listings; note text and version attribution."""

from conftest import FixtureModel
from sqlalchemy import select
from test_note_forget import NOTE, import_note, url_job
from test_note_versions import EDITED, MARKER, edit_and_run

from open_learning.models import Insight
from open_learning.services import claim_job, process_job

API = "/api/v1"


def insight_ids(factory, document_id):
    with factory() as db:
        return list(db.scalars(select(Insight.id).where(Insight.document_id == document_id)))


def ids(response):
    assert response.status_code == 200, response.text
    return {item["id"] for item in response.json()}


def test_feed_hides_superseded_including_cached_feed(client, factory, settings):
    v1 = import_note(client, factory, settings)
    old = set(insight_ids(factory, v1))
    assert old <= ids(client.get(API + "/feed/today"))  # Cached in today's DailyFeed.
    v2 = edit_and_run(client, factory, settings, v1)
    feed = ids(client.get(API + "/feed/today"))
    assert feed.isdisjoint(old)
    assert set(insight_ids(factory, v2)) <= feed


def test_library_search_and_reviews_hide_superseded(client, factory, settings):
    v1 = import_note(client, factory, settings)
    (old,) = insight_ids(factory, v1)
    assert client.post(f"{API}/insights/{old}/save").status_code == 200
    with factory.begin() as db:
        from open_learning.models import ReviewItem, now

        db.execute(ReviewItem.__table__.update().values(due_at=now()))
    assert old in ids(client.get(API + "/library"))
    assert old in ids(client.get(API + "/search", params={"q": "Retrieval"}))
    assert old in {r["insight_id"] for r in client.get(API + "/reviews/due").json()}
    edit_and_run(client, factory, settings, v1)
    assert old not in ids(client.get(API + "/library"))
    assert old not in ids(client.get(API + "/search", params={"q": "Retrieval"}))
    assert old not in {r["insight_id"] for r in client.get(API + "/reviews/due").json()}


def test_superseded_insight_still_readable_by_id(client, factory, settings):
    v1 = import_note(client, factory, settings)
    (old,) = insight_ids(factory, v1)
    edit_and_run(client, factory, settings, v1)
    body = client.get(f"{API}/insights/{old}").json()
    assert body["document_id"] == v1 and body["source_version"] == 1


def test_insight_and_citation_carry_source_version(client, factory, settings):
    v1 = import_note(client, factory, settings)
    v2 = edit_and_run(client, factory, settings, v1)
    (new,) = insight_ids(factory, v2)
    assert client.get(f"{API}/insights/{new}").json()["source_version"] == 2
    tutor = client.post(API + "/tutor/chat", json={"insight_id": new, "message": "Why?"}).json()
    assert tutor["citations"][0]["document_id"] == v2
    assert tutor["citations"][0]["source_version"] == 2


def test_url_insights_report_version_one(client, factory, settings):
    url_job(factory, settings)
    (item,) = client.get(API + "/feed/today").json()
    assert item["source_version"] == 1


def test_note_text_returns_stored_text_no_store(client, factory, settings):
    text = NOTE + " " + MARKER
    v1 = import_note(client, factory, settings, text=text)
    response = client.get(f"{API}/notes/{v1}/text")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "document_id": v1,
        "title": "My note",
        "label": "cls",
        "version": 1,
        "text": text,
    }


def test_note_text_available_for_superseded_version(client, factory, settings):
    v1 = import_note(client, factory, settings)
    edit_and_run(client, factory, settings, v1)
    body = client.get(f"{API}/notes/{v1}/text").json()
    assert (body["text"], body["version"]) == (NOTE, 1)


def test_note_text_404_for_unknown_url_and_forgotten(client, factory, settings):
    assert client.get(f"{API}/notes/nope/text").status_code == 404
    assert client.get(f"{API}/notes/{'x' * 500}/text").status_code == 404
    article = url_job(factory, settings)
    response = client.get(f"{API}/notes/{article}/text")
    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"
    v1 = import_note(client, factory, settings)
    client.delete(f"{API}/notes/{v1}")
    assert client.get(f"{API}/notes/{v1}/text").status_code == 404


def test_note_text_requires_auth(client, factory, settings):
    v1 = import_note(client, factory, settings)
    client.headers.pop("Authorization")
    assert client.get(f"{API}/notes/{v1}/text").status_code == 401


def test_stale_edit_racing_forget_is_not_published(client, factory, settings):
    """A forget committed while the model ran leaves the edit job cancelled, nothing published."""
    v1 = import_note(client, factory, settings)
    response = client.post(
        API + "/ingest/text",
        json={"title": "t", "text": EDITED, "replaces_document_id": v1},
    )
    claim = claim_job(factory, settings.job_lease_seconds)

    class Forgetting(FixtureModel):
        def insights(self, chunks):
            assert client.delete(f"{API}/notes/{v1}").status_code == 204
            return super().insights(chunks)

    process_job(factory, settings, claim, model=Forgetting())
    assert client.get(f"{API}/jobs/{response.json()['id']}").json()["status"] == "cancelled"
    assert client.get(API + "/notes", params={"include_superseded": True}).json() == []
