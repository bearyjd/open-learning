"""Strict, text-only OpenRouter transport. No inference is sent before a committed hold."""

import argparse
import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import simplejson
from sqlalchemy import select

from .models import CloudAttempt, CloudSettlement
from .spending import (
    Quote,
    SpendingError,
    money,
    record_generation,
    reserve_attempt,
    settle_attempt,
)

BASE = "https://openrouter.ai/api/v1"
METADATA_MAX = 8 * 1024 * 1024
RESPONSE_MAX = 2 * 1024 * 1024
REQUEST_MAX = 256 * 1024


class CloudError(ValueError):
    pass


def _no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CloudError("Duplicate upstream JSON property")
        result[key] = value
    return result


def _decode(raw: bytes):
    try:
        return json.loads(
            raw,
            parse_float=Decimal,
            parse_constant=lambda _: (_ for _ in ()).throw(CloudError("Invalid upstream number")),
            object_pairs_hook=_no_duplicate_keys,
        )
    except (UnicodeError, ValueError, TypeError):
        raise CloudError("Invalid upstream JSON") from None


def _request(
    client,
    method: str,
    path: str,
    key: str,
    *,
    body: bytes | None = None,
    params: dict | None = None,
    max_bytes=METADATA_MAX,
):
    if method not in {"GET", "POST"} or not path.startswith("/") or path.startswith("//"):
        raise CloudError("Invalid upstream request")
    headers = {"Authorization": "Bearer " + key}
    if body is not None:
        headers["Content-Type"] = "application/json"
    try:
        with client.stream(
            method,
            BASE + path,
            headers=headers,
            content=body,
            params=params,
            follow_redirects=False,
        ) as response:
            if response.status_code != 200:
                raise CloudError("OpenRouter request failed")
            result = bytearray()
            for chunk in response.iter_bytes():
                result.extend(chunk)
                if len(result) > max_bytes:
                    raise CloudError("OpenRouter response exceeds size bound")
            return bytes(result)
    except httpx.HTTPError:
        raise CloudError("OpenRouter request failed") from None


def _positive_int(value):
    if type(value) is not int or value <= 0 or value > 100_000_000:
        raise CloudError("Invalid upstream token bound")
    return value


def _price(value) -> Decimal:
    # Catalog prices are documented as decimal strings; reject floats and hidden rounding.
    if not isinstance(value, str):
        raise CloudError("Invalid upstream price")
    try:
        return money(value)
    except SpendingError:
        raise CloudError("Invalid upstream price") from None


def _data(raw):
    value = _decode(raw)
    if not isinstance(value, dict) or "data" not in value:
        raise CloudError("Missing upstream data")
    return value["data"]


def fetch_quote(client: httpx.Client, settings) -> tuple[Quote, Decimal]:
    """Four fresh official GETs; every unknown billable or identity field fails closed."""
    if settings.ai_backend != "openrouter":
        raise CloudError("Cloud profile is not enabled")
    key = settings.openrouter_api_key.get_secret_value()
    model = settings.openrouter_model
    checked_at = datetime.now(UTC)  # The oldest metadata must still be fresh at admission.
    catalog_raw = _request(client, "GET", "/models", key)
    endpoints_raw = _request(client, "GET", "/models/" + model + "/endpoints", key)
    zdr_raw = _request(client, "GET", "/endpoints/zdr", key)
    key_raw = _request(client, "GET", "/key", key)
    catalog = _data(catalog_raw)
    if not isinstance(catalog, list):
        raise CloudError("Invalid model catalog")
    matches = [item for item in catalog if isinstance(item, dict) and item.get("id") == model]
    if len(matches) != 1:
        raise CloudError("Exact model not found")
    architecture = matches[0].get("architecture")
    input_modalities = architecture.get("input_modalities") if isinstance(architecture, dict) else None
    output_modalities = architecture.get("output_modalities") if isinstance(architecture, dict) else None
    if (
        not isinstance(input_modalities, list)
        or not isinstance(output_modalities, list)
        or any(not isinstance(item, str) for item in input_modalities + output_modalities)
        or "text" not in input_modalities
        or "text" not in output_modalities
    ):
        raise CloudError("Model does not support text input and output")
    endpoints = _data(endpoints_raw)
    if (
        not isinstance(endpoints, dict)
        or endpoints.get("id") != model
        or not isinstance(endpoints.get("endpoints"), list)
    ):
        raise CloudError("Invalid model endpoints")
    rows = endpoints["endpoints"]
    if any(not isinstance(row, dict) or row.get("model_id") != model for row in rows):
        raise CloudError("Inconsistent model endpoints")
    tag = settings.openrouter_provider
    if any(isinstance(row.get("tag"), str) and row["tag"].startswith(tag + "/") for row in rows):
        raise CloudError("Provider tag has unpriced descendants")
    matches = [row for row in rows if row.get("tag") == tag]
    if len(matches) != 1 or any(not row.get("tag") for row in rows):
        raise CloudError("Exact provider tag unavailable")
    endpoint = matches[0]
    zdr = _data(zdr_raw)
    if not isinstance(zdr, list) or any(not isinstance(row, dict) for row in zdr):
        raise CloudError("Invalid ZDR catalog")
    zdr_matches = [row for row in zdr if row.get("model_id") == model and row.get("tag") == tag]
    if len(zdr_matches) != 1 or zdr_matches[0].get("provider_name") != endpoint.get(
        "provider_name"
    ):
        raise CloudError("Exact endpoint lacks ZDR verification")
    for field in (
        "context_length",
        "max_prompt_tokens",
        "max_completion_tokens",
        "pricing",
        "supported_parameters",
    ):
        if zdr_matches[0].get(field) != endpoint.get(field):
            raise CloudError("ZDR endpoint metadata differs from model endpoint")
    if endpoint.get("service_tier") is not None or zdr_matches[0].get("service_tier") is not None:
        raise CloudError("Provider service tier is unsupported")
    if not isinstance(endpoint.get("provider_name"), str) or not endpoint["provider_name"]:
        raise CloudError("Provider identity is missing")
    parameters = endpoint.get("supported_parameters")
    if not isinstance(parameters, list) or any(not isinstance(p, str) for p in parameters):
        raise CloudError("Invalid provider parameters")
    needed = {"structured_outputs", "response_format", "temperature"}
    if not needed.issubset(parameters):
        raise CloudError("Provider does not support strict structured output")
    token_parameter = "max_tokens" if "max_tokens" in parameters else "max_completion_tokens"
    if token_parameter not in parameters:
        raise CloudError("Provider lacks an output token cap")
    context_length = _positive_int(endpoint.get("context_length"))
    completion_max = _positive_int(endpoint.get("max_completion_tokens"))
    prompt_max = endpoint.get("max_prompt_tokens")
    if prompt_max is not None:
        _positive_int(prompt_max)
    output_tokens = min(settings.openrouter_output_tokens, completion_max)
    pricing = endpoint.get("pricing")
    if not isinstance(pricing, dict):
        raise CloudError("Provider price is missing")
    allowed = {
        "prompt",
        "completion",
        "request",
        "input_cache_read",
        "discount",
        "image",
        "web_search",
        "audio",
        "input_audio",
        "output_audio",
    }
    if set(pricing) - allowed:
        raise CloudError("Unknown provider billable component")
    prompt_rate, completion_rate = _price(pricing.get("prompt")), _price(pricing.get("completion"))
    if "input_cache_read" in pricing and _price(pricing["input_cache_read"]) > prompt_rate:
        raise CloudError("Cache read costs more than reserved prompt rate")
    if "discount" in pricing and pricing["discount"] not in (0, "0", None):
        raise CloudError("Unsupported price discount")
    for inactive in ("image", "web_search", "audio", "input_audio", "output_audio"):
        if inactive in pricing:
            _price(pricing[inactive])
    fee_cap = settings.openrouter_request_fee_cap_usd
    if "request" in pricing and _price(pricing["request"]) > fee_cap:
        raise CloudError("Provider request fee exceeds configured cap")
    key_data = _data(key_raw)
    if not isinstance(key_data, dict) or key_data.get("limit_reset", "missing") is not None:
        raise CloudError("Key needs a nonresetting spending limit")
    if (
        key_data.get("is_management_key") is not False
        or key_data.get("is_provisioning_key") is not False
    ):
        raise CloudError("Dedicated inference key required")
    try:
        limit = money(key_data.get("limit"))
        remaining = money(key_data.get("limit_remaining"))
        usage = money(key_data.get("usage"))
    except SpendingError:
        raise CloudError("Invalid key limit metadata") from None
    if (
        limit != settings.openrouter_key_limit_usd
        or limit == 0
        or remaining > limit
        or usage > limit
    ):
        raise CloudError("Key limit differs from configured limit")
    effective = key_data.get("effective_budget")
    if effective is not None:
        if not isinstance(effective, dict):
            raise CloudError("Invalid effective key budget")
        try:
            remaining = min(remaining, money(effective.get("remaining_usd")))
        except SpendingError:
            raise CloudError("Invalid effective key budget") from None
    budgets = key_data.get("budgets", [])
    if not isinstance(budgets, list) or any(not isinstance(item, dict) for item in budgets):
        raise CloudError("Invalid key budgets")
    for budget in budgets:
        try:
            remaining = min(remaining, money(budget.get("remaining_usd")))
        except SpendingError:
            raise CloudError("Invalid key budgets") from None
    expiry = key_data.get("expires_at")
    if expiry is not None:
        if not isinstance(expiry, str):
            raise CloudError("Invalid key expiry")
        try:
            expires_at = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
        except ValueError:
            raise CloudError("Invalid key expiry") from None
        if expires_at.tzinfo is None or expires_at <= datetime.now(UTC):
            raise CloudError("Cloud key is expired")
    metadata_hash = hashlib.sha256(catalog_raw + endpoints_raw + zdr_raw + key_raw).hexdigest()
    provider_name_unique = (
        sum(row.get("provider_name") == endpoint["provider_name"] for row in rows) == 1
    )
    return Quote(
        model=model,
        provider_tag=tag,
        provider_name=endpoint["provider_name"],
        provider_name_unique=provider_name_unique,
        output_parameter=token_parameter,
        context_length=context_length,
        output_tokens=output_tokens,
        prompt_rate=prompt_rate,
        completion_rate=completion_rate,
        request_fee_cap=fee_cap,
        metadata_sha256=metadata_hash,
        checked_at=checked_at,
    ), remaining


def complete(settings, factory, payload: dict) -> str:
    key = settings.openrouter_api_key.get_secret_value()
    with httpx.Client(
        timeout=settings.ai_timeout_seconds, trust_env=False, follow_redirects=False
    ) as client:
        quote, remaining = fetch_quote(client, settings)
        request = dict(payload)
        request.pop("max_tokens", None)
        request.update(
            {
                "model": quote.model,
                "stream": False,
                "usage": {"include": True},
                quote.output_parameter: quote.output_tokens,
                "provider": {
                    "only": [quote.provider_tag],
                    "allow_fallbacks": False,
                    "require_parameters": True,
                    "data_collection": "deny",
                    "zdr": True,
                    "max_price": {
                        "prompt": quote.prompt_rate * 1_000_000,
                        "completion": quote.completion_rate * 1_000_000,
                        "request": quote.request_fee_cap,
                    },
                },
            }
        )
        try:
            body = simplejson.dumps(
                request, use_decimal=True, allow_nan=False, separators=(",", ":")
            ).encode("utf-8")
        except (TypeError, ValueError):
            raise CloudError("Invalid cloud request") from None
        if len(body) > REQUEST_MAX:
            raise CloudError("Cloud request exceeds size bound")
        attempt_id = reserve_attempt(factory, settings, quote, remaining, len(body))
        response_raw = _request(
            client, "POST", "/chat/completions", key, body=body, max_bytes=RESPONSE_MAX
        )
        result = _decode(response_raw)
        if not isinstance(result, dict):
            raise CloudError("Invalid model response")
        generation_id = result.get("id")
        record_generation(factory, attempt_id, generation_id)
        if result.get("model") != quote.model:
            raise CloudError("Model response identity mismatch")
        usage = result.get("usage")
        if not isinstance(usage, dict) or "cost" not in usage:
            raise CloudError("Model response lacks final usage cost")
        settle_attempt(factory, attempt_id, generation_id, usage["cost"])
        # Application content and schema validation run after financial settlement.
        try:
            content = result["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise CloudError("Invalid model response") from None
        if not isinstance(content, str):
            raise CloudError("Invalid model content")
        return content


def inspect_pending(factory, settings, *, limit=100, client=None):
    """Inspect held generations; upstream does not expose exact endpoint tag for settlement."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise SpendingError("Inspection limit must be 1..100")
    key = settings.openrouter_api_key.get_secret_value()
    key_hash = hashlib.sha256(key.encode()).hexdigest()
    with factory() as db:
        pending = list(
            db.scalars(
                select(CloudAttempt)
                .outerjoin(CloudSettlement)
                .where(CloudSettlement.attempt_id.is_(None))
                .order_by(CloudAttempt.created_at)
                .limit(limit)
            )
        )
    own_client = client is None
    if own_client:
        client = httpx.Client(
            timeout=settings.ai_timeout_seconds, trust_env=False, follow_redirects=False
        )
    findings = []
    try:
        for attempt in pending:
            finding = {
                "attempt_id": attempt.id,
                "generation_id": attempt.generation_id,
                "model": attempt.model,
                "provider_tag": attempt.provider_tag,
                "reserved_usd": str(attempt.reserved),
            }
            findings.append(finding)
            if attempt.generation_id is None:
                finding["status"] = "no_generation_id"
                continue
            if attempt.key_sha256 != key_hash:
                finding["status"] = "different_key"
                continue
            try:
                data = _data(
                    _request(
                        client,
                        "GET",
                        "/generation",
                        key,
                        params={"id": attempt.generation_id},
                        max_bytes=RESPONSE_MAX,
                    )
                )
            except CloudError:
                finding["status"] = "unavailable"
                continue
            if (
                not isinstance(data, dict)
                or data.get("id") != attempt.generation_id
                or data.get("model") != attempt.model
                or data.get("provider_name") != attempt.provider_name
                or not isinstance(data.get("finish_reason"), str)
            ):
                finding["status"] = "identity_or_completion_unverified"
                continue
            if data.get("is_byok") is not False or "total_cost" not in data:
                finding["status"] = "cost_unverified"
                continue
            try:
                finding["observed_cost_usd"] = str(money(data["total_cost"], round_up=True))
            except SpendingError:
                finding["status"] = "cost_unverified"
                continue
            finding["status"] = "exact_endpoint_unverified"
    finally:
        if own_client:
            client.close()
    return findings


def main():
    from .config import get_settings
    from .database import session_factory

    parser = argparse.ArgumentParser(
        description="Inspect held OpenRouter generations without releasing budget"
    )
    parser.add_argument("inspect-held", choices=["inspect-held"])
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    settings = get_settings()
    if settings.ai_backend != "openrouter":
        parser.error("OpenRouter profile must be enabled")
    findings = inspect_pending(session_factory(settings.database_url), settings, limit=args.limit)
    print(simplejson.dumps(findings, indent=2))


if __name__ == "__main__":
    main()
