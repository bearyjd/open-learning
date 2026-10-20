# Open Learning

A private, self-hosted learning companion with a native Android client. Original
source is licensed under **GPL-3.0-only**. The first milestone focuses on learning
from articles you deliberately import, with source passages kept alongside AI
interpretations.

## Current scope

The implementation targets this loop:

1. Submit a public article URL and follow its processing job.
2. Extract readable text and request structured insight cards from your model.
3. Read attributed cards in Android and save them for offline reading.
4. Explain an idea to the source-grounded tutor and receive a follow-up question.
5. Rate your recall and schedule another review.

This is an initial implementation, not a completed personal learning platform.
See [verification](docs/verification.md) for actual checks and limitations, and
[architecture](docs/architecture.md) for the boundaries of this milestone.
There are no bundled personal documents, demo cards, model weights, analytics,
Google Play Services, or hosted accounts.

## Run locally

Requirements: Python 3.12+, uv, and Podman with a Compose provider (or Docker
Compose). The inference server runs separately. Start in the repository root:

```sh
cp .env.example .env
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
python3 -c 'import secrets; print(secrets.token_hex(32))'
```

Put the first generated value in `OL_API_TOKEN`, the second in
`POSTGRES_PASSWORD`, and configure `OL_AI_BASE_URL` and `OL_AI_MODEL` in `.env`.
Use URL-safe passwords because the database connection URL includes the password.
Then run:

```sh
podman compose --env-file .env -f infrastructure/compose.yaml up --build -d
curl --fail http://127.0.0.1:8000/health
```

Compose starts PostgreSQL with pgvector, applies migrations, then starts the API
and worker. The database has no published port. Only the health endpoint is
public; the personal API requires your bearer token. No insights appear until an
article has been successfully processed.

The host's inference server must be reachable from the worker container.
`host.containers.internal` is supplied through the Compose host-gateway mapping;
verify its behavior with your Podman Compose provider. A server bound only to the
host's loopback address may need a private bridge address instead. Keep inference
ports firewalled. See [local AI configuration](docs/local-ai.md).

## Optional OpenRouter backend

The backend defaults to local inference. To use OpenRouter, set the commented
`OL_AI_BACKEND`, `OL_CLOUD_OPT_IN`, and `OL_OPENROUTER_*` values in `.env` before
starting Compose. Set an exact model ID and endpoint **tag** from OpenRouter's
[model endpoint data](https://openrouter.ai/docs/api/api-reference/endpoints/list-endpoints),
not a provider display name. Create a dedicated inference key in the OpenRouter
dashboard with a nonresetting spending limit equal to
`OL_OPENROUTER_KEY_LIMIT_USD`. Do not use a management or provisioning key. Keep
these values on the backend; Android uses only the private API token.
When enabled, article passages and tutor messages/history are sent to OpenRouter
and the selected ZDR endpoint for inference.

Configure positive Decimal USD request, lifetime, and key limits, with request
and key limits no greater than the lifetime limit. Set the nonnegative
`OL_OPENROUTER_REQUEST_FEE_CAP_USD` explicitly. The configured key limit must
match the provider's current key metadata; an effective account or workspace
budget with less remaining allowance also blocks a request. There is no funded
default. The lifetime limit is recorded in PostgreSQL on first admission and
cannot be changed by editing `.env`; a mismatch fails closed. Each cloud call
reserves its full context and bounded output at the freshly checked endpoint
rates plus the full request fee cap, commits that hold before sending one POST,
and settles only when the response provides a validated final `usage.cost`.
Application validation can reject a paid response after settlement. Failed or
uncertain calls retain their full holds, and an actual cost above the reservation
records persistent debt that blocks later dispatch. Job retry creates another
reservation. No prompt, response text, key, or source URL enters the ledger.

Run `uv run --frozen python -m open_learning.openrouter inspect-held --limit 100`
from `backend` with the same backend environment to inspect unresolved holds.
The command makes bounded, read-only generation lookups and never releases a
hold. The documented [generation API](https://openrouter.ai/docs/api/api-reference/generations/get-generation)
returns a provider display name, not the exact endpoint tag, so it cannot prove
the route required for safe automatic reconciliation. In particular, sampled
`azure` and `azure/swedencentral` endpoints share the display name `Azure`.
Attempts without a generation ID also stay held. Do not reset or delete the
ledger to work around a hold; investigate the provider activity and obtain exact
endpoint evidence before adding a future reconciliation mechanism. No live
inference is used by the test suite.

To run Python processes directly, provision PostgreSQL first, then:

```sh
cd backend
uv sync --frozen --python 3.12
export OL_DATABASE_URL='postgresql+psycopg2://openlearning:YOUR_PASSWORD@127.0.0.1:5432/openlearning'
export OL_API_TOKEN='YOUR_GENERATED_API_TOKEN'
export OL_AI_BASE_URL='http://127.0.0.1:8080/v1'
export OL_AI_MODEL='local-model'
uv run --frozen alembic upgrade head
uv run --frozen uvicorn open_learning.main:app --host 127.0.0.1 --port 8000 --no-access-log
# In a second terminal with the same environment:
uv run --frozen python -m open_learning.worker
```

The Compose database deliberately has no host port. For direct Python development,
use a separate local PostgreSQL instance or a private development port mapping.

## Android

Install a full JDK 17+ (the build was verified with Temurin 21) and Android SDK
platform 36/build-tools 36.0.0. A JRE alone lacks required build tools. Set `ANDROID_HOME`
to your SDK directory (or use Android Studio's SDK configuration), then:

```sh
cd android
./gradlew --no-daemon assembleDebug testDebugUnitTest lintDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

Open the app, enter the backend address and API token, and connect. For a USB
device connected to a backend running on your development machine:

```sh
adb reverse tcp:8000 tcp:8000
```

Use `http://127.0.0.1:8000/` on that device. Android emulator users can use
`http://10.0.2.2:8000/`. For LAN/Tailscale access, explicitly configure the
private server bind address and firewall; prefer HTTPS with a trusted certificate.
The app allows HTTP only after explicit consent in its connection settings. HTTP exposes the token and
reading data to the network, so do not use it on untrusted networks. The app
requires no Google account and uses native Android APIs compatible with GrapheneOS.

The Gradle wrapper and direct dependency versions are pinned for repeatable debug builds.
Debug signing keys and ZIP timestamps differ between machines; this is not a
claim of byte-for-byte reproducibility. CI builds and retains a debug APK.

## API and tests

All `/api/v1` endpoints require `Authorization: Bearer YOUR_TOKEN`. FastAPI
validates request and response models. The app uses the same real endpoints.
To export the OpenAPI contract from `backend`, run:

```sh
uv run --frozen python -c 'import json; from open_learning.main import app; print(json.dumps(app.openapi(), indent=2))' > openapi.json
```

Interactive public API documentation is disabled. Example submission:

```sh
curl --fail http://127.0.0.1:8000/api/v1/ingest/url \
  -H "Authorization: Bearer $OL_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://example.org/an-article-you-can-legally-process"}'
```

Plain text or Markdown you wrote or exported can be ingested the same way. The
text is not rendered; it must be 100 to 60,000 characters (`OL_MAX_DOCUMENT_CHARS`)
and control characters are rejected (see [security](docs/security.md)):

```sh
curl --fail http://127.0.0.1:8000/api/v1/ingest/text \
  -H "Authorization: Bearer $OL_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"title":"My study notes","label":"Biology 101","text":"...at least 100 characters..."}'
```

Cards from notes report `source_kind: "note"`, an empty `source_url`, and
`source_label`; URL cards report `source_kind: "url"`. This is backend-only for
now; the Android app has no note import or attribution display yet.

Notes can be listed with `GET /api/v1/notes` (`limit` 1 to 500, default 100, and
`offset`) and permanently forgotten with `DELETE /api/v1/notes/{document_id}`, which
also cancels queued, running and failed jobs holding the same text. Backups, the
Android cache and model-provider copies are not purged; see
[security](docs/security.md). `GET /api/v1/jobs?kind=note` lists note jobs (id,
status, created_at only) so a failed job can be removed with `DELETE /api/v1/jobs/{id}`.
`OL_NOTE_MAX_PENDING_JOBS` (default 20) limits queued plus running note jobs (429
beyond it; SQLite can overshoot slightly), and failed jobs keep note text for at
least `OL_NOTE_FAILED_PAYLOAD_HOURS` (default 168) only while the worker or note
ingest runs to purge them, and at most `OL_NOTE_FAILED_PAYLOADS` (default 20) jobs.

Poll `GET /api/v1/jobs/{id}`. A failed job retains its status; inspect the safe
error code, correct the source or model configuration, and retry it using
`POST /api/v1/jobs/{id}/retry`. The worker must be running.

```sh
cd backend
uv run --frozen pytest -q
uv run --frozen ruff check .
```

Tests use original, legally distributable sample text and controlled inference
responses. They do not prove a specific live model will generate accurate cards.
Real extraction and HTTP boundary regression tests also run in the backend suite.
For manual acceptance against your running API, worker, and model, see the
[live smoke procedure](docs/verification.md#acceptance-still-pending); it modifies
the connected library.
See the verification report for dependency and runtime checks completed here.
Python dependencies are locked in `backend/uv.lock`; local setup, Docker, and CI
use frozen installs. PostgreSQL integration tests use `OL_TEST_DATABASE_URL`,
which must point to a **disposable test database**: the test fixture creates and
drops application tables. Never use your personal library database for tests.
`OL_TEST_MIGRATION_DATABASE_URL` enables the PostgreSQL migration tests; it must be
a second disposable database (the tests drop the `public` schema) that differs from
`OL_TEST_DATABASE_URL`.

Before downgrading below migration 0003, stop the API and worker. The downgrade
discards text and labels held by failed note jobs permanently (retry then returns
409), and `note:<sha256>` source keys become visible as `source_url` to older code.

## Roadmap and operation

RSS, file imports (EPUB/PDF/text/Markdown), Kobo and Libby exports, semantic
retrieval, personalized recommendations, and cross-document Explore answers are
follow-on work. The initial Explore screen searches saved insights. Schema
extension points do not imply these features are implemented. There is no DRM
bypass or private library API integration. Article text is treated as an excerpt;
partial evidence is never presented as a whole-book summary.

Read [security and backups](docs/security.md) before storing private material.
Dependency license notices are listed in [third-party licenses](docs/third-party-licenses.md).
