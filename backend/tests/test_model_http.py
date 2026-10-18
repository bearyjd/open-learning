"""Exercise the real HTTP adapter against a controlled OpenAI-compatible server."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from conftest import PASSAGE

from open_learning.ai import ModelClient, ModelError


@pytest.mark.parametrize("overlong", [False, True], ids=["valid", "oversized"])
def test_model_adapter_sends_request_and_validates_grounded_response(settings, overlong):
    received = []
    insight = {
        "title": "Retrieval practice",
        "idea": "A" * 2001 if overlong else "Recall before rereading.",
        "explanation": "Trying to recall strengthens memory.",
        "why_it_matters": "Study more effectively.",
        "evidence": PASSAGE.strip(),
        "chunk_index": 0,
        "tags": ["learning"],
    }

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
                {"choices": [{"message": {"content": json.dumps({"insights": [insight]})}}]}
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
        assert result.insights[0].model_dump() == insight
    path, authorization, payload = received[0]
    assert path == "/v1/chat/completions"
    assert authorization == "Bearer fixture-key"
    assert payload["model"] == "controlled-protocol-fixture"
    shape = payload["response_format"]["json_schema"]["schema"]
    assert payload["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "InsightBatch",
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
        "items": {"$ref": "#/$defs/GroundedInsight"},
    }
    item_shape = shape["$defs"]["GroundedInsight"]
    assert item_shape["type"] == "object"
    assert set(item_shape["required"]) == set(insight)
    assert item_shape["additionalProperties"] is False
    assert item_shape["properties"]["title"]["type"] == "string"
    assert item_shape["properties"]["tags"]["items"]["type"] == "string"
    assert item_shape["properties"]["chunk_index"]["minimum"] == 0
    assert '"maxLength": 2000' in payload["messages"][0]["content"]
    assert (
        "Every insight must include title, idea, explanation, why_it_matters, evidence, "
        "chunk_index, tags"
    ) in payload["messages"][0]["content"]
    assert json.loads(payload["messages"][1]["content"]) == {"passages": [PASSAGE]}
