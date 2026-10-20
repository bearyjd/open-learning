"""Validate tutor HTTP responses before changing conversation or review state."""

import json

import httpx
import pytest
from conftest import FixtureModel, article_fetcher
from sqlalchemy import select

from open_learning.ai import ModelClient
from open_learning.models import History, Interaction, ReviewItem
from open_learning.worker import run_once

API = "/api/v1"


@pytest.mark.parametrize(
    "mode,existing_history",
    [
        ("valid", False),
        ("unknown-id", False),
        ("unknown-id", True),
        ("non-string-id", False),
        ("legacy-quote", False),
        ("extra-field", False),
        ("oversized-answer", False),
        ("oversized-question", False),
    ],
)
def test_tutor_model_http_validates_before_writing(
    client, factory, settings, monkeypatch, mode, existing_history
):
    job = client.post(API + "/ingest/url", json={"url": "https://example.com/article"}).json()
    assert run_once(factory, settings, article_fetcher, FixtureModel())
    assert client.get(API + "/jobs/" + job["id"]).json()["status"] == "completed"
    insight = client.get(API + "/feed/today").json()[0]
    if existing_history:
        assert (
            client.post(
                API + "/tutor/chat",
                json={"insight_id": insight["id"], "message": "Earlier answer."},
            ).status_code
            == 200
        )

    def snapshot():
        with factory() as db:
            return {
                entity: [
                    dict(row)
                    for row in db.execute(select(entity.__table__).order_by(entity.id)).mappings()
                ]
                for entity in (History, ReviewItem, Interaction)
            }

    before = snapshot()
    content = {
        "answer": "Recall the idea before rereading.",
        "question": "How does retrieval practice support memory?",
    }
    draft = {"evidence_id": "e0", "content": content}
    if mode == "unknown-id":
        draft["evidence_id"] = "e999"
    elif mode == "non-string-id":
        draft["evidence_id"] = 0
    elif mode == "legacy-quote":
        draft["evidence"] = "An invented quotation that is not in the article."
    elif mode == "extra-field":
        content["confidence"] = 100
    elif mode == "oversized-answer":
        content["answer"] = "A" * 2001
    elif mode == "oversized-question":
        content["question"] = "Q" * 501
    received = []

    def respond(request):
        received.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(draft)}}]})

    original_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    client.app.state.model = ModelClient(settings)
    result = client.post(
        API + "/tutor/chat",
        json={"insight_id": insight["id"], "message": "I should recall before rereading."},
    )
    assert len(received) == 1
    payload = json.loads(received[0]["messages"][1]["content"])
    assert payload["user_message"] == "I should recall before rereading."
    assert bool(payload["history"]) == existing_history
    after = snapshot()
    if mode == "valid":
        assert result.status_code == 200
        body = result.json()
        assert body["answer"] == content["answer"]
        assert body["question"] == content["question"]
        assert body["citations"] == [
            {
                "document_id": insight["document_id"],
                "source_url": insight["source_url"],
                "source_kind": "url",
                "source_label": "",
                "passage": payload["spans"][0]["text"],
            }
        ]
        assert payload["spans"][0]["text"] in article_fetcher("", settings).text
        assert len(after[History]) == 2
        assert len(after[Interaction]) == 1
        assert len(after[ReviewItem]) == 1
        assert after[ReviewItem][0]["id"] == body["review_id"]
        assert after[ReviewItem][0]["question"] == content["question"]
    else:
        assert result.status_code == 503
        assert result.json()["detail"] == (
            "Model selected an unknown source span"
            if mode == "unknown-id"
            else "Model unavailable or returned invalid structured output"
        )
        assert after == before
