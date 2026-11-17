from datetime import timedelta

from open_learning.models import Chunk, Document, Insight, Source, now

API = "/api/v1"


def seed(factory, tag_lists):
    created_at = now()
    with factory.begin() as db:
        source = Source(url="https://example.com/feed", title="Learning")
        db.add(source)
        db.flush()
        document = Document(
            source_id=source.id, title="Learning", text="Recall ideas.", content_hash="a" * 64
        )
        db.add(document)
        db.flush()
        chunk = Chunk(document_id=document.id, position=0, text=document.text)
        db.add(chunk)
        db.flush()
        db.add_all(
            Insight(
                document_id=document.id,
                chunk_id=chunk.id,
                title=f"Idea {number}",
                idea="Recall ideas.",
                explanation="Practice recall.",
                why_it_matters="Remember more.",
                evidence="Recall ideas.",
                tags=tags,
                model="fixture",
                model_endpoint="fixture",
                created_at=created_at + timedelta(seconds=number),
            )
            for number, tags in enumerate(tag_lists)
        )


def set_preferences(client, **values):
    payload = {"interests": [], "daily_limit": 10, **values}
    assert client.put(API + "/preferences", json=payload).status_code == 200


def test_feed_honors_saved_daily_limit(client, factory):
    seed(factory, [[] for _ in range(8)])
    set_preferences(client, daily_limit=3)
    assert len(client.get(API + "/feed/today").json()) == 3


def test_feed_defaults_to_ten_without_preferences(client, factory):
    seed(factory, [[] for _ in range(12)])
    assert len(client.get(API + "/feed/today").json()) == 10


def test_lowering_limit_trims_cached_feed(client, factory):
    seed(factory, [[] for _ in range(8)])
    assert len(client.get(API + "/feed/today").json()) == 8
    set_preferences(client, daily_limit=2)
    assert len(client.get(API + "/feed/today").json()) == 2


def test_feed_prefers_saved_interests(client, factory):
    # Idea 0 is oldest, so without interests it would be cut by the limit.
    seed(factory, [["Python"], [], [], []])
    set_preferences(client, interests=["python"], daily_limit=1)
    (item,) = client.get(API + "/feed/today").json()
    assert item["title"] == "Idea 0"
