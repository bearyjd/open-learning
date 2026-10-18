# Verification and remaining work

Verification performed on 2026-10-18. This environment permits source edits and
cached tools, but blocks shell DNS/network access and has read-only Git metadata
and container runtime paths. These limits prevent a complete acceptance run.

## Completed checks

- **40 backend tests pass.** They cover the authenticated API learning loop, saving, source
  citations, failed-job retry, atomic publication, stale-worker fencing,
  duplicate ingestion, saved-only search, and all four review ratings/history.
- Security tests reject non-public destinations and redirects, mixed DNS
  answers, fabricated/empty evidence, oversized bodies, and slow DNS/headers.
  Connection tests check that validated IPs are used without another DNS lookup.
- Fresh SQLite Alembic upgrade and model/migration parity were independently
  checked. SQLite is a test dialect; PostgreSQL remains the production database.
- Three Android Kotlin/JUnit tests pass for server validation and explicit HTTP
  consent, shared URL parsing, and server/account cache isolation. They ran using
  cached compiler/JUnit dependencies rather than Gradle.
- Android XML and deployment/workflow YAML parse. Independent source review covered
  authentication, fetch safety, job completion, Android credential handling,
  cache isolation, and safe plain-text rendering.

The initial 33 backend tests passed with normal pytest. After final changes,
Starlette's test client hung because the sandbox denies asyncio UNIX-socket
wakeups (a minimal FastAPI test reproduced it). The final 40 passed in an
independently inspected temporary harness that caps selector sleep at 10 ms;
application code, test inputs, and assertions were unchanged. Ruff and formatting
checks pass for all 15 Python files. PostgreSQL migration SQL also compiles
offline, with extension creation before vector columns; this does not validate
the SQL against a running server.

Normal verification commands are in [README](../README.md); CI runs backend
tests against PostgreSQL and builds/tests/lints Android when published.

## Checks blocked here

- `./gradlew --no-daemon assembleDebug` fails before dependency resolution:
  `Could not create service of type FileLockContentionHandler` followed by
  `Could not determine a usable wildcard IP for this machine`. IPv4/no-daemon
  options did not fix it. No APK has been produced or installed. A separate
  cached Kotlin check found and fixed UI type errors, but cannot replace a real
  build because Retrofit artifacts are not cached.
- Trafilatura is unavailable in the cache. Backend pipeline tests explicitly
  inject extracted original sample text and controlled model responses. Actual
  HTML extraction and a live llama.cpp/Ollama response were not tested. No model
  is bundled, and generation never silently substitutes demo cards.
- Podman cannot access its runtime configuration: `chmod /run/user/1000/libpod:
  read-only file system`. Compose was inspected and parsed, but not launched.
  PostgreSQL/pgvector migrations, row locks, container networking, and the
  Android device learning loop still need live verification.
- `uv lock --offline` cannot resolve the complete dependency metadata. Direct
  Python versions are pinned, but no transitive lockfile is claimed. Docker and
  CI resolve the manifest from PyPI; they need network access. Generate/review
  `uv.lock` on a connected machine and use frozen installs before a release.
- Shell GitHub access fails DNS resolution; the connected GitHub app reads the
  repository but rejects branch creation with HTTP 403, `Resource not accessible
  by integration`. Remote push and PR creation could not be completed.

## Next acceptance run

1. On a connected Linux machine, resolve dependencies, run backend tests and
   lint, then start Compose with generated secrets and a reachable local model.
2. Verify `alembic upgrade head` and `alembic check` against PostgreSQL/pgvector.
3. Build/test/lint Android with Gradle, install the debug APK on a device or
   emulator, enter the backend URL/token, and submit a permitted public article.
4. Confirm job completion, inspect source quotes, save a card, explain it to the
   tutor, reveal a recall answer, rate it, and verify the future due date.
5. Disconnect the network and confirm saved cards remain readable. Reconnect,
   restart API/worker, and confirm queued work recovers. Test a database restore.

## Deliberately unfinished

RSS ingestion; plain-text/Markdown/PDF/EPUB document picker/imports; Kobo/Libby
export adapters; embeddings and semantic search; natural-language cross-document
Explore/RAG; topic-diverse recommendation ranking; collections/notes editing;
progress charts; FSRS; media/transcript/audio features. The initial Explore
screen is lexical search over saved cards. WorkManager currently synchronizes
article ingestion jobs; offline reading uses cached cards and manual refresh.

Exact quotation validation establishes provenance, not logical entailment or
source truth. Live model quality, misconception detection, and opposing-viewpoint
generation have not been independently validated. This milestone must not be
presented as completing the entire requested platform or the device acceptance
test.
