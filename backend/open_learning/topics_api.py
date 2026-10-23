# SPDX-License-Identifier: GPL-3.0-only
"""Authenticated topic routes sharing the application API router."""

from dataclasses import asdict
from datetime import UTC

from fastapi import Depends, HTTPException, Query, Response
from pydantic import BaseModel, model_validator
from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse

from .discovery import lock_sqlite_writes
from .models import PinnedTopic
from .topics import normalize_pin, pin_topic, profile
from .versions import NO_STORE


def private_topic_read(scope):
    path = scope.get("path", "").removeprefix(scope.get("root_path", "")).rstrip("/")
    return path in {
        "/api/v1/topics",
        "/api/v1/discover",
        "/api/v1/topics/pinned",
    } or path.startswith("/api/v1/topics/pinned/")


class TopicReadPrivacyMiddleware:
    """Protect private topic routes, including early failures and slash variants."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not private_topic_read(scope):
            await self.app(scope, receive, send)
            return

        if scope.get("path", "").endswith("/"):
            # Router slash redirects include the original private query in Location.
            await JSONResponse({"detail": "Not Found"}, status_code=404, headers=NO_STORE)(
                scope, receive, send
            )
            return

        async def private_send(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(raw=list(message.get("headers", [])))
                headers.update(NO_STORE)
                message = {**message, "headers": headers.raw}
            await send(message)

        await self.app(scope, receive, private_send)


class PinInput(BaseModel):
    term: str

    @model_validator(mode="before")
    @classmethod
    def validate_pin(cls, value):
        if not isinstance(value, dict) or set(value) != {"term"}:
            raise ValueError("Invalid pinned topic")
        return {"term": normalize_pin(value["term"])}


def pin_output(pin):
    created = pin.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return {"id": pin.id, "term": pin.term, "created_at": created}


def mount_topics(api, database):
    @api.get("/topics")
    def topics(
        response: Response, limit: int = Query(default=20, ge=1, le=50), db=Depends(database)
    ):
        response.headers.update(NO_STORE)
        derived, pins, _ = profile(db)
        return {
            "topics": [asdict(topic) for topic in derived[:limit]],
            "pinned": [pin_output(pin) for pin in pins],
        }

    @api.post("/topics/pinned", status_code=201)
    def pin(payload: PinInput, response: Response, db=Depends(database)):
        response.headers.update(NO_STORE)
        return pin_output(pin_topic(db, payload.term))

    @api.delete("/topics/pinned/{pin_id}", status_code=204)
    def unpin(pin_id: str, db=Depends(database)):
        lock_sqlite_writes(db)
        pin = db.get(PinnedTopic, pin_id)
        if pin is None:
            raise HTTPException(404, "Pinned topic not found")
        db.delete(pin)
        db.commit()
