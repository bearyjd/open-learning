"""Discovery persistence and refresh scheduling; never fetch full articles here."""

import hashlib
from datetime import UTC, timedelta
from itertools import islice

from fastapi import HTTPException
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError

from .feed_fetching import FeedError, fetch_feed
from .fetching import FetchError, validate_url
from .models import DiscoveredItem, Feed, now

MAX_FEEDS = 50
MAX_ITEMS = 50
MAX_STORED_ITEMS = 200
ITEM_RETENTION_DAYS = 30
PAUSE_FAILURES = 10


def checked_url(url: str) -> str:
    try:
        result = validate_url(url)
        if len(result.encode("utf-8")) > 2048:
            raise FetchError("Invalid URL")
        return result
    except (FetchError, UnicodeError) as error:
        raise HTTPException(422, "feed_invalid_url") from error


def feed_output(feed: Feed) -> dict:
    return {
        "id": feed.id,
        "url": feed.url,
        "title": feed.title,
        "created_at": feed.created_at,
        "last_fetched_at": feed.last_fetched_at,
        "last_error": feed.last_error,
        "consecutive_failures": feed.consecutive_failures,
        "paused": feed.consecutive_failures >= PAUSE_FAILURES,
    }


def lock_sqlite_writes(db):
    # SELECT FOR UPDATE is ignored by SQLite. Acquire its writer reservation
    # before the first read used for a mutation, including the item's parent lookup.
    if db.bind.dialect.name == "sqlite":
        connection = db.connection()
        if not connection.connection.driver_connection.in_transaction:
            connection.exec_driver_sql("BEGIN IMMEDIATE")


def lock_feed(db, feed_id: str) -> Feed:
    lock_sqlite_writes(db)
    row = db.scalar(select(Feed).where(Feed.id == feed_id).with_for_update())
    if row is None:
        raise HTTPException(404, "Feed not found")
    return row


def _fetch(url, settings, **headers):
    try:
        return fetch_feed(url, settings, **headers)
    except FeedError:
        raise
    except Exception as error:
        raise FeedError("feed_fetch_failed") from error


def _store_items(db, feed, parsed):
    # Parent lock serializes writers, including deletion and user decisions. A refresh
    # never changes an existing item's status or resurrects a dismissed entry.
    candidates = {}
    for entry in islice(parsed.items, 100):
        try:
            url = checked_url(entry.url)
        except HTTPException:
            continue
        candidates.setdefault(url, entry)
        if len(candidates) == MAX_ITEMS:
            break
    existing = set(
        db.scalars(
            select(DiscoveredItem.url).where(
                DiscoveredItem.feed_id == feed.id, DiscoveredItem.url.in_(candidates)
            )
        )
    )
    for url, entry in candidates.items():
        if url in existing:
            continue
        db.add(
            DiscoveredItem(
                feed_id=feed.id,
                url=url,
                title=entry.title[:500],
                published_at=entry.published_at,
                snippet=entry.snippet[:500],
            )
        )


def _prune_items(db, feed):
    # Flush admission first: response URLs were deduplicated against all retained
    # rows, including expired terminal entries, before any metadata is removed.
    # Jobs and their published documents are independent of discovery metadata.
    db.flush()
    db.execute(
        delete(DiscoveredItem).where(
            DiscoveredItem.feed_id == feed.id,
            DiscoveredItem.status.in_(("dismissed", "ingested")),
            DiscoveredItem.created_at < now() - timedelta(days=ITEM_RETENTION_DAYS),
        )
    )
    newest = (
        select(DiscoveredItem.id)
        .where(DiscoveredItem.feed_id == feed.id)
        .order_by(DiscoveredItem.created_at.desc(), DiscoveredItem.id.desc())
        .limit(MAX_STORED_ITEMS)
    )
    db.execute(
        delete(DiscoveredItem).where(
            DiscoveredItem.feed_id == feed.id, DiscoveredItem.id.not_in(newest)
        )
    )


def create_feed(factory, settings, url: str) -> dict:
    url = checked_url(url)
    # No transaction or lock spans remote I/O. Recheck both constraints after fetch.
    with factory() as db:
        if db.scalar(select(Feed.id).where(Feed.url == url)) is not None:
            raise HTTPException(409, "Feed already subscribed")
        if db.scalar(select(func.count()).select_from(Feed)) >= MAX_FEEDS:
            raise HTTPException(409, "Feed subscription limit reached")
    try:
        result = _fetch(url, settings)
        if result.feed is None:
            raise FeedError("feed_invalid_response")
    except FeedError as error:
        raise HTTPException(422, str(error)) from error
    with factory.begin() as db:
        if db.bind.dialect.name == "postgresql":
            db.execute(text("SELECT pg_advisory_xact_lock(73240606)"))
        else:
            lock_sqlite_writes(db)
        if db.scalar(select(Feed.id).where(Feed.url == url)) is not None:
            raise HTTPException(409, "Feed already subscribed")
        if db.scalar(select(func.count()).select_from(Feed)) >= MAX_FEEDS:
            raise HTTPException(409, "Feed subscription limit reached")
        feed = Feed(
            url=url,
            title=result.feed.title[:500],
            last_fetched_at=now(),
            etag=result.etag,
            last_modified=result.last_modified,
        )
        db.add(feed)
        try:
            db.flush()
        except IntegrityError as error:
            raise HTTPException(409, "Feed already subscribed") from error
        _store_items(db, feed, result.feed)
        _prune_items(db, feed)
        return feed_output(feed)


def delete_feed(db, feed_id):
    feed = lock_feed(db, feed_id)
    # SQLite installations may not enable FK enforcement; delete explicitly too.
    db.execute(delete(DiscoveredItem).where(DiscoveredItem.feed_id == feed_id))
    db.delete(feed)
    db.commit()


def refresh_delay(feed, settings) -> float:
    # Stable jitter needs no schedule column and survives worker restarts.
    jitter = int(hashlib.sha256(feed.id.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    failures = min(feed.consecutive_failures, PAUSE_FAILURES)
    return settings.feed_refresh_interval_seconds * 2**failures * (0.9 + 0.2 * jitter)


def refresh_due(feed, settings, *, at=None) -> bool:
    if feed.consecutive_failures >= PAUSE_FAILURES:
        return False
    if feed.last_fetched_at is None:
        return True
    fetched_at = (
        feed.last_fetched_at.replace(tzinfo=UTC)
        if feed.last_fetched_at.tzinfo is None
        else feed.last_fetched_at
    )
    return (at or now()) >= fetched_at + timedelta(seconds=refresh_delay(feed, settings))


def refresh_feed(factory, settings, feed_id, *, scheduled=False) -> dict:
    with factory() as db:
        feed = db.get(Feed, feed_id)
        if feed is None:
            raise HTTPException(404, "Feed not found")
        if scheduled and not refresh_due(feed, settings):
            return feed_output(feed)
        snapshot = (feed.last_fetched_at, feed.consecutive_failures, feed.etag, feed.last_modified)
        url = feed.url
    result, failure = None, None
    try:
        result = _fetch(url, settings, etag=snapshot[2], last_modified=snapshot[3])
    except FeedError as error:
        failure = str(error)
    with factory.begin() as db:
        feed = lock_feed(db, feed_id)
        # Another refresh completed while this one fetched. Its newer result wins.
        if (
            feed.last_fetched_at,
            feed.consecutive_failures,
            feed.etag,
            feed.last_modified,
        ) != snapshot:
            return feed_output(feed)
        feed.last_fetched_at = now()
        if failure is not None:
            feed.last_error = failure
            feed.consecutive_failures += 1
        else:
            feed.last_error, feed.consecutive_failures = None, 0
            if result.feed is not None:
                feed.title = result.feed.title[:500]
                feed.etag, feed.last_modified = result.etag, result.last_modified
                _store_items(db, feed, result.feed)
            else:
                feed.etag = result.etag or feed.etag
                feed.last_modified = result.last_modified or feed.last_modified
        _prune_items(db, feed)
        return feed_output(feed)


def refresh_due_feed(factory, settings) -> bool:
    # One bounded network operation per article iteration prevents either queue starving.
    with factory() as db:
        feeds = list(
            db.scalars(
                select(Feed)
                .where(Feed.consecutive_failures < PAUSE_FAILURES)
                .order_by(Feed.last_fetched_at.asc().nullsfirst(), Feed.id)
            )
        )
        feed_id = next((feed.id for feed in feeds if refresh_due(feed, settings)), None)
    if feed_id is None:
        return False
    try:
        refresh_feed(factory, settings, feed_id, scheduled=True)
    except HTTPException as error:
        if error.status_code != 404:
            raise
    return True
