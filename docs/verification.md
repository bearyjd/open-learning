# Verification and remaining work

Verification performed on 2026-10-18 with normal network, container, and build
access. The earlier sandbox restrictions no longer describe the checks below.

## Completed checks

| Area | Evidence |
| --- | --- |
| Backend | 45 tests pass normally against real PostgreSQL/pgvector; Ruff lint and formatting pass for 17 Python files. |
| Extraction and HTTP | Real Trafilatura extraction and local TCP HTTP regression tests cover article titles, complete body reads, and rejection of incomplete declared bodies. Model HTTP boundary tests use controlled responses. |
| Database | Independent fresh PostgreSQL/pgvector migration upgrade, `alembic check`, and repeated upgrade pass. |
| Dependencies | `backend/uv.lock` locks 50 packages; local setup, Docker, and CI use frozen installs. |
| Android | Full Gradle `assembleDebug testDebugUnitTest lintDebug` succeeds with a full Temurin 21 JDK and SDK 36. Four unit tests pass; lint reports zero errors and 14 warnings. |
| APK | Debug APK package `org.openlearning.app`, minimum API 26, target API 36; APK v2 signing independently checked. |
| Compose | Full image builds, database migration, API, and worker start successfully. `/health` returns 200 and an unauthenticated personal API request returns 401. |

Backend tests cover authentication, the learning loop, source citations, saving,
failed-job retry, atomic publication, stale-worker fencing, duplicate ingestion,
saved-only search, and review scheduling/history. Fetch safety checks cover
non-public destinations and redirects, mixed DNS answers, pinned connections,
oversized responses, and timeouts. Android tests cover connection validation,
shared URLs, account cache isolation, and preservation of source coverage,
truncation, and model metadata in API JSON.

The model HTTP contract test verifies strict JSON-schema requests and rejection
of an oversized 2,001-character response after simplifying generation constraints.
Full Pydantic validation still applies to returned content.

The extraction and response-body fixes were checked with failing regression
tests before the fixes and passing tests afterward. Android lint warnings concern
dependency updates, backup metadata, kapt/ktx usage, and a missing application
icon. A full JDK is required: a JRE without `jlink` cannot perform this build.

Commands are in [README](../README.md). PostgreSQL tests require a disposable
database because their fixture creates and drops application tables. Local
Both jobs in [GitHub Actions run 39151607375](https://github.com/bearyjd/open-learning/actions/runs/39151607375)
passed at commit `081fd279`, before the final model adapter compatibility change;
the latest rerun is pending. Work is tracked in [draft PR #1](https://github.com/bearyjd/open-learning/pull/1).

## Acceptance still pending

- Live local-model acceptance is pending. Controlled inference tests establish
  API behavior, not the quality or reliability of a specific model. No model is
  bundled and generation never silently substitutes demo cards.
  A direct llama.cpp b9551 CPU probe with
  [Qwen2.5-1.5B-Instruct-GGUF](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF)
  produced a valid insight with exact source evidence; the full API flow remains
  pending.
- Emulator attempts with versions 35 and 37 failed to produce a usable device:
  KVM starts crashed and software boot remained offline. The APK has not been
  validated through a device UI, including offline reading and queued import
  recovery.
- Concurrent first requests for a daily feed can race while creating its unique
  database row. This remains follow-up work. API logs also contain an upstream
  Starlette deprecation warning.
- Restart/recovery and database restore still need acceptance checks.

The manual [live smoke script](../scripts/smoke_live.py) uses real API requests
to check health/authentication, ingestion, feed attribution, saving, two tutor
turns, and review scheduling. With the API, worker, and model running, use a
fresh source and database so the review is initially due:

```sh
# From backend/, with OL_API_TOKEN already set:
export OL_SERVER_URL='http://127.0.0.1:8000'
export OL_SMOKE_URL='https://example.org/a-fresh-public-article'
uv run --frozen python ../scripts/smoke_live.py
```

This modifies the connected library: it imports an article, saves an insight,
creates tutor turns, and rates a review. Keep tokens, private server addresses,
and personal reading URLs out of published logs. See
[local inference](local-ai.md) for container connectivity.

## Deliberately unfinished

RSS ingestion; plain-text/Markdown/PDF/EPUB imports; Kobo/Libby export adapters;
embeddings and semantic search; cross-document Explore/RAG; personalized
recommendations; collections/notes editing; progress charts; FSRS; and
media/transcript/audio features remain unimplemented. Explore is lexical search
over saved cards. WorkManager synchronizes article ingestion jobs; offline
reading is implemented through cached cards and manual refresh, with device
acceptance still pending.

Exact quotation validation establishes provenance, not logical entailment or
source truth. Live model quality, misconception detection, and opposing-viewpoint
generation have not been independently validated. This milestone does not
complete the entire requested platform or the device acceptance test.
