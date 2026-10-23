# SPDX-License-Identifier: GPL-3.0-only
"""Adversarial coverage for mounted APIs, cache policy, forgetting, and cursors."""

import asyncio
import base64
import json

import pytest
from conftest import TOKEN
from fastapi.testclient import TestClient
from test_note_forget import import_note
from test_topics import API, candidates

from open_learning.models import DiscoveredItem

PRIVATE_QUERY_MARKER = "private-query-marker"
PRIVATE_PATHS = (
    "/topics",
    "/topics/pinned",
    "/topics/pinned/missing",
    "/topics/unknown",
    "/topics/unknown/deep",
    "/discover",
    "/discover/missing/ingest",
    "/discover/missing/dismiss",
    "/discover/unknown",
    "/discover/unknown/deep",
)


def assert_private_response(response):
    assert response.headers.get("Cache-Control") == "no-store"
    assert response.headers.get("Pragma") == "no-cache"
    assert not response.is_redirect
    assert "Location" not in response.headers
    assert PRIVATE_QUERY_MARKER not in response.text
    assert all(PRIVATE_QUERY_MARKER not in value for value in response.headers.values())


@pytest.mark.parametrize("root_path", ["", "/ol"])
@pytest.mark.parametrize("authenticated", [True, False])
@pytest.mark.parametrize(
    "method", ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE", "CONNECT"]
)
def test_private_namespace_method_path_matrix(client, root_path, authenticated, method):
    matching_methods = {
        "/topics": "GET",
        "/topics/pinned": "POST",
        "/topics/pinned/missing": "DELETE",
        "/discover": "GET",
        "/discover/missing/ingest": "POST",
        "/discover/missing/dismiss": "POST",
    }
    authenticated_statuses = {
        "/topics": 200,
        "/topics/pinned": 422,
        "/topics/pinned/missing": 404,
        "/discover": 422,
        "/discover/missing/ingest": 404,
        "/discover/missing/dismiss": 404,
    }
    with TestClient(client.app, root_path=root_path) as mounted:
        for path in PRIVATE_PATHS:
            for slash in ("", "/"):
                response = mounted.request(
                    method,
                    root_path + API + path + slash,
                    params={"cursor": PRIVATE_QUERY_MARKER},
                    headers={"Authorization": "Bearer " + TOKEN if authenticated else ""},
                    follow_redirects=False,
                )
                if slash or path not in matching_methods:
                    expected = 404
                elif method != matching_methods[path]:
                    expected = 405
                else:
                    expected = authenticated_statuses[path] if authenticated else 401
                assert response.status_code == expected, (
                    method,
                    root_path + API + path + slash,
                    authenticated,
                    response.text,
                )
                assert_private_response(response)


@pytest.mark.parametrize("root_path", ["", "/ol"])
@pytest.mark.parametrize(
    "module,attribute,method,path",
    [
        ("topics_api", "profile", "GET", "/topics"),
        ("discovery_api", "profile", "GET", "/discover"),
        ("discovery_api", "_lock_item", "POST", "/discover/missing/ingest"),
        ("discovery_api", "_lock_item", "POST", "/discover/missing/dismiss"),
    ],
)
def test_private_unhandled_failures_are_generic_no_store(
    client, monkeypatch, root_path, module, attribute, method, path
):
    from importlib import import_module

    def fail(*args, **kwargs):
        raise RuntimeError(PRIVATE_QUERY_MARKER)

    monkeypatch.setattr(import_module("open_learning." + module), attribute, fail)
    with TestClient(client.app, root_path=root_path, raise_server_exceptions=False) as mounted:
        response = mounted.request(
            method,
            root_path + API + path,
            params={"cursor": PRIVATE_QUERY_MARKER},
            headers={"Authorization": "Bearer " + TOKEN},
        )
    assert response.status_code == 500
    assert response.text == "Internal Server Error"
    assert_private_response(response)


@pytest.mark.parametrize("root_path", ["", "/ol"])
@pytest.mark.parametrize("path", PRIVATE_PATHS)
def test_private_oversized_body_is_no_store(client, path, root_path):
    from open_learning.main import DEFAULT_BODY_LIMIT

    with TestClient(client.app, root_path=root_path) as mounted:
        response = mounted.post(
            root_path + API + path,
            params={"cursor": PRIVATE_QUERY_MARKER},
            headers={"Content-Length": str(DEFAULT_BODY_LIMIT + 1)},
            content=b"{}",
        )
    assert response.status_code == 413
    assert_private_response(response)


def test_all_api_routers_disable_slash_redirects(client, monkeypatch, factory, settings):
    from open_learning import main

    routers = []
    original_router = main.APIRouter

    def record_router(*args, **kwargs):
        router = original_router(*args, **kwargs)
        routers.append(router)
        return router

    monkeypatch.setattr(main, "APIRouter", record_router)
    app = main.create_app(settings, factory)
    assert app.router.redirect_slashes is False
    assert routers and all(router.redirect_slashes is False for router in routers)
    for path in ("/health/", API + "/health/", API + "/feeds/"):
        response = client.get(path, params={"cursor": PRIVATE_QUERY_MARKER}, follow_redirects=False)
        assert response.status_code == 404
        assert "Location" not in response.headers
        assert PRIVATE_QUERY_MARKER not in response.text
    for path in ("/health", API + "/health", API + "/feeds"):
        assert client.get(path).status_code == 200
        response = client.get(path, headers={"Authorization": ""})
        assert response.status_code == (200 if path == "/health" else 401)


@pytest.mark.parametrize("root_path", ["", "/ol"])
@pytest.mark.parametrize("authenticated", [True, False])
@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/topics/"),
        ("GET", "/discover/"),
        ("GET", "/topics/pinned/"),
        ("POST", "/topics/pinned/"),
        ("DELETE", "/topics/pinned/missing/"),
    ],
)
def test_private_slash_routes_never_redirect_queries(
    client, root_path, authenticated, method, path
):
    marker = "private-slash-marker"
    with TestClient(client.app, root_path=root_path) as mounted:
        response = mounted.request(
            method,
            root_path + API + path,
            params={"limit" if path == "/topics/" else "cursor": marker},
            headers={"Authorization": "Bearer " + TOKEN if authenticated else ""},
            follow_redirects=False,
        )
    assert response.status_code == 404
    assert "Location" not in response.headers
    assert marker not in response.text
    assert response.headers.get("Cache-Control") == "no-store"
    assert response.headers.get("Pragma") == "no-cache"


@pytest.mark.parametrize(
    "method,path,status",
    [
        ("GET", "/topics/pinned", 405),
        ("POST", "/topics/pinned", 422),
        ("DELETE", "/topics/pinned/missing", 404),
    ],
)
def test_pinned_route_errors_are_no_store(client, method, path, status):
    response = client.request(method, API + path)
    assert response.status_code == status
    assert response.headers.get("Cache-Control") == "no-store"
    assert response.headers.get("Pragma") == "no-cache"


def test_pin_writes_and_auth_errors_are_no_store(client):
    params = {"cursor": PRIVATE_QUERY_MARKER}
    response = client.post(API + "/topics/pinned", json={"term": "orchid"}, params=params)
    assert response.status_code == 201
    pin_id = response.json()["id"]
    responses = [response, client.delete(API + "/topics/pinned/" + pin_id, params=params)]
    assert responses[-1].status_code == 204
    for method, path in [("POST", "/topics/pinned"), ("DELETE", "/topics/pinned/missing")]:
        response = client.request(method, API + path, headers={"Authorization": ""}, params=params)
        assert response.status_code == 401
        responses.append(response)
    for response in responses:
        assert_private_response(response)


@pytest.mark.parametrize("root_path", ["", "/ol"])
@pytest.mark.parametrize("path", ["/topics", "/topics/pinned", "/topics/pinned/example"])
def test_private_cache_middleware_preserves_upstream_message_and_headers(root_path, path):
    from open_learning.topics_api import TopicPrivacyMiddleware

    headers = [(b"x-existing", b"preserved")]
    original = {"type": "http.response.start", "status": 200, "headers": headers}
    sent = []

    async def upstream(scope, receive, send):
        await send(original)

    async def collect(message):
        sent.append(message)

    asyncio.run(
        TopicPrivacyMiddleware(upstream)(
            {
                "type": "http",
                "method": "GET",
                "path": root_path + API + path,
                "root_path": root_path,
            },
            None,
            collect,
        )
    )
    assert original == {
        "type": "http.response.start",
        "status": 200,
        "headers": [(b"x-existing", b"preserved")],
    }
    assert original["headers"] is headers
    assert sent[0] is not original and sent[0]["headers"] is not headers
    assert dict(sent[0]["headers"])[b"x-existing"] == b"preserved"
    assert dict(sent[0]["headers"])[b"cache-control"] == b"no-store"


@pytest.mark.parametrize("path", ["/feeds/", "/topics-extra/", "/discoverable/", "/discovery/"])
def test_private_cache_middleware_preserves_unrelated_slash_routes(path):
    from open_learning.topics_api import TopicPrivacyMiddleware

    original = {"type": "http.response.start", "status": 307, "headers": [(b"location", b"/feeds")]}
    sent = []

    async def upstream(scope, receive, send):
        await send(original)

    async def collect(message):
        sent.append(message)

    asyncio.run(
        TopicPrivacyMiddleware(upstream)(
            {"type": "http", "method": "GET", "path": API + path}, None, collect
        )
    )
    assert sent == [original] and sent[0] is original


@pytest.mark.parametrize("root_path", ["", "/ol"])
@pytest.mark.parametrize("path,parameter", [("/topics", "limit"), ("/discover", "sort")])
def test_mounted_private_query_errors_are_redacted_and_no_store(client, root_path, path, parameter):
    with TestClient(client.app, root_path=root_path) as mounted:
        response = mounted.get(
            root_path + API + path,
            params={parameter: PRIVATE_QUERY_MARKER, "cursor": PRIVATE_QUERY_MARKER},
            headers={"Authorization": "Bearer " + TOKEN},
        )
    assert response.status_code == 422
    assert_private_response(response)


@pytest.mark.parametrize(
    "path,params,authenticated,status",
    [
        ("/topics", {"limit": 0}, True, 422),
        ("/topics", {}, False, 401),
        ("/discover", {}, False, 401),
        ("/discover", {"cursor": "broken"}, True, 422),
        ("/discover", {"sort": "relevance", "cursor": "broken"}, True, 422),
    ],
)
def test_private_read_errors_are_no_store(client, path, params, authenticated, status):
    response = client.get(
        API + path, params=params, headers={} if authenticated else {"Authorization": ""}
    )
    assert response.status_code == status
    assert response.headers.get("Cache-Control") == "no-store"
    assert response.headers.get("Pragma") == "no-cache"


@pytest.mark.parametrize("version_only", [False, True])
def test_forget_clears_discovery_scores_matches_and_old_cursor(
    client, factory, settings, version_only
):
    document = import_note(client, factory, settings, text=("orchid gardening " * 30).strip())
    ids, _ = candidates(factory)
    first = client.get(API + "/discover", params={"sort": "relevance", "limit": 1}).json()
    assert first["items"][0]["score"] > 0
    assert "orchid" in first["items"][0]["matched_topics"]
    assert (
        client.delete(f"{API}/notes/{document}", params={"version_only": version_only}).status_code
        == 204
    )
    old = client.get(
        API + "/discover", params={"sort": "relevance", "cursor": first["next_cursor"]}
    )
    assert old.status_code == 409
    assert old.headers.get("Cache-Control") == "no-store"
    current = client.get(API + "/discover?sort=relevance").json()["items"]
    assert [item["id"] for item in current] == ids
    assert all(item["score"] == 0 and item["matched_topics"] == [] for item in current)


def test_title_overlap_scores_three_times_snippet_and_matches_cap_at_five(client, factory):
    ids, _ = candidates(
        factory, titles=("orchid", "astronomy", "orchid volcano café garden forest river")
    )
    with factory.begin() as db:
        db.get(DiscoveredItem, ids[1]).snippet = "orchid"
    for term in ("orchid", "volcano", "café", "garden", "forest", "river"):
        assert client.post(API + "/topics/pinned", json={"term": term}).status_code == 201
    rows = {
        item["id"]: item for item in client.get(API + "/discover?sort=relevance").json()["items"]
    }
    assert rows[ids[0]]["score"] == 3 * rows[ids[1]]["score"] > 0
    assert len(rows[ids[2]]["matched_topics"]) == 5
    assert rows[ids[2]]["score"] == 6 * rows[ids[0]]["score"]


@pytest.mark.parametrize(
    "key,value",
    [
        ("offset", None),
        ("offset", []),
        ("offset", True),
        ("offset", 0),
        ("offset", -1),
        ("offset", 500),
        ("offset", 1e100),
        ("anchor", None),
        ("anchor", []),
        ("anchor", ["bad", "bad"]),
        ("anchor", [3, {}]),
        ("fingerprint", []),
        ("fingerprint", {}),
        ("fingerprint", "a" * 63),
        ("fingerprint", "z" * 64),
        ("status", "dismissed"),
        ("sort", "newest"),
        ("v", 2),
    ],
)
def test_malformed_relevance_cursor_types_and_bounds_are_fixed_422(client, factory, key, value):
    candidates(factory)
    cursor = client.get(API + "/discover?sort=relevance&limit=1").json()["next_cursor"]
    state = json.loads(base64.urlsafe_b64decode(cursor))
    state[key] = value
    malformed = base64.urlsafe_b64encode(json.dumps(state).encode()).decode()
    response = client.get(API + "/discover", params={"sort": "relevance", "cursor": malformed})
    assert response.status_code == 422
    assert response.json()["detail"] == "Invalid discovery cursor"


def test_richer_corpus_order_is_deterministic_and_document_counts_ignore_segments():
    from open_learning.topics import CorpusText, derive_topics

    corpus = (
        CorpusText("a", "orchid garden café 東京大学 orchid", 1),
        CorpusText("a", "orchid forest", 2),
        CorpusText("b", "garden forest café", 1),
        CorpusText("c", "volcano river 東京大学", 1),
    )
    pins = ("river", "orchid garden")
    expected = derive_topics(corpus, pins)
    assert derive_topics(tuple(reversed(corpus)), tuple(reversed(pins))) == expected
    rows = {row.term: row for row in expected}
    assert rows["orchid"].count == 1 and rows["forest"].count == 2
    assert rows["orchid garden"].weight >= 10
