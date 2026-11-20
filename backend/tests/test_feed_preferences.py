from datetime import timedelta

from open_learning.models import Chunk, Document, Insight, Preference, Source, now
from open_learning.services import today_feed


def seed(factory, count, tags=None):
    """Insert `count` insights; Idea N is newer than Idea N-1."""
    created_at = now()
    with factory.begin() as db:
        source = Source(url="https://example.com/feed", title="Learning")
        db.add(source)
        db.flush()
        document = Document(source_id=source.id, title="L", text="Recall.", content_hash="a" * 64)
        db.add(document)
        db.flush()
        chunk = Chunk(document_id=document.id, position=0, text="Recall.")
        db.add(chunk)
        db.flush()
        for number in range(count):
            db.add(
                Insight(
                    document_id=document.id,
                    chunk_id=chunk.id,
                    title=f"Idea {number}",
                    idea="i",
                    explanation="e",
                    why_it_matters="w",
                    evidence="Recall.",
                    tags=(tags or {}).get(number, []),
                    model="fixture",
                    model_endpoint="fixture",
                    created_at=created_at + timedelta(seconds=number),
                )
            )


def prefer(factory, key, value):
    with factory.begin() as db:
        db.merge(Preference(key=key, value={"value": value}))


def titles(factory):
    with factory() as db:
        return [item["title"] for item in today_feed(db)]


def test_feed_defaults_to_ten(factory):
    seed(factory, 12)
    assert len(titles(factory)) == 10


def test_feed_uses_saved_daily_limit(factory):
    seed(factory, 12)
    prefer(factory, "daily_limit", 3)
    assert titles(factory) == ["Idea 11", "Idea 10", "Idea 9"]


def test_feed_clamps_daily_limit(factory):
    seed(factory, 25)
    prefer(factory, "daily_limit", 99)
    assert len(titles(factory)) == 20
    prefer(factory, "daily_limit", 0)
    assert len(titles(factory)) == 1


def test_feed_ignores_malformed_daily_limit(factory):
    seed(factory, 12)
    prefer(factory, "daily_limit", "many")
    assert len(titles(factory)) == 10


def test_cached_feed_shrinks_and_grows_with_limit(factory):
    seed(factory, 12)
    prefer(factory, "daily_limit", 5)
    assert len(titles(factory)) == 5
    prefer(factory, "daily_limit", 2)
    assert len(titles(factory)) == 2
    prefer(factory, "daily_limit", 8)
    assert len(titles(factory)) == 8


def test_interests_rank_matching_tags_first(factory):
    seed(factory, 12, tags={0: ["Python"], 1: ["rust"], 11: ["other"]})
    prefer(factory, "daily_limit", 4)
    prefer(factory, "interests", ["python", "RUST"])
    assert titles(factory) == ["Idea 1", "Idea 0", "Idea 11", "Idea 10"]
