"""Authenticated discovery routes, mounted on the application's shared API router."""

import base64
import binascii
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, or_, select

from .discovery import (
    create_feed,
    delete_feed,
    feed_output,
    lock_feed,
    lock_sqlite_writes,
    refresh_feed,
)
from .fetching import FetchError
from .models import DiscoveredItem, Feed
from .schemas import DiscoveryPage, FeedOutput, JobOutput
from .services import enqueue_url


class FeedInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1, max_length=2048)


def _cursor(item):
    date = item.created_at
    if date.tzinfo is None:
        date = date.replace(tzinfo=UTC)
    return base64.urlsafe_b64encode(f"{date.isoformat()}|{item.id}".encode()).decode()


def _decode(cursor):
    try:
        if len(cursor) > 256:
            raise ValueError
        date, ident = base64.b64decode(cursor, altchars=b"-_", validate=True).decode().split("|")
        date = datetime.fromisoformat(date)
        if date.tzinfo is None or str(UUID(ident)) != ident:
            raise ValueError
        return date, ident
    except (ValueError, UnicodeError, binascii.Error) as error:
        raise HTTPException(422, "Invalid discovery cursor") from error


def _lock_item(db, item_id):
    lock_sqlite_writes(db)
    feed_id = db.scalar(select(DiscoveredItem.feed_id).where(DiscoveredItem.id == item_id))
    if feed_id is None:
        raise HTTPException(404, "Discovered item not found")
    lock_feed(db, feed_id)
    item = db.scalar(select(DiscoveredItem).where(DiscoveredItem.id == item_id).with_for_update())
    if item is None:
        raise HTTPException(404, "Discovered item not found")
    return item


def mount_discovery(api, database):
    @api.post("/feeds", status_code=201, response_model=FeedOutput)
    def subscribe(payload: FeedInput, request: Request):
        return create_feed(request.app.state.factory, request.app.state.settings, payload.url)

    @api.get("/feeds", response_model=list[FeedOutput])
    def subscriptions(db=Depends(database)):
        return [
            feed_output(feed)
            for feed in db.scalars(select(Feed).order_by(Feed.created_at.desc(), Feed.id.desc()))
        ]

    @api.delete("/feeds/{feed_id}", status_code=204)
    def unsubscribe(feed_id: str, db=Depends(database)):
        delete_feed(db, feed_id)

    @api.post("/feeds/{feed_id}/refresh", response_model=FeedOutput)
    def refresh(feed_id: str, request: Request):
        return refresh_feed(request.app.state.factory, request.app.state.settings, feed_id)

    @api.get("/discover", response_model=DiscoveryPage)
    def discover(
        status: Literal["new", "ingested", "dismissed"] = "new",
        limit: int = Query(default=50, ge=1, le=100),
        cursor: str | None = None,
        db=Depends(database),
    ):
        query = select(DiscoveredItem).where(DiscoveredItem.status == status)
        if cursor is not None:
            date, ident = _decode(cursor)
            query = query.where(
                or_(
                    DiscoveredItem.created_at < date,
                    and_(DiscoveredItem.created_at == date, DiscoveredItem.id < ident),
                )
            )
        items = list(
            db.scalars(
                query.order_by(DiscoveredItem.created_at.desc(), DiscoveredItem.id.desc()).limit(
                    limit + 1
                )
            )
        )
        next_cursor = _cursor(items[limit - 1]) if len(items) > limit else None
        return {
            "items": [
                dict(
                    id=item.id,
                    feed_id=item.feed_id,
                    url=item.url,
                    title=item.title,
                    published_at=item.published_at,
                    snippet=item.snippet,
                    status=item.status,
                    created_at=item.created_at,
                )
                for item in items[:limit]
            ],
            "next_cursor": next_cursor,
        }

    @api.post("/discover/{item_id}/ingest", status_code=202, response_model=JobOutput)
    def ingest(item_id: str, db=Depends(database)):
        item = _lock_item(db, item_id)
        if item.status != "new":
            raise HTTPException(409, "Only new items can be ingested")
        try:
            job = enqueue_url(db, item.url)
        except FetchError as error:
            raise HTTPException(422, "feed_invalid_url") from error
        item.status = "ingested"
        db.commit()
        return dict(
            id=job.id,
            status=job.status,
            error=job.error,
            document_id=job.document_id,
            replaces_document_id=job.replaces_document_id,
        )

    @api.post("/discover/{item_id}/dismiss", status_code=204)
    def dismiss(item_id: str, db=Depends(database)):
        item = _lock_item(db, item_id)
        if item.status == "ingested":
            raise HTTPException(409, "Ingested items cannot be dismissed")
        item.status = "dismissed"
        db.commit()
