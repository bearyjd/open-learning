"""Real database transactions exercise races; every network call is mocked."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest
from sqlalchemy import func, select
from test_discovery import add, response

from open_learning import discovery
from open_learning.feed_fetching import FeedError
from open_learning.models import DiscoveredItem, Feed, Job


@pytest.fixture(autouse=True)
def network(monkeypatch):
    monkeypatch.setattr(discovery, "fetch_feed", lambda *a, **k: response())


def together(function, count=2):
    with ThreadPoolExecutor(max_workers=count) as executor:
        return [
            result.result(timeout=20)
            for result in [executor.submit(function, i) for i in range(count)]
        ]


@pytest.mark.parametrize("duplicate", [False, True])
def test_concurrent_create_cap_and_duplicate(client, factory, monkeypatch, duplicate):
    if not duplicate:
        with factory.begin() as db:
            db.add_all(Feed(url=f"https://example.com/feed/{i}", title="T") for i in range(49))
    barrier = Barrier(2)

    def fetch(*a, **k):
        barrier.wait(timeout=10)
        return response()

    monkeypatch.setattr(discovery, "fetch_feed", fetch)
    results = together(
        lambda i: client.post(
            "/api/v1/feeds", json={"url": f"https://example.com/new/{0 if duplicate else i}"}
        )
    )
    assert sorted(result.status_code for result in results) == [201, 409]
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Feed)) == (1 if duplicate else 50)


def test_concurrent_refresh_deduplicates(client, factory, monkeypatch):
    feed = add(client)
    barrier = Barrier(2)

    def fetch(*a, **k):
        barrier.wait(timeout=10)
        return response(3)

    monkeypatch.setattr(discovery, "fetch_feed", fetch)
    results = together(lambda _: client.post(f"/api/v1/feeds/{feed['id']}/refresh"))
    assert [result.status_code for result in results] == [200, 200]
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(DiscoveredItem)) == 3


def test_concurrent_refresh_failure_counted_once(client, factory, monkeypatch):
    feed = add(client)
    barrier = Barrier(2)

    def fail(*a, **k):
        barrier.wait(timeout=10)
        raise FeedError("feed_connection_error")

    monkeypatch.setattr(discovery, "fetch_feed", fail)
    results = together(lambda _: client.post(f"/api/v1/feeds/{feed['id']}/refresh"))
    assert [result.status_code for result in results] == [200, 200]
    with factory() as db:
        assert db.get(Feed, feed["id"]).consecutive_failures == 1


def test_delete_and_ingest_leave_no_orphans(client, factory):
    feed = add(client)
    item = client.get("/api/v1/discover").json()["items"][0]
    barrier = Barrier(2)

    def mutate(i):
        barrier.wait(timeout=10)
        if i:
            return client.delete(f"/api/v1/feeds/{feed['id']}")
        return client.post(f"/api/v1/discover/{item['id']}/ingest")

    results = together(mutate)
    assert results[0].status_code in (202, 404) and results[1].status_code == 204
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(DiscoveredItem)) == 0
        assert db.scalar(select(func.count()).select_from(Job)) <= 1


def test_refresh_delete_does_not_resurrect(client, factory, monkeypatch):
    feed = add(client)
    entered, release = Event(), Event()

    def fetch(*a, **k):
        entered.set()
        assert release.wait(timeout=10)
        return response(3)

    monkeypatch.setattr(discovery, "fetch_feed", fetch)
    with ThreadPoolExecutor(max_workers=1) as executor:
        refreshed = executor.submit(client.post, f"/api/v1/feeds/{feed['id']}/refresh")
        assert entered.wait(timeout=10)
        try:
            assert client.delete(f"/api/v1/feeds/{feed['id']}").status_code == 204
        finally:
            release.set()
        assert refreshed.result(timeout=20).status_code == 404
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(DiscoveredItem)) == 0


@pytest.mark.parametrize("mixed", [False, True])
def test_concurrent_item_decisions_enqueue_at_most_once(client, factory, mixed):
    add(client)
    item = client.get("/api/v1/discover").json()["items"][0]
    barrier = Barrier(2)

    def decide(i):
        barrier.wait(timeout=10)
        action = "dismiss" if mixed and i else "ingest"
        return client.post(f"/api/v1/discover/{item['id']}/{action}")

    results = together(decide)
    codes = sorted(result.status_code for result in results)
    assert codes in ([202, 409], [204, 409])
    with factory() as db:
        jobs = db.scalar(select(func.count()).select_from(Job))
        status = db.get(DiscoveredItem, item["id"]).status
        assert jobs == (1 if status == "ingested" else 0)


def test_refresh_dismiss_preserves_decision_without_network_lock(client, factory, monkeypatch):
    feed = add(client)
    item = client.get("/api/v1/discover").json()["items"][0]
    entered, release = Event(), Event()

    def fetch(*a, **k):
        entered.set()
        assert release.wait(timeout=10)
        return response(3)

    monkeypatch.setattr(discovery, "fetch_feed", fetch)
    with ThreadPoolExecutor(max_workers=1) as executor:
        refreshed = executor.submit(client.post, f"/api/v1/feeds/{feed['id']}/refresh")
        assert entered.wait(timeout=10)
        try:
            assert client.post(f"/api/v1/discover/{item['id']}/dismiss").status_code == 204
        finally:
            release.set()
        assert refreshed.result(timeout=20).status_code == 200
    with factory() as db:
        assert db.get(DiscoveredItem, item["id"]).status == "dismissed"
        assert db.scalar(select(func.count()).select_from(DiscoveredItem)) == 3
