"""Persistent discovery bounds and transactional metadata retention."""

from datetime import timedelta

import pytest
from conftest import FixtureModel, article_fetcher
from sqlalchemy import event, select

from open_learning import discovery
from open_learning.feed_fetching import FeedEntry, FeedError, FeedResponse, ParsedFeed
from open_learning.models import DiscoveredItem, Document, Feed, Job, Source, now
from open_learning.services import claim_job, process_job


def response(*urls):
    return FeedResponse(
        ParsedFeed("Learning", tuple(FeedEntry(url, "Title", None, "Snippet") for url in urls))
    )


def add_feed(factory, suffix="one"):
    with factory.begin() as db:
        feed = Feed(url=f"https://example.com/feed/{suffix}")
        db.add(feed)
        db.flush()
        return feed.id


def add_item(db, feed_id, suffix, **kwargs):
    item = DiscoveredItem(
        feed_id=feed_id, url=f"https://example.com/{suffix}", title="Title", snippet="", **kwargs
    )
    db.add(item)
    return item


def stored(factory, feed_id):
    with factory() as db:
        return list(db.scalars(select(DiscoveredItem).where(DiscoveredItem.feed_id == feed_id)))


def test_rotating_refreshes_keep_only_newest_200(factory, settings, monkeypatch):
    feed_id = add_feed(factory)
    for batch in range(6):
        urls = tuple(f"https://example.com/{batch}/{index}" for index in range(50))
        monkeypatch.setattr(discovery, "fetch_feed", lambda *a, urls=urls, **k: response(*urls))
        discovery.refresh_feed(factory, settings, feed_id)
        rows = stored(factory, feed_id)
        assert len(rows) == min((batch + 1) * 50, 200)
        assert set(urls) <= {row.url for row in rows}
    assert {row.url for row in rows} == {
        f"https://example.com/{batch}/{index}" for batch in range(2, 6) for index in range(50)
    }


def test_configured_cap_uses_created_time_then_id_and_isolates_feeds(
    factory, settings, monkeypatch
):
    monkeypatch.setattr(discovery, "MAX_STORED_ITEMS", 3, raising=False)
    feed_id, other_id = add_feed(factory), add_feed(factory, "two")
    stamp = now() - timedelta(days=1)
    with factory.begin() as db:
        for index in range(4):
            add_item(db, feed_id, f"tie-{index}", id=str(index), created_at=stamp)
            add_item(db, other_id, f"other-{index}", created_at=stamp)
        add_item(db, feed_id, "old-high-id", id="z", created_at=stamp - timedelta(days=1))
    monkeypatch.setattr(discovery, "fetch_feed", lambda *a, **k: FeedResponse(None))
    discovery.refresh_feed(factory, settings, feed_id)
    assert {row.id for row in stored(factory, feed_id)} == {"1", "2", "3"}
    assert len(stored(factory, other_id)) == 4


@pytest.mark.parametrize("result", ["parsed", "unchanged", "failure"])
def test_terminal_expiry_preserves_new_and_recent_rows_without_same_refresh_resurrection(
    factory, settings, monkeypatch, result
):
    feed_id = add_feed(factory)
    stamp = now()
    monkeypatch.setattr(discovery, "now", lambda: stamp)
    with factory.begin() as db:
        for status in ("new", "dismissed", "ingested"):
            add_item(
                db, feed_id, f"old-{status}", status=status, created_at=stamp - timedelta(days=31)
            )
            add_item(
                db,
                feed_id,
                f"recent-{status}",
                status=status,
                created_at=stamp - timedelta(days=29),
            )
        add_item(db, feed_id, "boundary", status="dismissed", created_at=stamp - timedelta(days=30))

    def fetch(*args, **kwargs):
        if result == "failure":
            raise FeedError("feed_connection_failed")
        if result == "unchanged":
            return FeedResponse(None)
        return response(
            "https://example.com/old-dismissed",
            "https://example.com/old-ingested",
            "https://example.com/fresh",
        )

    monkeypatch.setattr(discovery, "fetch_feed", fetch)
    discovery.refresh_feed(factory, settings, feed_id)
    expected = {"old-new", "recent-new", "recent-dismissed", "recent-ingested", "boundary"}
    if result == "parsed":
        expected.add("fresh")
    assert {row.url.rsplit("/", 1)[1] for row in stored(factory, feed_id)} == expected


def test_cap_preserves_retained_status_and_deduplicates_response(factory, settings, monkeypatch):
    monkeypatch.setattr(discovery, "MAX_STORED_ITEMS", 3, raising=False)
    feed_id = add_feed(factory)
    with factory.begin() as db:
        add_item(db, feed_id, "oldest", created_at=now() - timedelta(days=2))
        dismissed = add_item(db, feed_id, "dismissed", status="dismissed")
        ingested = add_item(db, feed_id, "ingested", status="ingested")
    monkeypatch.setattr(
        discovery,
        "fetch_feed",
        lambda *a, **k: response(
            "https://example.com/dismissed",
            "https://example.com/ingested",
            "https://example.com/fresh",
            "https://example.com/fresh",
        ),
    )
    for _ in range(2):
        discovery.refresh_feed(factory, settings, feed_id)
    rows = stored(factory, feed_id)
    assert {row.url.rsplit("/", 1)[1] for row in rows} == {"dismissed", "ingested", "fresh"}
    assert {(row.id, row.status) for row in rows if row.status != "new"} == {
        (dismissed.id, "dismissed"),
        (ingested.id, "ingested"),
    }


def test_pruning_ingested_metadata_preserves_queue_and_documents(
    client, factory, settings, monkeypatch
):
    feed_id = add_feed(factory)
    with factory.begin() as db:
        first = add_item(db, feed_id, "first")
        second = add_item(db, feed_id, "second")
    for item in (first, second):
        assert client.post(f"/api/v1/discover/{item.id}/ingest").status_code == 202
    claim = claim_job(factory, settings.job_lease_seconds)
    process_job(factory, settings, claim, article_fetcher, FixtureModel())
    with factory.begin() as db:
        for item in db.scalars(select(DiscoveredItem)):
            item.created_at = now() - timedelta(days=31)
        document_ids = set(db.scalars(select(Document.id)))
        source_ids = set(db.scalars(select(Source.id)))
        jobs = {job.id: (job.url, job.status, job.document_id) for job in db.scalars(select(Job))}
    monkeypatch.setattr(discovery, "fetch_feed", lambda *a, **k: FeedResponse(None))
    discovery.refresh_feed(factory, settings, feed_id)
    assert stored(factory, feed_id) == []
    with factory() as db:
        assert set(db.scalars(select(Document.id))) == document_ids
        assert set(db.scalars(select(Source.id))) == source_ids
        assert {
            job.id: (job.url, job.status, job.document_id) for job in db.scalars(select(Job))
        } == jobs
    claim = claim_job(factory, settings.job_lease_seconds)
    assert claim is not None
    process_job(factory, settings, claim, article_fetcher, FixtureModel())
    with factory() as db:
        assert all(job.status == "completed" for job in db.scalars(select(Job)))
        assert set(db.scalars(select(Source.url))) == {
            "https://example.com/first",
            "https://example.com/second",
        }


@pytest.mark.parametrize("operation", ["INSERT", "DELETE"])
def test_refresh_rolls_back_items_pruning_and_feed_metadata_on_database_failure(
    factory, settings, monkeypatch, operation
):
    feed_id = add_feed(factory)
    with factory.begin() as db:
        old = add_item(
            db, feed_id, "old", status="dismissed", created_at=now() - timedelta(days=31)
        )
    monkeypatch.setattr(
        discovery, "fetch_feed", lambda *a, **k: response("https://example.com/fresh")
    )
    engine = factory.kw["bind"]

    def fail(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith(operation) and "discovered_items" in statement:
            raise RuntimeError("injected database failure")

    event.listen(engine, "after_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError, match="injected database failure"):
            discovery.refresh_feed(factory, settings, feed_id)
    finally:
        event.remove(engine, "after_cursor_execute", fail)
    assert {row.id for row in stored(factory, feed_id)} == {old.id}
    with factory() as db:
        feed = db.get(Feed, feed_id)
        assert feed.last_fetched_at is None and feed.title == ""
