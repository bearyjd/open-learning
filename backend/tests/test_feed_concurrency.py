from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from conftest import TOKEN, FixtureModel
from fastapi.testclient import TestClient
from sqlalchemy import event, select

from open_learning.main import create_app
from open_learning.models import Chunk, DailyFeed, Document, Insight, Source, now


def test_concurrent_first_feed_requests_share_persisted_feed(factory, settings):
    engine = factory.kw["bind"]
    if engine.dialect.name != "postgresql":
        pytest.skip("Concurrent feed transactions require OL_TEST_DATABASE_URL PostgreSQL")
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
        document_id, chunk_id = document.id, chunk.id

        def insight(number, *, dismissed=False):
            return Insight(
                document_id=document_id,
                chunk_id=chunk_id,
                title=f"Idea {number}",
                idea="Recall ideas.",
                explanation="Practice recall.",
                why_it_matters="Remember more.",
                evidence="Recall ideas.",
                tags=[],
                model="fixture",
                model_endpoint="fixture",
                dismissed=dismissed,
                created_at=created_at + timedelta(seconds=number),
            )

        usable = [insight(number) for number in range(12)]
        db.add_all(usable + [insight(12, dismissed=True)])
    usable_ids = {item.id for item in usable}

    # Both real transactions reach insertion before either can create today's row.
    # This forces the absent-row race without changing query results or app reads.
    inserts = Barrier(2)

    def synchronize_inserts(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("INSERT INTO DAILY_FEEDS "):
            inserts.wait(timeout=10)

    with TestClient(create_app(settings, factory, FixtureModel())) as client:
        client.headers["Authorization"] = "Bearer " + TOKEN
        event.listen(engine, "before_cursor_execute", synchronize_inserts)
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                requests = [executor.submit(client.get, "/api/v1/feed/today") for _ in range(2)]
                responses = [request.result(timeout=20) for request in requests]
        finally:
            event.remove(engine, "before_cursor_execute", synchronize_inserts)

        assert [response.status_code for response in responses] == [200, 200]
        first_ids, second_ids = [[item["id"] for item in response.json()] for response in responses]
        assert first_ids == second_ids
        assert len(first_ids) == len(set(first_ids)) == 10
        assert set(first_ids) <= usable_ids
        with factory() as db:
            feeds = db.scalars(select(DailyFeed)).all()
            assert len(feeds) == 1
            assert feeds[0].insight_ids == first_ids

        dismissed_id = first_ids[0]
        assert (
            client.post(
                f"/api/v1/insights/{dismissed_id}/feedback", json={"action": "dismiss"}
            ).status_code
            == 204
        )
        with factory.begin() as db:
            new_item = insight(13)
            db.add(new_item)
        refreshed = client.get("/api/v1/feed/today")
        assert refreshed.status_code == 200
        refreshed_ids = [item["id"] for item in refreshed.json()]
        assert refreshed_ids == first_ids[1:] + [new_item.id]
        with factory() as db:
            assert db.scalar(select(DailyFeed)).insight_ids == refreshed_ids
