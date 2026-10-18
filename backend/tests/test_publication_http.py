"""Characterize atomic publication using fixed model responses over real TCP HTTP."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from conftest import FixtureModel, article_fetcher
from sqlalchemy import select

from open_learning.ai import ModelClient
from open_learning.fetching import FetchedDocument
from open_learning.models import Chunk, Document, Insight, ReviewItem, Source
from open_learning.worker import run_once

API = "/api/v1"
FIRST_QUOTE = "Aster beds retain rainwater in shallow roots."
LATER_QUOTE = "Cobalt reeds shade the bank during hot afternoons."
ARTICLE = (
    FIRST_QUOTE
    + " "
    + "Soil sensors record moisture after dawn. " * 70
    + LATER_QUOTE
    + " "
    + "Willow hedges shelter small birds during storms. " * 100
)


@pytest.mark.parametrize(
    "mode,error",
    [
        ("valid", None),
        ("paraphrased-quote", "Model returned evidence absent from the source"),
        ("wrong-index", "Model returned evidence absent from the source"),
        ("missing-index", "Model selected a missing source passage"),
        ("too-many-tags", "Model unavailable or returned invalid structured output"),
    ],
)
def test_model_http_batch_publication_is_atomic(client, factory, settings, mode, error):
    existing_job = client.post(
        API + "/ingest/url", json={"url": "https://example.com/existing"}
    ).json()
    assert run_once(factory, settings, article_fetcher, FixtureModel())
    assert client.get(API + "/jobs/" + existing_job["id"]).json()["status"] == "completed"
    existing_card = client.get(API + "/feed/today").json()[0]
    assert client.post(API + "/insights/" + existing_card["id"] + "/save").status_code == 200
    existing_feed = client.get(API + "/feed/today").json()

    def snapshot():
        with factory() as db:
            return {
                entity: [
                    dict(row)
                    for row in db.execute(select(entity.__table__).order_by(entity.id)).mappings()
                ]
                for entity in (Source, Document, Chunk, Insight, ReviewItem)
            }

    before = snapshot()
    first = {
        "title": "Aster beds",
        "idea": "Rainwater stays near shallow roots.",
        "explanation": "Aster beds retain rainwater in shallow roots.",
        "why_it_matters": "The source describes where rainwater remains.",
        "evidence": FIRST_QUOTE,
        "chunk_index": 0,
        "tags": ["garden"],
    }
    second = {
        **first,
        "title": "Cobalt reeds",
        "idea": "Reeds shade the bank.",
        "explanation": "Cobalt reeds shade the bank during hot afternoons.",
        "evidence": LATER_QUOTE,
        "chunk_index": 1,
    }
    if mode == "paraphrased-quote":
        second["evidence"] = "Cobalt reeds keep the entire garden cool throughout the day."
    elif mode == "wrong-index":
        second["chunk_index"] = 0
    elif mode == "missing-index":
        second["chunk_index"] = 99
    elif mode == "too-many-tags":
        second["tags"] = ["garden", "reeds", "bank", "shade", "weather", "plants", "soil"]
    response_body = json.dumps(
        {"choices": [{"message": {"content": json.dumps({"insights": [first, second]})}}]}
    ).encode()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(
                (self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            self.wfile.write(response_body)

        def log_message(self, *args):
            pass

    def fetch_article(url, config):
        return FetchedDocument(url, "Garden observations", ARTICLE)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        settings.ai_base_url = f"http://127.0.0.1:{server.server_port}/v1"
        job = client.post(API + "/ingest/url", json={"url": "https://example.com/garden"}).json()
        assert run_once(factory, settings, fetch_article, ModelClient(settings))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert len(requests) == 1
    path, request = requests[0]
    assert path == "/v1/chat/completions"
    passages = json.loads(request["messages"][1]["content"])["passages"]
    assert len(passages) > 2
    assert FIRST_QUOTE in passages[0]
    assert LATER_QUOTE in passages[1]
    assert LATER_QUOTE not in passages[0]
    assert "Cobalt reeds keep the entire garden cool throughout the day." not in ARTICLE
    status = client.get(API + "/jobs/" + job["id"]).json()
    after = snapshot()

    if error:
        assert status["status"] == "failed"
        assert status["error"] == error
        assert after == before
        assert client.get(API + "/feed/today").json() == existing_feed
        retry = client.post(API + "/jobs/" + job["id"] + "/retry")
        assert retry.status_code == 200
        assert retry.json()["status"] == "queued"
        assert retry.json()["error"] is None
    else:
        assert status["status"] == "completed"
        assert status["error"] is None
        for entity, rows in before.items():
            assert all(row in after[entity] for row in rows)
        assert len(after[Source]) == len(before[Source]) + 1
        assert len(after[Document]) == len(before[Document]) + 1
        assert len(after[Chunk]) == len(before[Chunk]) + len(passages)
        assert len(after[Insight]) == len(before[Insight]) + 2
        new_document = next(row for row in after[Document] if row["title"] == "Garden observations")
        assert new_document["text"] == ARTICLE
        assert new_document["generation_coverage"] == "excerpt"
        with factory() as db:
            later = db.scalar(select(Insight).where(Insight.title == "Cobalt reeds"))
            chunk = db.get(Chunk, later.chunk_id)
            assert chunk.position == 1
            assert later.evidence == LATER_QUOTE
            assert LATER_QUOTE in chunk.text
        cards = client.get(API + "/feed/today").json()
        assert {card["title"] for card in cards} == {
            existing_card["title"],
            "Aster beds",
            "Cobalt reeds",
        }
