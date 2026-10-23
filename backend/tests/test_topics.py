# SPDX-License-Identifier: GPL-3.0-only
"""Private, bounded topic extraction and stateless relevance pagination."""

import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from types import SimpleNamespace

import pytest
from conftest import PASSAGE
from sqlalchemy import event
from test_note_forget import import_note, insight_of
from test_note_versions import edit_and_run

from open_learning.models import DiscoveredItem, Document, Feed, Insight, SavedItem, now

API = "/api/v1"

UNICODE_EQUIVALENTS = [
    ("J\u030car", "ǰar"),
    ("CAFE\u0301", "café"),
    ("A\u0323\u0302bc", "ậbc"),
    ("İstanbul", "i\u0307stanbul"),
    ("ΣΟΦΟΣ", "σοφος"),
    ("Kelvin", "kelvin"),
]


@pytest.mark.parametrize("original,equivalent", UNICODE_EQUIVALENTS)
def test_unicode_normalization_is_idempotent_and_matches_items(original, equivalent):
    from open_learning.topics import CorpusText, derive_topics, normalize_pin, score_item, words

    normalized = normalize_pin(original)
    assert normalize_pin(normalized) == normalized
    assert normalize_pin(equivalent) == normalized
    assert words(original) == words(equivalent) == words(normalized)
    derived = derive_topics((CorpusText("a", original), CorpusText("b", equivalent)))
    assert len(derived) == 1 and derived[0].term == normalized and derived[0].count == 2
    for profile in (derived, derive_topics((), (normalized,))):
        for field in ("title", "snippet"):
            scores = [
                score_item(SimpleNamespace(**{"title": "", "snippet": "", field: text}), profile)
                for text in (original, equivalent, normalized)
            ]
            assert scores[0] == scores[1] == scores[2]
            assert scores[0][0] > 0


@pytest.mark.parametrize(
    "value,expected",
    [("J\u030car", "ǰar"), ("J\u030c" + "a" * 78, "ǰ" + "a" * 78), ("İ" * 40, "i\u0307" * 40)],
)
def test_pin_normalized_length_boundaries(value, expected):
    from open_learning.topics import normalize_pin

    assert normalize_pin(value) == expected
    assert normalize_pin(expected) == expected


@pytest.mark.parametrize("value", ["J\u030ca", "İ" * 41, "a" * 81])
def test_pin_rejects_outside_normalized_length_boundaries(value):
    from open_learning.topics import normalize_pin

    with pytest.raises(ValueError):
        normalize_pin(value)


def test_equivalent_unicode_pins_remain_idempotent_at_cap(client, factory):
    from sqlalchemy import select

    from open_learning.models import PinnedTopic

    first = client.post(API + "/topics/pinned", json={"term": "J\u030car"})
    assert first.status_code == 201
    pin = first.json()
    for term in (pin["term"], "ǰar", "J\u030car"):
        assert client.post(API + "/topics/pinned", json={"term": term}).json() == pin
    assert client.get(API + "/topics").json()["pinned"] == [pin]
    candidates(factory, titles=("ǰar", "J\u030car"))
    ranked = client.get(API + "/discover", params={"sort": "relevance"}).json()["items"]
    assert len(ranked) == 2 and ranked[0]["score"] == ranked[1]["score"] > 0
    for i in range(29):
        assert client.post(API + "/topics/pinned", json={"term": f"orchid {i}"}).status_code == 201
    for term in (pin["term"], "ǰar", "J\u030car"):
        response = client.post(API + "/topics/pinned", json={"term": term})
        assert response.status_code == 201 and response.json() == pin
    assert client.post(API + "/topics/pinned", json={"term": "volcano"}).status_code == 409
    with factory() as db:
        pins = db.scalars(select(PinnedTopic)).all()
        assert len(pins) == 30
        assert [row.term for row in pins].count("ǰar") == 1


def topics(client):
    response = client.get(API + "/topics", params={"limit": 50})
    assert response.status_code == 200, response.text
    return {row["term"]: row for row in response.json()["topics"]}


def candidates(factory, titles=("astronomy", "orchid gardening", "orchid")):
    stamp = now()
    with factory.begin() as db:
        feed = Feed(url="https://example.com/topics", title="T")
        db.add(feed)
        db.flush()
        items = [
            DiscoveredItem(
                feed_id=feed.id,
                url=f"https://example.com/{i}",
                title=title,
                snippet="",
                created_at=stamp - timedelta(seconds=i),
            )
            for i, title in enumerate(titles)
        ]
        db.add_all(items)
        db.flush()
        return [item.id for item in items], feed.id


def test_extraction_is_immutable_deterministic_unicode_and_counts_documents():
    from open_learning.topics import CorpusText, derive_topics

    corpus = (
        CorpusText("a", "The orchid gardening café 東京大学 orchid", 1),
        CorpusText("b", "ORCHID gardening café 東京大学", 1),
    )
    result = derive_topics(corpus)
    assert result == derive_topics(tuple(reversed(corpus)))
    found = {row.term: row for row in result}
    assert {"orchid", "orchid gardening", "café", "東京大学"} <= found.keys()
    assert "the" not in found and found["orchid"].count == 2
    assert found["orchid"].weight > found["café"].weight
    with pytest.raises((AttributeError, TypeError)):
        result[0].weight = 0


@pytest.mark.parametrize("version_only", [False, True])
def test_forget_removes_private_topics_immediately(client, factory, settings, version_only):
    document = import_note(
        client, factory, settings, text=(PASSAGE * 3 + "orchid gardening " * 30).strip()
    )
    assert "orchid" in topics(client)
    response = client.delete(f"{API}/notes/{document}", params={"version_only": version_only})
    assert response.status_code == 204
    assert "orchid" not in topics(client)
    assert client.get(API + "/topics").json() == {"topics": [], "pinned": []}


def test_superseded_saved_insights_do_not_contribute(client, factory, settings):
    document = import_note(
        client, factory, settings, text=(PASSAGE * 3 + "orchid gardening " * 30).strip()
    )
    with factory.begin() as db:
        old = db.get(Insight, insight_of(factory, document))
        old.title = "hiddeninsightmarker"
        db.add(SavedItem(insight_id=old.id))
    edit_and_run(client, factory, settings, document, text="volcano geology " * 30)
    found = topics(client)
    assert "volcano" in found and "orchid" not in found and "hiddeninsightmarker" not in found
    assert (
        client.delete(f"{API}/notes/{document}", params={"version_only": True}).status_code == 204
    )
    assert topics(client) == found


def test_saved_current_insight_boosts_weights(client, factory, settings):
    document = import_note(client, factory, settings)
    before = topics(client)["retrieval"]["weight"]
    with factory.begin() as db:
        db.add(SavedItem(insight_id=insight_of(factory, document)))
    assert topics(client)["retrieval"]["weight"] > before


def test_pins_crud_normalization_and_auth(client):
    for method, path, body in (
        ("get", "/topics", None),
        ("post", "/topics/pinned", {"term": "orchid"}),
        ("delete", "/topics/pinned/missing", None),
    ):
        assert (
            client.request(method, API + path, json=body, headers={"Authorization": ""}).status_code
            == 401
        )
    response = client.post(API + "/topics/pinned", json={"term": "  ＣＡＦÉ   Gardening  "})
    assert response.status_code == 201
    pin = response.json()
    assert pin["term"] == "café gardening"
    assert client.post(API + "/topics/pinned", json={"term": "café gardening"}).json() == pin
    response = client.get(API + "/topics")
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json()["pinned"] == [pin]
    assert topics(client)["café gardening"]["weight"] >= 10
    assert client.delete(API + "/topics/pinned/" + pin["id"]).status_code == 204
    assert client.delete(API + "/topics/pinned/" + pin["id"]).status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"term": ""},
        {"term": "a"},
        {"term": "!@#"},
        {"term": "private" * 20},
        {"term": "secret\nvalue"},
        {"term": "one two three four five six"},
        {"term": 5},
        {"term": "orchid", "extra": "private-marker"},
    ],
)
def test_pin_validation_never_echoes_content(client, body):
    response = client.post(API + "/topics/pinned", json=body)
    assert response.status_code == 422
    assert "private-marker" not in response.text and "secret" not in response.text


@pytest.mark.parametrize(
    "path,parameter",
    [("/topics", "limit"), ("/discover", "sort"), ("/discover", "status"), ("/discover", "limit")],
)
def test_private_query_validation_never_echoes_input(client, path, parameter):
    response = client.get(API + path, params={parameter: "private-marker"})
    assert response.status_code == 422
    assert "private-marker" not in response.text


def test_pin_cap_is_serialized_with_real_connections(client, factory):
    from open_learning.models import PinnedTopic

    with factory.begin() as db:
        db.add_all(PinnedTopic(term=f"topic {i}") for i in range(29))
    barrier = Barrier(2)

    def post(i):
        barrier.wait(timeout=10)
        return client.post(API + "/topics/pinned", json={"term": f"orchid {i}"}).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(post, range(2))) == [201, 409]
    assert len(client.get(API + "/topics").json()["pinned"]) == 30


def test_topic_limits_and_bounded_sql_text(client, factory):
    from open_learning.models import Source
    from open_learning.topics import CORPUS_LIMIT, TEXT_LIMIT, load_corpus

    stamp = now()
    with factory.begin() as db:
        source = Source(url="note:bounded", title="", kind="note")
        db.add(source)
        db.flush()
        db.add_all(
            Document(
                source_id=source.id,
                title="",
                text="orchid " * 2000,
                content_hash=str(i),
                created_at=stamp - timedelta(seconds=i),
            )
            for i in range(510)
        )
    statements = []

    def record(conn, cursor, statement, parameters, context, many):
        statements.append(statement.lower())

    event.listen(factory.kw["bind"], "before_cursor_execute", record)
    try:
        with factory() as db:
            corpus = load_corpus(db)
    finally:
        event.remove(factory.kw["bind"], "before_cursor_execute", record)
    assert len({row.document_id for row in corpus}) == CORPUS_LIMIT == 500
    assert max(len(row.text) for row in corpus) <= TEXT_LIMIT + 256
    assert any("substr" in sql and "limit" in sql for sql in statements)
    for limit in (0, 51):
        assert client.get(API + "/topics", params={"limit": limit}).status_code == 422


def test_insight_scan_and_projection_are_bounded(client, factory, settings):
    from open_learning.topics import INSIGHT_LIMIT, load_corpus

    document = import_note(client, factory, settings)
    with factory.begin() as db:
        template = db.get(Insight, insight_of(factory, document))
        template.created_at = now() - timedelta(days=1)
        template.title = "excludedoldinsight"
        db.add_all(
            Insight(
                document_id=document,
                chunk_id=template.chunk_id,
                title="current insight",
                idea="orchid " * 2000,
                explanation="",
                why_it_matters="",
                evidence="",
                model="fixture",
                model_endpoint="local",
            )
            for _ in range(510)
        )
    with factory() as db:
        corpus = load_corpus(db)
    assert len(corpus) == INSIGHT_LIMIT + 1  # 500 insights plus the note's own text.
    assert max(len(row.text) for row in corpus) <= 3772
    assert not any("excludedoldinsight" in row.text for row in corpus)


def test_relevance_title_boost_newest_default_and_max_matches(client, factory):
    ids, _ = candidates(factory)
    client.post(API + "/topics/pinned", json={"term": "orchid"})
    newest = client.get(API + "/discover")
    assert newest.headers["Cache-Control"] == "no-store"
    assert [row["id"] for row in newest.json()["items"]] == ids
    ranked = client.get(API + "/discover", params={"sort": "relevance"}).json()["items"]
    assert [row["id"] for row in ranked] == [ids[1], ids[2], ids[0]]
    assert ranked[0]["score"] > 0 and ranked[0]["matched_topics"] == ["orchid"]
    assert all(len(row["matched_topics"]) <= 5 for row in ranked)
    assert client.get(API + "/discover?sort=invalid").status_code == 422


def test_relevance_cursor_anchor_and_private_payload(client, factory):
    ids, feed_id = candidates(factory)
    client.post(API + "/topics/pinned", json={"term": "orchid"})
    first = client.get(API + "/discover?sort=relevance&limit=1").json()
    cursor = first["next_cursor"]
    assert cursor and b"orchid" not in base64.urlsafe_b64decode(cursor)
    with factory.begin() as db:
        db.add(
            DiscoveredItem(
                feed_id=feed_id,
                url="https://example.com/arrival",
                title="orchid",
                snippet="",
                created_at=now(),
            )
        )
    rest = client.get(
        API + "/discover", params={"sort": "relevance", "limit": 10, "cursor": cursor}
    )
    assert rest.status_code == 200
    assert [row["id"] for row in rest.json()["items"]] == [ids[2], ids[0]]
    for params in ({"sort": "relevance", "status": "dismissed"}, {"sort": "newest"}):
        assert client.get(API + "/discover", params={**params, "cursor": cursor}).status_code == 422


@pytest.mark.parametrize("change", ["pin", "candidate", "forget"])
def test_relevance_cursor_requires_restart_after_private_state_changes(
    client, factory, settings, change
):
    document = import_note(client, factory, settings, text=("orchid gardening " * 30).strip())
    ids, _ = candidates(factory)
    first = client.get(API + "/discover?sort=relevance&limit=1").json()
    if change == "pin":
        client.post(API + "/topics/pinned", json={"term": "volcano"})
    elif change == "candidate":
        with factory.begin() as db:
            db.get(DiscoveredItem, ids[-1]).title = "changed"
    else:
        client.delete(f"{API}/notes/{document}")
    response = client.get(
        API + "/discover", params={"sort": "relevance", "cursor": first["next_cursor"]}
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "Discovery ranking changed; restart pagination"


def test_relevance_window_is_bounded(client, factory):
    ids, _ = candidates(factory, titles=tuple("orchid" for _ in range(510)))
    result = []
    cursor = None
    while True:
        params = {"sort": "relevance", "limit": 100}
        if cursor:
            params["cursor"] = cursor
        response = client.get(API + "/discover", params=params)
        assert response.status_code == 200, response.text
        page = response.json()
        result.extend(row["id"] for row in page["items"])
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert result == ids[:500]
