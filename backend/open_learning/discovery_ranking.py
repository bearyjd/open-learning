# SPDX-License-Identifier: GPL-3.0-only
"""Stateless relevance pages over an anchored window of at most 500 candidates."""

import base64
import binascii
import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select

from .models import DiscoveredItem
from .topics import score_item

CANDIDATE_LIMIT = 500
RANKING_CHANGED = "Discovery ranking changed; restart pagination"


def stamp(date):
    return (date.replace(tzinfo=UTC) if date.tzinfo is None else date).isoformat()


def decode_cursor(cursor, status):
    try:
        if len(cursor) > 768:
            raise ValueError
        data = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
        if (
            not isinstance(data, dict)
            or set(data) != {"v", "sort", "status", "anchor", "offset", "fingerprint"}
            or data["v"] != 1
            or data["sort"] != "relevance"
            or data["status"] != status
            or type(data["offset"]) is not int
            or not 1 <= data["offset"] < CANDIDATE_LIMIT
            or not isinstance(data["anchor"], list)
            or len(data["anchor"]) != 2
            or not isinstance(data["fingerprint"], str)
            or len(data["fingerprint"]) != 64
            or any(char not in "0123456789abcdef" for char in data["fingerprint"])
        ):
            raise ValueError
        date = datetime.fromisoformat(data["anchor"][0])
        ident = data["anchor"][1]
        if date.tzinfo is None or str(UUID(ident)) != ident:
            raise ValueError
        return data, date, ident
    except (
        ValueError,
        TypeError,
        AttributeError,
        KeyError,
        OverflowError,
        UnicodeError,
        binascii.Error,
    ) as error:
        raise HTTPException(422, "Invalid discovery cursor") from error


def relevance_page(db, status, limit, cursor, topics, profile_signature):
    query = select(
        DiscoveredItem.id,
        DiscoveredItem.feed_id,
        DiscoveredItem.url,
        func.substr(DiscoveredItem.title, 1, 2048).label("title"),
        DiscoveredItem.published_at,
        DiscoveredItem.snippet,
        DiscoveredItem.status,
        DiscoveredItem.created_at,
    ).where(DiscoveredItem.status == status)
    state = None
    if cursor is not None:
        state, date, ident = decode_cursor(cursor, status)
        query = query.where(
            or_(
                DiscoveredItem.created_at < date,
                and_(DiscoveredItem.created_at == date, DiscoveredItem.id <= ident),
            )
        )
    candidates = db.execute(
        query.order_by(DiscoveredItem.created_at.desc(), DiscoveredItem.id.desc()).limit(
            CANDIDATE_LIMIT
        )
    ).all()
    ranked = []
    for item in candidates:
        score, matches = score_item(item, topics)
        ranked.append({**item._asdict(), "score": score, "matched_topics": matches})
    # Stable sorts retain newest/id ordering of the bounded SQL window for score ties.
    ranked.sort(key=lambda item: -item["score"])
    fingerprint = hashlib.sha256(
        json.dumps(
            [profile_signature, ranked],
            default=lambda value: stamp(value),
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    if state is not None and state["fingerprint"] != fingerprint:
        raise HTTPException(409, RANKING_CHANGED)
    offset = state["offset"] if state else 0
    page = ranked[offset : offset + limit]
    next_cursor = None
    if offset + limit < len(ranked):
        anchor = state["anchor"] if state else [stamp(candidates[0].created_at), candidates[0].id]
        next_cursor = base64.urlsafe_b64encode(
            json.dumps(
                {
                    "v": 1,
                    "sort": "relevance",
                    "status": status,
                    "anchor": anchor,
                    "offset": offset + limit,
                    "fingerprint": fingerprint,
                },
                separators=(",", ":"),
            ).encode()
        ).decode()
    return {"items": page, "next_cursor": next_cursor}
