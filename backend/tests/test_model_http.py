"""Exercise the real HTTP adapter against a controlled OpenAI-compatible server."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from conftest import PASSAGE

from open_learning.ai import ModelClient, ModelError, TutorResult


@pytest.mark.parametrize("overlong", [False, True], ids=["valid", "oversized"])
def test_model_adapter_sends_request_and_validates_grounded_response(settings, overlong):
    received = []
    content = {
        "title": "Retrieval practice",
        "idea": "A" * 2001 if overlong else "Recall before rereading.",
        "explanation": "Trying to recall strengthens memory.",
        "why_it_matters": "Study more effectively.",
        "tags": ["learning"],
    }
    draft = {"evidence_id": "e0", "content": content}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(
                (
                    self.path,
                    self.headers["Authorization"],
                    json.loads(self.rfile.read(int(self.headers["Content-Length"]))),
                )
            )
            body = json.dumps(
                {"choices": [{"message": {"content": json.dumps({"insights": [draft]})}}]}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        settings.ai_base_url = f"http://127.0.0.1:{server.server_port}/v1"
        settings.ai_model = "controlled-protocol-fixture"
        settings.ai_api_key = "fixture-key"
        if overlong:
            with pytest.raises(ModelError, match="invalid structured output"):
                ModelClient(settings).insights([PASSAGE])
        else:
            result = ModelClient(settings).insights([PASSAGE])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    if not overlong:
        assert result.insights[0].model_dump() == {
            **content,
            "evidence": PASSAGE,
            "chunk_index": 0,
        }
    path, authorization, payload = received[0]
    assert path == "/v1/chat/completions"
    assert authorization == "Bearer fixture-key"
    assert payload["model"] == "controlled-protocol-fixture"
    shape = payload["response_format"]["json_schema"]["schema"]
    assert payload["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "_InsightDraftBatch",
            "schema": shape,
            "strict": True,
        },
    }
    for keyword in ("minLength", "maxLength", "minItems", "maxItems"):
        assert f'"{keyword}"' not in json.dumps(shape)
    assert shape["type"] == "object"
    assert shape["required"] == ["insights"]
    assert shape["additionalProperties"] is False
    assert shape["properties"]["insights"] == {
        "type": "array",
        "title": "Insights",
        "items": {"$ref": "#/$defs/_InsightDraft"},
    }
    item_shape = shape["$defs"]["_InsightDraft"]
    assert item_shape["type"] == "object"
    assert item_shape["required"] == ["evidence_id", "content"]
    assert list(item_shape["properties"]) == ["evidence_id", "content"]
    assert item_shape["additionalProperties"] is False
    assert item_shape["properties"]["evidence_id"]["type"] == "string"
    content_shape = shape["$defs"]["_InsightContent"]
    assert set(content_shape["required"]) == set(content)
    assert content_shape["additionalProperties"] is False
    assert content_shape["properties"]["title"]["type"] == "string"
    assert content_shape["properties"]["tags"]["items"]["type"] == "string"
    assert '"maxLength": 2000' in payload["messages"][0]["content"]
    assert "Extract exactly ONE narrow useful idea" in payload["messages"][0]["content"]
    assert "Evidence is selected by evidence_id" in payload["messages"][0]["content"]
    assert "Evidence must be an exact verbatim quote" not in payload["messages"][0]["content"]
    assert json.loads(payload["messages"][1]["content"]) == {
        "spans": [{"evidence_id": "e0", "text": PASSAGE}]
    }


@pytest.mark.parametrize("legacy_quote", [False, True])
def test_tutor_selects_canonical_source_span(monkeypatch, settings, legacy_quote):
    received = []
    content = {
        "answer": "Recall the idea before rereading.",
        "question": "How does retrieval practice support memory?",
    }
    response = {"evidence_id": "e0", "content": content}
    if legacy_quote:
        response["evidence"] = "This invented quotation does not appear in the source."

    def respond(request):
        received.append(json.loads(request.content))
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(response)}}]}
        )

    original_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    model = ModelClient(settings)
    if legacy_quote:
        with pytest.raises(ModelError, match="invalid structured output"):
            model.tutor(PASSAGE, "Retrieval", "", [])
    else:
        assert model.tutor(PASSAGE, "Retrieval", "", []).model_dump() == {
            **content,
            "evidence": PASSAGE,
        }
    request = received[0]
    shape = request["response_format"]["json_schema"]
    assert shape["name"] == "_TutorDraft"
    assert shape["schema"]["required"] == ["evidence_id", "content"]
    assert shape["schema"]["additionalProperties"] is False
    assert "Evidence must be an exact verbatim quote" not in request["messages"][0]["content"]
    assert "Evidence is selected by evidence_id" in request["messages"][0]["content"]
    assert json.loads(request["messages"][1]["content"]) == {
        "spans": [{"evidence_id": "e0", "text": PASSAGE}],
        "title": "Retrieval",
        "user_message": "",
        "history": [],
    }


def _tutor_model(monkeypatch, settings, content):
    received = []

    def respond(request):
        received.append(json.loads(request.content))
        body = {"evidence_id": "e0", "content": content}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(body)}}]})

    original_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    return ModelClient(settings), received


@pytest.mark.parametrize(
    "assessment", ["correct", "partially_correct", "incorrect", "not_assessed"]
)
def test_tutor_carries_optional_assessment(monkeypatch, settings, assessment):
    content = {"answer": "Good.", "question": "What does recall replace?", "assessment": assessment}
    model, received = _tutor_model(monkeypatch, settings, content)
    result = model.tutor(PASSAGE, "Retrieval", "Recall helps.", [])
    assert result.assessment == assessment
    shape = received[0]["response_format"]["json_schema"]["schema"]["$defs"]["_TutorContent"]
    assert shape["properties"]["assessment"]["enum"] == [
        "correct",
        "partially_correct",
        "incorrect",
        "not_assessed",
    ]
    assert "assessment" not in shape["required"]


def test_tutor_assessment_is_optional_and_hidden_when_unassessed(monkeypatch, settings):
    content = {"answer": "Good.", "question": "What does recall replace?"}
    model, _ = _tutor_model(monkeypatch, settings, content)
    result = model.tutor(PASSAGE, "Retrieval", "", [])
    assert result.assessment == "not_assessed"
    assert "assessment" not in result.model_dump()
    assert TutorResult.model_validate(result.model_dump()) == result


@pytest.mark.parametrize(
    "override",
    [{"assessment": "mostly"}, {"answer": "   "}, {"question": "     "}],
    ids=["bad-assessment", "blank-answer", "blank-question"],
)
def test_tutor_rejects_invalid_content(monkeypatch, settings, override):
    content = {"answer": "Good.", "question": "What does recall replace?", **override}
    model, _ = _tutor_model(monkeypatch, settings, content)
    with pytest.raises(ModelError, match="invalid structured output"):
        model.tutor(PASSAGE, "Retrieval", "Recall helps.", [])


def test_tutor_payload_carries_previous_question_and_retry_instruction(monkeypatch, settings):
    content = {"answer": "Good.", "question": "What does recall replace?"}
    model, received = _tutor_model(monkeypatch, settings, content)
    model.tutor(PASSAGE, "Retrieval", "Recall helps.", [], previous_question="Why recall?")
    payload = json.loads(received[0]["messages"][1]["content"])
    assert payload["previous_question"] == "Why recall?"
    assert "retry_instruction" not in payload
    model.tutor(
        PASSAGE, "Retrieval", "Recall helps.", [], previous_question="Why recall?", avoid_repeat=True
    )
    payload = json.loads(received[1]["messages"][1]["content"])
    assert payload["previous_question"] == "Why recall?"
    assert "different question" in payload["retry_instruction"]
