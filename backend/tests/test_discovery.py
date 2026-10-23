"""Discovery API and scheduling tests; every feed response is local."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, BrokenBarrierError

import pytest
from sqlalchemy import func, select

from open_learning import discovery
from open_learning.feed_fetching import FeedEntry, FeedError, FeedResponse, ParsedFeed
from open_learning.models import DiscoveredItem, Feed, Job, now
from open_learning.worker import run_once


def response(count=2):
    return FeedResponse(
        ParsedFeed(
            "Learning",
            tuple(
                FeedEntry(f"https://example.com/article/{i}", f"Title {i}", None, "Short snippet")
                for i in range(count)
            ),
        ),
        etag="tag-1",
        last_modified="Wed, 21 Oct 2026 00:00:00 GMT",
    )


@pytest.fixture(autouse=True)
def mock_network(monkeypatch):
    monkeypatch.setattr(discovery, "fetch_feed", lambda *a, **k: response())


def add(client, url="https://example.com/feed"):
    result = client.post("/api/v1/feeds", json={"url": url})
    assert result.status_code == 201, result.text
    return result.json()


def test_auth_validation_duplicate_cap(client, factory):
    for path in ("/api/v1/feeds", "/api/v1/discover"):
        assert client.get(path, headers={"Authorization": ""}).status_code == 401
    for url in (
        "file:///etc/passwd",
        "http://127.0.0.1/rss",
        "https://user:secret@example.com/rss",
    ):
        result = client.post("/api/v1/feeds", json={"url": url})
        assert result.status_code == 422
        assert url not in result.text
    add(client)
    assert client.post("/api/v1/feeds", json={"url": "https://example.com/feed"}).status_code == 409
    with factory.begin() as db:
        db.add_all(Feed(url=f"https://example.com/feed/{i}", title="T") for i in range(49))
    assert (
        client.post("/api/v1/feeds", json={"url": "https://example.com/overflow"}).status_code
        == 409
    )
    assert len(client.get("/api/v1/feeds").json()) == 50


def test_cap_checked_before_fetch_and_unicode_url_bound(client, factory, monkeypatch):
    calls = []
    monkeypatch.setattr(discovery, "fetch_feed", lambda *a, **k: calls.append(a) or response())
    result = client.post("/api/v1/feeds", json={"url": "https://example.com/" + "界" * 800})
    assert result.status_code == 422 and not calls
    with factory.begin() as db:
        db.add_all(Feed(url=f"https://example.com/feed/{i}", title="T") for i in range(50))
    result = client.post("/api/v1/feeds", json={"url": "https://example.com/overflow"})
    assert result.status_code == 409 and not calls


def test_cursor_ties_and_new_insert_keep_pages_stable(client, factory):
    feed = add(client)
    stamp = now()
    with factory.begin() as db:
        items = list(db.scalars(select(DiscoveredItem)))
        for item in items:
            item.created_at = stamp
        expected = sorted((item.id for item in items), reverse=True)
    first = client.get("/api/v1/discover?limit=1").json()
    assert first["items"][0]["id"] == expected[0]
    with factory.begin() as db:
        db.add(
            DiscoveredItem(
                feed_id=feed["id"], url="https://example.com/newer", title="New", snippet=""
            )
        )
    second = client.get(
        "/api/v1/discover", params={"limit": 1, "cursor": first["next_cursor"]}
    ).json()
    assert second["items"][0]["id"] == expected[1] and second["next_cursor"] is None


def test_initial_items_pagination_ingest_dismiss_delete(client, factory):
    feed = add(client)
    page = client.get("/api/v1/discover?limit=1").json()
    assert len(page["items"]) == 1 and page["next_cursor"]
    first = page["items"][0]
    second = client.get(
        "/api/v1/discover", params={"limit": 1, "cursor": page["next_cursor"]}
    ).json()
    assert len(second["items"]) == 1 and second["items"][0]["id"] != first["id"]
    assert second["next_cursor"] is None
    job = client.post(f"/api/v1/discover/{first['id']}/ingest")
    assert job.status_code == 202 and job.json()["status"] == "queued"
    assert client.post(f"/api/v1/discover/{first['id']}/ingest").status_code == 409
    other = second["items"][0]["id"]
    assert client.post(f"/api/v1/discover/{other}/dismiss").status_code == 204
    assert client.post(f"/api/v1/discover/{other}/ingest").status_code == 409
    assert client.get("/api/v1/discover").json()["items"] == []
    assert len(client.get("/api/v1/discover?status=ingested").json()["items"]) == 1
    assert len(client.get("/api/v1/discover?status=dismissed").json()["items"]) == 1
    with factory() as db:
        assert db.get(Job, job.json()["id"]).url == first["url"]
    assert client.delete(f"/api/v1/feeds/{feed['id']}").status_code == 204
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(DiscoveredItem)) == 0
    assert client.post(f"/api/v1/feeds/{feed['id']}/refresh").status_code == 404


def test_query_validation(client):
    for query in ("limit=101", "limit=0", "status=unknown", "cursor=broken", "cursor=" + "x" * 257):
        assert client.get("/api/v1/discover?" + query).status_code == 422


def test_mutation_auth_and_missing_ids(client):
    for path, body in (
        ("/api/v1/feeds", {"url": "https://example.com/feed"}),
        ("/api/v1/feeds/missing/refresh", None),
        ("/api/v1/discover/missing/ingest", None),
        ("/api/v1/discover/missing/dismiss", None),
    ):
        assert client.post(path, json=body, headers={"Authorization": ""}).status_code == 401
    assert client.delete("/api/v1/feeds/missing", headers={"Authorization": ""}).status_code == 401
    assert client.delete("/api/v1/feeds/missing").status_code == 404
    for path in (
        "/api/v1/feeds/missing/refresh",
        "/api/v1/discover/missing/ingest",
        "/api/v1/discover/missing/dismiss",
    ):
        assert client.post(path).status_code == 404


def test_dismiss_idempotent_and_ingested_conflicts(client):
    add(client)
    first, second = client.get("/api/v1/discover").json()["items"]
    for _ in range(2):
        assert client.post(f"/api/v1/discover/{first['id']}/dismiss").status_code == 204
    assert client.post(f"/api/v1/discover/{second['id']}/ingest").status_code == 202
    assert client.post(f"/api/v1/discover/{second['id']}/dismiss").status_code == 409


def test_initial_304_rejected(client, monkeypatch):
    monkeypatch.setattr(discovery, "fetch_feed", lambda *a, **k: FeedResponse(None))
    result = client.post("/api/v1/feeds", json={"url": "https://example.com/feed"})
    assert result.status_code == 422 and result.json()["detail"] == "feed_fetch_failed"


def test_scheduler_default_bounds_and_jitter(settings):
    from open_learning.config import Settings

    assert settings.feed_refresh_interval_seconds == 21600
    feed = Feed(
        id="00000000-0000-0000-0000-000000000000",
        url="https://example.com/",
        consecutive_failures=0,
    )
    delay = discovery.refresh_delay(feed, settings)
    assert 21600 * 0.9 <= delay <= 21600 * 1.1
    assert discovery.refresh_delay(feed, settings) == delay
    for value in (0, -1, 604801):
        with pytest.raises(ValueError):
            Settings(api_token=settings.api_token, feed_refresh_interval_seconds=value)


def test_refresh_dedupe_preserves_status_and_conditionals(client, monkeypatch, factory):
    feed = add(client)
    item = client.get("/api/v1/discover").json()["items"][0]
    client.post(f"/api/v1/discover/{item['id']}/dismiss")
    calls = []

    def fetch(*args, **kwargs):
        calls.append(kwargs)
        return response()

    monkeypatch.setattr(discovery, "fetch_feed", fetch)
    assert client.post(f"/api/v1/feeds/{feed['id']}/refresh").status_code == 200
    assert calls == [{"etag": "tag-1", "last_modified": "Wed, 21 Oct 2026 00:00:00 GMT"}]
    assert len(client.get("/api/v1/discover").json()["items"]) == 1
    monkeypatch.setattr(discovery, "fetch_feed", lambda *a, **k: FeedResponse(None))
    assert client.post(f"/api/v1/feeds/{feed['id']}/refresh").status_code == 200
    with factory() as db:
        assert db.get(Feed, feed["id"]).etag == "tag-1"
        assert db.scalar(select(func.count()).select_from(DiscoveredItem)) == 2


def test_refresh_failure_backoff_pause_recovery(client, monkeypatch, factory, settings):
    feed = add(client)

    def fail(*args, **kwargs):
        raise FeedError("feed_connection_failed")

    monkeypatch.setattr(discovery, "fetch_feed", fail)
    delays = []
    for number in range(1, 11):
        refreshed = client.post(f"/api/v1/feeds/{feed['id']}/refresh")
        assert refreshed.status_code == 200
        assert refreshed.json()["consecutive_failures"] == number
        with factory() as db:
            row = db.get(Feed, feed["id"])
            delays.append(discovery.refresh_delay(row, settings))
    assert delays == sorted(delays) and delays[1] > delays[0]
    with factory() as db:
        row = db.get(Feed, feed["id"])
        assert not discovery.refresh_due(row, settings, at=now() + timedelta(days=365))
    assert client.get("/api/v1/feeds").json()[0]["paused"] is True
    monkeypatch.setattr(discovery, "fetch_feed", lambda *a, **k: response())
    result = client.post(f"/api/v1/feeds/{feed['id']}/refresh").json()
    assert result["consecutive_failures"] == 0 and result["last_error"] is None


def test_fetch_errors_fixed(client, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("secret body https://private.example/?token=password")

    monkeypatch.setattr(discovery, "fetch_feed", fail)
    failed = client.post("/api/v1/feeds", json={"url": "https://example.com/feed"})
    assert failed.status_code == 422 and failed.json()["detail"] == "feed_fetch_failed"


def test_item_cap_and_validation(client, monkeypatch):
    entries = list(response(80).feed.items)
    entries.insert(0, FeedEntry("http://127.0.0.1/private", "private", None, "text"))
    monkeypatch.setattr(
        discovery, "fetch_feed", lambda *a, **k: FeedResponse(ParsedFeed("T", tuple(entries)))
    )
    add(client)
    items = client.get("/api/v1/discover?limit=100").json()["items"]
    assert len(items) == 50
    assert all(item["url"].startswith("https://example.com/") for item in items)


def test_atomic_ingest_validation_failure_keeps_new(client, factory):
    feed = add(client)
    with factory.begin() as db:
        item = DiscoveredItem(
            feed_id=feed["id"], url="http://127.0.0.1/private", title="Bad", snippet=""
        )
        db.add(item)
    assert client.post(f"/api/v1/discover/{item.id}/ingest").status_code == 422
    with factory() as db:
        assert db.get(DiscoveredItem, item.id).status == "new"
        assert db.scalar(select(func.count()).select_from(Job)) == 0


def test_worker_refreshes_with_busy_queue(client, factory, settings, monkeypatch):
    feed = add(client)
    with factory.begin() as db:
        row = db.get(Feed, feed["id"])
        row.last_fetched_at = now() - timedelta(days=2)
        db.add(Job(url="https://example.com/article"))
    calls = []
    monkeypatch.setattr(discovery, "fetch_feed", lambda *a, **k: calls.append(a) or response(3))
    monkeypatch.setattr("open_learning.worker.process_job", lambda *a, **k: None)
    assert run_once(factory, settings)
    assert len(calls) == 1
    assert len(client.get("/api/v1/discover").json()["items"]) == 3


def test_simultaneous_ingest_queues_once_on_sqlite(client, factory, monkeypatch):
    if factory.kw["bind"].dialect.name != "sqlite":
        pytest.skip("SQLite write serialization regression")
    from open_learning import discovery_api

    add(client)
    item = client.get("/api/v1/discover").json()["items"][0]
    enqueue = discovery_api.enqueue_url
    both_decided = Barrier(2)

    def delayed_enqueue(db, url):
        # Give a competing request time to reach the same decision. With proper
        # serialization it waits at the DB lock instead, then observes ingested.
        try:
            both_decided.wait(timeout=1)
        except BrokenBarrierError:
            pass
        return enqueue(db, url)

    monkeypatch.setattr(discovery_api, "enqueue_url", delayed_enqueue)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(client.post, f"/api/v1/discover/{item['id']}/ingest") for _ in range(2)
        ]
        results = [future.result(timeout=10) for future in futures]
    assert sorted(result.status_code for result in results) == [202, 409]
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 1
