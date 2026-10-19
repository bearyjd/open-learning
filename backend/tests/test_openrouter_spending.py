"""OpenRouter contract tests; every inference response is supplied by a mock transport."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from conftest import PASSAGE, article_fetcher
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from open_learning.ai import ModelClient, ModelError
from open_learning.config import Settings
from open_learning.main import create_app
from open_learning.models import CloudAttempt, CloudBudget, CloudSettlement, now
from open_learning.openrouter import fetch_quote, inspect_pending
from open_learning.spending import (
    SpendingError,
    record_generation,
    reserve_amount,
    reserve_attempt,
    settle_attempt,
)
from open_learning.worker import run_once

TOKEN = "0123456789abcdef0123456789abcdef"


def cloud_settings(**changes):
    values = {
        "api_token": TOKEN,
        "ai_backend": "openrouter",
        "cloud_opt_in": True,
        "openrouter_api_key": "sk-or-test-only",
        "openrouter_model": "openai/gpt-4.1-mini",
        "openrouter_provider": "azure/swedencentral",
        "openrouter_request_budget_usd": "0.5",
        "openrouter_lifetime_budget_usd": "2",
        "openrouter_key_limit_usd": "2",
        "openrouter_request_fee_cap_usd": "0.01",
    }
    values.update(changes)
    return Settings(**values)


@pytest.mark.parametrize(
    "field,value",
    [
        ("cloud_opt_in", False),
        ("openrouter_api_key", None),
        ("openrouter_model", "openai/gpt-4.1-mini:free"),
        ("openrouter_provider", "Azure Sweden Central"),
        ("openrouter_request_budget_usd", "Infinity"),
        ("openrouter_request_budget_usd", "0"),
        ("openrouter_request_fee_cap_usd", None),
        ("openrouter_request_fee_cap_usd", "-0.01"),
    ],
)
def test_cloud_profile_rejects_incomplete_or_unsafe_settings(field, value):
    with pytest.raises(ValidationError):
        cloud_settings(**{field: value})


def test_cloud_config_error_string_masks_api_key():
    sentinel = "sentinel-openrouter-key-do-not-log"
    with pytest.raises(ValidationError) as caught:
        cloud_settings(openrouter_api_key=sentinel, openrouter_provider="Display Name")
    assert sentinel not in str(caught.value)


def test_full_context_reservation_ceil_to_twelve_places():
    assert reserve_amount(
        1000,
        300,
        Decimal("0.000000111111"),
        Decimal("0.000000222222"),
        Decimal("0.001"),
    ) == Decimal("0.001177777600")


def metadata_responses(*, endpoint_changes=None, zdr=True, key_changes=None):
    endpoint = {
        "model_id": "openai/gpt-4.1-mini",
        "tag": "azure/swedencentral",
        "provider_name": "Azure",
        "service_tier": None,
        "context_length": 1000,
        "max_prompt_tokens": None,
        "max_completion_tokens": 500,
        "supported_parameters": [
            "temperature",
            "response_format",
            "structured_outputs",
            "max_completion_tokens",
        ],
        "pricing": {
            "prompt": "0.0000004",
            "completion": "0.0000016",
            "input_cache_read": "0.0000001",
            "discount": 0,
        },
    }
    endpoint.update(endpoint_changes or {})
    key = {
        "limit": 2,
        "limit_reset": None,
        "limit_remaining": 2,
        "usage": 0,
        "is_management_key": False,
        "is_provisioning_key": False,
        "expires_at": None,
    }
    key.update(key_changes or {})
    return {
        "/api/v1/models": {
            "data": [
                {
                    "id": "openai/gpt-4.1-mini",
                    "architecture": {
                        "input_modalities": ["text", "image"],
                        "output_modalities": ["text"],
                    },
                }
            ]
        },
        "/api/v1/models/openai/gpt-4.1-mini/endpoints": {
            "data": {"id": "openai/gpt-4.1-mini", "endpoints": [endpoint]}
        },
        "/api/v1/endpoints/zdr": {"data": [endpoint] if zdr else []},
        "/api/v1/key": {"data": key},
    }


def metadata_client(responses, seen):
    def respond(request):
        seen.append((request.method, str(request.url), request.headers.get("Authorization")))
        assert request.method == "GET"
        return httpx.Response(200, json=responses[request.url.path])

    return httpx.Client(transport=httpx.MockTransport(respond), trust_env=False)


def test_fresh_metadata_selects_exact_zdr_tag_and_reserves_full_context():
    seen = []
    with metadata_client(metadata_responses(), seen) as client:
        quote, remaining = fetch_quote(client, cloud_settings())
    assert quote.provider_tag == "azure/swedencentral"
    assert quote.output_tokens == 500
    assert quote.reservation == Decimal("0.0112")
    assert remaining == Decimal("2")
    assert [url for _, url, _ in seen] == [
        "https://openrouter.ai/api/v1/models",
        "https://openrouter.ai/api/v1/models/openai/gpt-4.1-mini/endpoints",
        "https://openrouter.ai/api/v1/endpoints/zdr",
        "https://openrouter.ai/api/v1/key",
    ]
    assert all(auth == "Bearer sk-or-test-only" for _, _, auth in seen)


@pytest.mark.parametrize(
    "change",
    [
        {"zdr": False},
        {"endpoint_changes": {"tag": "azure"}},
        {"endpoint_changes": {"pricing": {"prompt": "NaN", "completion": "0.1"}}},
        {
            "endpoint_changes": {
                "pricing": {"prompt": "0.1", "completion": "0.1", "input_cache_write": "0.1"}
            }
        },
        {"endpoint_changes": {"supported_parameters": ["temperature", "response_format"]}},
        {"key_changes": {"limit_reset": "monthly"}},
        {"key_changes": {"limit_remaining": None}},
        {"key_changes": {"limit": 3}},
        {"endpoint_changes": {"service_tier": "priority"}},
        {
            "endpoint_changes": {
                "pricing": {"prompt": "0.1", "completion": "0.1", "request": "0.02"}
            }
        },
        {"endpoint_changes": {"pricing": {"prompt": "0.1", "completion": "0.1", "overrides": {}}}},
    ],
)
def test_unknown_or_unsafe_metadata_fails_before_dispatch(change):
    seen = []
    with metadata_client(metadata_responses(**change), seen) as client:
        with pytest.raises(ValueError):
            fetch_quote(client, cloud_settings())
    assert all(method == "GET" for method, _, _ in seen)


@pytest.mark.parametrize("variant", ["duplicate", "descendant", "zdr-mismatch", "nontext"])
def test_exact_route_rejects_ambiguous_or_inconsistent_endpoint(variant):
    responses = metadata_responses()
    endpoint = responses["/api/v1/models/openai/gpt-4.1-mini/endpoints"]["data"]["endpoints"][0]
    if variant == "duplicate":
        responses["/api/v1/models/openai/gpt-4.1-mini/endpoints"]["data"]["endpoints"].append(
            endpoint.copy()
        )
    elif variant == "descendant":
        responses["/api/v1/models/openai/gpt-4.1-mini/endpoints"]["data"]["endpoints"].append(
            {**endpoint, "tag": "azure/swedencentral/tier"}
        )
    elif variant == "zdr-mismatch":
        responses["/api/v1/endpoints/zdr"]["data"][0] = {
            **endpoint,
            "pricing": {**endpoint["pricing"], "prompt": "0.0000005"},
        }
    else:
        responses["/api/v1/models"]["data"][0]["architecture"]["input_modalities"] = ["image"]
    with metadata_client(responses, []) as client:
        with pytest.raises(ValueError):
            fetch_quote(client, cloud_settings())


def test_effective_budget_is_tighter_than_key_remainder():
    seen = []
    responses = metadata_responses(
        key_changes={
            "effective_budget": {"remaining_usd": "0.015"},
            "budgets": [{"remaining_usd": "0.012"}],
        }
    )
    with metadata_client(responses, seen) as client:
        _, remaining = fetch_quote(client, cloud_settings())
    assert remaining == Decimal("0.012")


def test_metadata_freshness_starts_before_first_get():
    first_seen = []
    responses = metadata_responses()

    def respond(request):
        first_seen.append(datetime.now(UTC))
        return httpx.Response(200, json=responses[request.url.path])

    with httpx.Client(transport=httpx.MockTransport(respond), trust_env=False) as client:
        quote, _ = fetch_quote(client, cloud_settings())
    assert len(first_seen) == 4
    assert quote.checked_at <= first_seen[0]


@pytest.fixture
def pg_factory(factory):
    if factory.kw["bind"].dialect.name != "postgresql":
        pytest.skip("PostgreSQL ledger contract")
    with factory.begin() as db:
        if db.get(CloudBudget, 1) is None:
            db.add(
                CloudBudget(
                    id=1,
                    spent=Decimal(0),
                    held=Decimal(0),
                    debt=Decimal(0),
                    created_at=now(),
                    updated_at=now(),
                )
            )
    return factory


def quote_for_test():
    with metadata_client(metadata_responses(), []) as client:
        return fetch_quote(client, cloud_settings())[0]


def test_parallel_admission_allows_only_one_dispatch_at_last_allowance(pg_factory):
    settings = cloud_settings(
        openrouter_request_budget_usd="0.02",
        openrouter_lifetime_budget_usd="0.02",
        openrouter_key_limit_usd="0.02",
    )
    quote = quote_for_test()

    def attempt(_):
        try:
            return reserve_attempt(pg_factory, settings, quote, Decimal("0.02"), 100)
        except SpendingError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        accepted = list(pool.map(attempt, range(2)))
    assert sum(item is not None for item in accepted) == 1
    with pg_factory() as db:
        budget = db.get(CloudBudget, 1)
        assert budget.held == Decimal("0.0112")
        assert budget.spent == 0
        assert len(list(db.scalars(select(CloudAttempt)))) == 1


def test_reservation_survives_outer_rollback_and_overrun_creates_debt(pg_factory):
    settings = cloud_settings()
    quote = quote_for_test()
    with pg_factory() as outer:
        outer.begin()
        attempt = reserve_attempt(pg_factory, settings, quote, Decimal("2"), 100)
        outer.rollback()
    with pg_factory() as db:
        assert db.get(CloudAttempt, attempt) is not None
        assert db.get(CloudBudget, 1).held == Decimal("0.0112")
    record_generation(pg_factory, attempt, "gen-test-1")
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(
            pool.map(
                lambda _: settle_attempt(pg_factory, attempt, "gen-test-1", Decimal("0.02")),
                range(2),
            )
        )
    assert sorted(outcomes) == [False, True]
    with pytest.raises(SpendingError, match="differs"):
        settle_attempt(pg_factory, attempt, "gen-test-1", Decimal("0.03"))
    with pytest.raises(SpendingError, match="identity mismatch"):
        record_generation(pg_factory, attempt, "gen-other")
    with pg_factory() as db:
        budget = db.get(CloudBudget, 1)
        assert budget.held == 0
        assert budget.spent == Decimal("0.02")
        assert budget.debt == Decimal("0.0088")
        assert db.get(CloudSettlement, attempt).actual == Decimal("0.02")
    with pytest.raises(SpendingError):
        reserve_attempt(pg_factory, settings, quote, Decimal("2"), 100)


def test_limit_change_cannot_reset_spending(pg_factory):
    quote = quote_for_test()
    settings = cloud_settings()
    first = reserve_attempt(pg_factory, settings, quote, Decimal("2"), 100)
    assert first
    changed = cloud_settings(openrouter_lifetime_budget_usd="3", openrouter_key_limit_usd="2")
    with pytest.raises(SpendingError, match="persisted limit"):
        reserve_attempt(pg_factory, changed, quote, Decimal("2"), 100)
    with pg_factory() as db:
        assert db.get(CloudBudget, 1).held == Decimal("0.0112")


def test_key_rotation_preserves_previous_holds(pg_factory):
    quote = quote_for_test()
    first = reserve_attempt(pg_factory, cloud_settings(), quote, Decimal("2"), 100)
    rotated = cloud_settings(openrouter_api_key="sk-or-test-rotated")
    second = reserve_attempt(pg_factory, rotated, quote, Decimal("2"), 100)
    with pg_factory() as db:
        assert first != second
        assert db.get(CloudAttempt, first).key_sha256 != db.get(CloudAttempt, second).key_sha256
        assert db.get(CloudBudget, 1).held == Decimal("0.0224")
        assert db.get(CloudBudget, 1).spent == 0


def test_stale_metadata_refuses_reservation(pg_factory):
    stale = replace(quote_for_test(), checked_at=datetime.now(UTC) - timedelta(seconds=31))
    with pytest.raises(SpendingError, match="stale"):
        reserve_attempt(pg_factory, cloud_settings(), stale, Decimal("2"), 100)
    with pg_factory() as db:
        assert db.get(CloudBudget, 1).held == 0
        assert db.scalar(select(CloudAttempt)) is None


@pytest.mark.parametrize(
    "table,column",
    [
        ("cloud_budget", "lifetime_limit"),
        ("cloud_budget", "spent"),
        ("cloud_budget", "held"),
        ("cloud_budget", "debt"),
        ("cloud_attempt", "prompt_rate"),
        ("cloud_attempt", "completion_rate"),
        ("cloud_attempt", "request_fee_cap"),
        ("cloud_attempt", "reserved"),
        ("cloud_settlement", "actual"),
    ],
)
def test_database_rejects_nan_in_every_money_column(pg_factory, table, column):
    attempt = reserve_attempt(pg_factory, cloud_settings(), quote_for_test(), Decimal("2"), 100)
    record_generation(pg_factory, attempt, "gen-nan-check")
    settle_attempt(pg_factory, attempt, "gen-nan-check", Decimal("0.001"))
    # Column and table names are fixed test constants, not request values.
    with pytest.raises(IntegrityError):
        with pg_factory.begin() as db:
            db.execute(text(f"UPDATE {table} SET {column} = CAST('NaN' AS NUMERIC)"))


def install_cloud_transport(monkeypatch, responses, post):
    original = httpx.Client

    def respond(request):
        assert request.url.host == "openrouter.ai"
        assert request.url.scheme == "https"
        assert request.headers["Authorization"] == "Bearer sk-or-test-only"
        if request.method == "GET":
            return httpx.Response(200, json=responses[request.url.path])
        assert request.method == "POST"
        assert request.url.path == "/api/v1/chat/completions"
        return post(request)

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original(
            transport=httpx.MockTransport(respond),
            **kwargs,
        ),
    )


@pytest.mark.parametrize("valid", [True, False])
def test_cloud_completion_commits_hold_before_post_and_settles_before_evidence_validation(
    pg_factory, monkeypatch, valid
):
    settings = cloud_settings()
    posts = []

    def post(request):
        posts.append(request)
        with pg_factory() as db:
            assert db.get(CloudBudget, 1).held == Decimal("0.0112")
            assert db.get(CloudBudget, 1).spent == 0
            assert len(list(db.scalars(select(CloudAttempt)))) == 1
        body = json.loads(request.content, parse_float=Decimal)
        assert body["model"] == "openai/gpt-4.1-mini"
        assert body["max_completion_tokens"] == 500
        assert "max_tokens" not in body
        assert body["stream"] is False
        assert body["usage"] == {"include": True}
        assert body["provider"] == {
            "only": ["azure/swedencentral"],
            "allow_fallbacks": False,
            "require_parameters": True,
            "data_collection": "deny",
            "zdr": True,
            "max_price": {
                "prompt": Decimal("0.4"),
                "completion": Decimal("1.6"),
                "request": Decimal("0.01"),
            },
        }
        content = {
            "insights": [
                {
                    "evidence_id": "e0" if valid else "e999",
                    "content": {
                        "title": "Retrieval practice",
                        "idea": "Recall before rereading.",
                        "explanation": "Trying to recall strengthens memory.",
                        "why_it_matters": "Study more effectively.",
                        "tags": ["learning"],
                    },
                }
            ]
        }
        return httpx.Response(
            200,
            json={
                "id": "gen-cloud-1",
                "model": settings.openrouter_model,
                "usage": {"cost": "0.001"},
                "choices": [{"message": {"content": json.dumps(content)}}],
            },
        )

    install_cloud_transport(monkeypatch, metadata_responses(), post)
    model = ModelClient(settings, pg_factory)
    if valid:
        assert model.insights([PASSAGE]).insights[0].evidence == PASSAGE
    else:
        with pytest.raises(ModelError, match="unknown source span"):
            model.insights([PASSAGE])
    assert len(posts) == 1
    with pg_factory() as db:
        budget = db.get(CloudBudget, 1)
        assert budget.held == 0
        assert budget.spent == Decimal("0.001")
        assert db.scalar(select(CloudSettlement)).generation_id == "gen-cloud-1"


@pytest.mark.parametrize(
    "mode",
    [
        "http-error",
        "missing-usage",
        "invalid-json",
        "wrong-model",
        "invalid-id",
        "negative-cost",
        "nonfinite-cost",
    ],
)
def test_uncertain_response_retains_full_hold(pg_factory, monkeypatch, mode):
    settings = cloud_settings()

    def post(request):
        if mode == "http-error":
            return httpx.Response(503)
        if mode == "invalid-json":
            return httpx.Response(200, content=b"{")
        result = {
            "id": None if mode == "invalid-id" else "gen-uncertain",
            "model": "other/model" if mode == "wrong-model" else settings.openrouter_model,
            "choices": [],
        }
        if mode == "negative-cost":
            result["usage"] = {"cost": "-0.01"}
        elif mode == "nonfinite-cost":
            result["usage"] = {"cost": "NaN"}
        return httpx.Response(200, json=result)

    install_cloud_transport(monkeypatch, metadata_responses(), post)
    with pytest.raises(ModelError, match="unavailable"):
        ModelClient(settings, pg_factory).insights([PASSAGE])
    with pg_factory() as db:
        budget = db.get(CloudBudget, 1)
        assert budget.held == Decimal("0.0112")
        assert budget.spent == 0
        assert db.scalar(select(CloudSettlement)) is None
        attempt = db.scalar(select(CloudAttempt))
        assert attempt.generation_id == (
            "gen-uncertain"
            if mode
            in {
                "missing-usage",
                "wrong-model",
                "negative-cost",
                "nonfinite-cost",
            }
            else None
        )


def _insight_content(evidence_id="e0"):
    return {
        "insights": [
            {
                "evidence_id": evidence_id,
                "content": {
                    "title": "Retrieval practice",
                    "idea": "Recall before rereading.",
                    "explanation": "Trying to recall strengthens memory.",
                    "why_it_matters": "Study more effectively.",
                    "tags": ["learning"],
                },
            }
        ]
    }


def test_cloud_ingest_and_authenticated_tutor_use_same_ledger(pg_factory, monkeypatch):
    settings = cloud_settings()
    posts = []

    def post(request):
        body = json.loads(request.content)
        posts.append(body)
        with pg_factory() as db:
            assert db.get(CloudBudget, 1).held == Decimal("0.0112")
        if body["response_format"]["json_schema"]["name"] == "_InsightDraftBatch":
            content = _insight_content()
        else:
            content = {
                "evidence_id": "e0",
                "content": {
                    "answer": "Recall the idea before rereading.",
                    "question": "How does retrieval practice support memory?",
                },
            }
        return httpx.Response(
            200,
            json={
                "id": f"gen-integration-{len(posts)}",
                "model": settings.openrouter_model,
                "usage": {"cost": "0.001"},
                "choices": [{"message": {"content": json.dumps(content)}}],
            },
        )

    install_cloud_transport(monkeypatch, metadata_responses(), post)
    with TestClient(create_app(settings, pg_factory)) as client:
        client.headers["Authorization"] = "Bearer " + TOKEN
        job = client.post("/api/v1/ingest/url", json={"url": "https://example.com/article"}).json()
        assert run_once(pg_factory, settings, article_fetcher)
        assert client.get("/api/v1/jobs/" + job["id"]).json()["status"] == "completed"
        insight = client.get("/api/v1/feed/today").json()[0]
        assert insight["model"] == settings.openrouter_model
        assert insight["model_endpoint"] == "https://openrouter.ai/api/v1"
        tutor = client.post(
            "/api/v1/tutor/chat",
            json={
                "insight_id": insight["id"],
                "message": "Recall before rereading.",
            },
        )
        assert tutor.status_code == 200
        assert tutor.json()["citations"][0]["passage"] in article_fetcher("", settings).text
    assert len(posts) == 2
    with pg_factory() as db:
        assert db.get(CloudBudget, 1).held == 0
        assert db.get(CloudBudget, 1).spent == Decimal("0.002")
        assert len(list(db.scalars(select(CloudSettlement)))) == 2


def test_failed_ingest_retry_gets_distinct_paid_attempt(pg_factory, monkeypatch):
    settings = cloud_settings()
    state = {"valid": False, "posts": 0}

    def post(request):
        state["posts"] += 1
        content = _insight_content("e0" if state["valid"] else "e999")
        return httpx.Response(
            200,
            json={
                "id": f"gen-retry-{state['posts']}",
                "model": settings.openrouter_model,
                "usage": {"cost": "0.001"},
                "choices": [{"message": {"content": json.dumps(content)}}],
            },
        )

    install_cloud_transport(monkeypatch, metadata_responses(), post)
    with TestClient(create_app(settings, pg_factory)) as client:
        client.headers["Authorization"] = "Bearer " + TOKEN
        job = client.post("/api/v1/ingest/url", json={"url": "https://example.com/retry"}).json()
        assert run_once(pg_factory, settings, article_fetcher)
        assert client.get("/api/v1/jobs/" + job["id"]).json()["status"] == "failed"
        assert client.get("/api/v1/feed/today").json() == []
        state["valid"] = True
        assert client.post("/api/v1/jobs/" + job["id"] + "/retry").status_code == 200
        assert run_once(pg_factory, settings, article_fetcher)
        assert client.get("/api/v1/jobs/" + job["id"]).json()["status"] == "completed"
    assert state["posts"] == 2
    with pg_factory() as db:
        assert len(list(db.scalars(select(CloudAttempt)))) == 2
        assert len(list(db.scalars(select(CloudSettlement)))) == 2
        assert db.get(CloudBudget, 1).spent == Decimal("0.002")


@pytest.mark.parametrize("mode", ["complete", "ambiguous", "wrong-id", "incomplete", "not-found"])
def test_bounded_diagnostic_never_releases_unverified_endpoint_hold(pg_factory, mode):
    settings = cloud_settings()
    quote = quote_for_test()
    if mode == "ambiguous":
        quote = replace(quote, provider_name_unique=False)
    attempt = reserve_attempt(pg_factory, settings, quote, Decimal("2"), 100)
    record_generation(pg_factory, attempt, "gen-pending")
    requests = []

    def respond(request):
        requests.append(str(request.url))
        assert request.method == "GET"
        assert request.url.path == "/api/v1/generation"
        assert request.url.params["id"] == "gen-pending"
        if mode == "not-found":
            return httpx.Response(404)
        data = {
            "id": "gen-other" if mode == "wrong-id" else "gen-pending",
            "model": settings.openrouter_model,
            "provider_name": "Azure",
            "is_byok": False,
            "finish_reason": None if mode == "incomplete" else "stop",
            "total_cost": "0.002",
        }
        return httpx.Response(200, json={"data": data})

    with httpx.Client(transport=httpx.MockTransport(respond), trust_env=False) as client:
        first = inspect_pending(pg_factory, settings, client=client)
        assert len(first) == 1
        assert first[0]["status"] == (
            "exact_endpoint_unverified"
            if mode in {"complete", "ambiguous"}
            else "identity_or_completion_unverified"
            if mode in {"wrong-id", "incomplete"}
            else "unavailable"
        )
        assert len(inspect_pending(pg_factory, settings, client=client)) == 1
    assert len(requests) == 2
    with pg_factory() as db:
        budget = db.get(CloudBudget, 1)
        assert budget.held == Decimal("0.0112")
        assert budget.spent == 0
