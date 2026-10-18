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
        ("unknown-id", "Model selected an unknown source span"),
        ("non-string-id", "Model unavailable or returned invalid structured output"),
        ("legacy-quote", "Model unavailable or returned invalid structured output"),
        ("legacy-index", "Model unavailable or returned invalid structured output"),
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
    first_content = {
        "title": "Aster beds",
        "idea": "Rainwater stays near shallow roots.",
        "explanation": "Aster beds retain rainwater in shallow roots.",
        "why_it_matters": "The source describes where rainwater remains.",
        "tags": ["garden"],
    }
    first = {"evidence_id": "e0", "content": first_content}
    second = {
        "evidence_id": "e3",
        "content": {
            **first_content,
            "title": "Cobalt reeds",
            "idea": "Reeds shade the bank.",
            "explanation": "Cobalt reeds shade the bank during hot afternoons.",
        },
    }
    if mode == "unknown-id":
        second["evidence_id"] = "e999"
    elif mode == "non-string-id":
        second["evidence_id"] = 3
    elif mode == "legacy-quote":
        second["evidence"] = "Cobalt reeds keep the entire garden cool throughout the day."
    elif mode == "legacy-index":
        second["chunk_index"] = 0
    elif mode == "too-many-tags":
        second["content"]["tags"] = [
            "garden",
            "reeds",
            "bank",
            "shade",
            "weather",
            "plants",
            "soil",
        ]
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
    spans = json.loads(request["messages"][1]["content"])["spans"]
    assert len(spans) > 3
    assert spans[0]["evidence_id"] == "e0"
    assert FIRST_QUOTE in spans[0]["text"]
    assert spans[3]["evidence_id"] == "e3"
    assert LATER_QUOTE in spans[3]["text"]
    assert LATER_QUOTE not in spans[0]["text"]
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
        assert len(after[Chunk]) > len(before[Chunk]) + 2
        assert len(after[Insight]) == len(before[Insight]) + 2
        new_document = next(row for row in after[Document] if row["title"] == "Garden observations")
        assert new_document["text"] == ARTICLE
        assert new_document["generation_coverage"] == "excerpt"
        with factory() as db:
            later = db.scalar(select(Insight).where(Insight.title == "Cobalt reeds"))
            chunk = db.get(Chunk, later.chunk_id)
            assert chunk.position == 1
            assert later.evidence == spans[3]["text"]
            assert LATER_QUOTE in chunk.text
            first_card = db.scalar(select(Insight).where(Insight.title == "Aster beds"))
            assert first_card.evidence == spans[0]["text"]
        cards = client.get(API + "/feed/today").json()
        assert {card["title"] for card in cards} == {
            existing_card["title"],
            "Aster beds",
            "Cobalt reeds",
        }
