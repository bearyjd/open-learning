# Verification and remaining work

Verification performed on 2026-10-18 with normal network, container, and build
access. The earlier sandbox restrictions no longer describe the checks below.

## Completed checks

| Area | Evidence |
| --- | --- |
| Backend | 117 tests pass against real PostgreSQL/pgvector: 68 existing regressions and 49 evaluation tests. Ruff lint and formatting pass. |
| Extraction and HTTP | Real Trafilatura extraction and local TCP HTTP regression tests cover article titles, complete body reads, and rejection of incomplete declared bodies. Model HTTP boundary tests use controlled responses. |
| Database | Independent fresh PostgreSQL/pgvector migration upgrade, `alembic check`, and repeated upgrade pass. |
| Dependencies | `backend/uv.lock` locks 50 packages; local setup, Docker, and CI use frozen installs. |
| Android | Full Gradle `assembleDebug testDebugUnitTest lintDebug` succeeds with a full Temurin 21 JDK and SDK 36. Four unit tests pass; lint reports zero errors and 15 warnings. |
| Device acceptance | Six instrumentation tests pass on a physical Pixel 10 Pro Fold running API 37, using the isolated `org.openlearning.app.acceptance` package and a device-loopback HTTP fixture server. Scope is described below. |
| Live Android acceptance | One opt-in instrumentation test passes on the same physical phone against actual API/worker processes, fresh PostgreSQL, and local GPU inference. All learning mutations originate in the UI; independent database checks confirm retained three-day scheduling. |
| Live runner safeguards | Ten standard-library tests pass, including isolation, private credential transport, redirect refusal, failed/skipped instrumentation, and owned-resource cleanup. CI runs these without a device or model. |
| Model quality evaluation | Eight original synthetic cases ran once against local Qwen2.5-7B Q4. All outputs pass contracts; only three meet the predeclared rubric under independent AI-assisted review, with two critical failures. This is a failed model-quality baseline, not a qualified model. See [results](model-evaluation.md). |
| APK | Debug APK package `org.openlearning.app`, minimum API 26, target API 36; APK v2 signing independently checked. |
| Compose | Full image builds, database migration, API, and worker start successfully. `/health` returns 200 and an unauthenticated personal API request returns 401. |
| Live workflow | All seven smoke stages pass on a longer Python tutorial using real API/worker processes, PostgreSQL, and local Vulkan inference on an 8 GB GPU. An earlier short-page workflow also passed with Compose and CPU inference. Scope and limitations are below. |
| Backup restore | `pg_dump` of the public-sample database restores into a fresh isolated PostgreSQL database; one insight and one review-history row were verified before removing that isolated database. |

Backend tests cover authentication, the learning loop, source citations, saving,
failed-job retry, atomic publication, stale-worker fencing, duplicate ingestion,
saved-only search, and review scheduling/history. Fetch safety checks cover
non-public destinations and redirects, mixed DNS answers, pinned connections,
oversized responses, and timeouts. Android tests cover connection validation,
shared URLs, account cache isolation, and preservation of source coverage,
truncation, and model metadata in API JSON.

The model HTTP contract test verifies strict JSON-schema requests and rejection
of an oversized 2,001-character response after simplifying generation constraints.
Full Pydantic validation still applies to returned content. The current insight
and tutor wire schemas select an ID before nested generated content; published
quotations are resolved from supplied source spans.

Fixed-response HTTP tests exercise the real model adapter and worker. A valid
later-passage span ID is published with canonical source text and the correct
original chunk. Mixed batches containing an unknown or non-string ID, forbidden
legacy quote/index fields, or excessive tags fail atomically. Existing source,
document, chunk, insight, review, and feed records remain unchanged; failed jobs
remain retryable. Tutor transport tests cover canonical citation materialization
and rejection of unknown IDs, legacy quotations, and invalid content without
writing chat, interaction, or review state.

The source-ID wire contract was checked failing before implementation and passing
afterward. Span tests cover bounds, duplicate text with distinct original chunks,
immutable request-local mappings, short tails, and whitespace-heavy input. Review
caught a sparse-prefix loss; its regression failed before the correction. Source
text eligible for citation is preserved in bounded contiguous spans or processing
fails explicitly; an evidence span needs at least 20 normalized characters.

The feed concurrency regression failed with a duplicate-key error before the
fix. It coordinates two authenticated API requests at the real PostgreSQL insert
boundary; both now return the same persisted feed with at most ten unique usable
cards. It also verifies dismissal and refill with new content. This boundary
does not exercise Android or model behavior. On SQLite, the suite passes 67 tests
and skips this PostgreSQL-specific concurrency test.

The extraction and response-body fixes were checked with failing regression
tests before the fixes and passing tests afterward. Android lint warnings concern
dependency updates, backup metadata, kapt/ktx usage, and a missing application
icon. A full JDK is required: a JRE without `jlink` cannot perform this build.

Commands are in [README](../README.md). PostgreSQL tests require a disposable
database because their fixture creates and drops application tables.
Both jobs in [GitHub Actions run 39151607375](https://github.com/bearyjd/open-learning/actions/runs/39151607375)
passed at commit `081fd279`, before the final model adapter compatibility change;
see [draft PR #1 checks](https://github.com/bearyjd/open-learning/pull/1/checks)
for the current commit.

## Android device results

The acceptance suite exercises production Compose UI, ViewModels, Retrofit,
Room, Android Keystore, and scheduled WorkManager jobs. Only the remote HTTP
service is replaced with controlled responses. Its six cases cover:

- Android sharing prefills an editable article URL without submitting it.
- Saving online, making the fixture server unreachable, and reopening the
  Activity preserves saved Library cards and source details. A fresh credentials
  reader decrypts the saved connection; switching accounts hides the old library.
- An import submitted through sharing and Add resumes its existing server job
  after Activity/ViewModel recreation and a real WorkManager retry, without a
  duplicate URL submission.
- A late feed response from the previous account stays in that account's cache
  and never appears in the new account's UI.
- Work for a different account pauses; reconnecting the original account and
  retrying polls the existing job without resubmitting the URL.
- Tutor messages and source citations render, recall answers stay hidden until
  revealed, and a remembered rating is sent before the due queue updates.

Tests reset only the acceptance package's data and do not change device network,
rotation, or fold settings. No personal reading data is used. See the
[device test commands](../android/README.md#device-acceptance-tests).
CI compiles both acceptance APKs; connected execution requires a device or emulator.

These checks validate the client against controlled API responses, separately
from the real backend/model workflow below. They do not establish model quality,
process-death or device-reboot recovery, or transitions between folded and
unfolded layouts. Activity/ViewModel recreation runs within the same app process.
The app reported a 443 × 970 dp portrait window during testing, covering compact
bottom navigation; expanded navigation rail behavior remains unverified.
The initial API 37 run failed inside older Espresso initialization before app
assertions; test-only AndroidX Test/Espresso updates resolved it.

### Android UI against the live stack

The opt-in `LiveLearningLoopTest` passed in 51.529 seconds on October 18, 2026,
on the physical Pixel 10 Pro Fold (API 37). It used the isolated acceptance
package, actual host API and worker, a fresh migrated PostgreSQL/pgvector
database, and the local llama.cpp/Qwen configuration documented below. The
source was the public Python tutorial at
`https://docs.python.org/3/tutorial/introduction.html`. No HTTP fixture or model
response substitution participated in this test.

The UI submitted the URL, waited for real WorkManager completion, displayed the
generated card's source/evidence, saved it, started tutoring, submitted a learner
reply, revealed the recall explanation, and sent a Remembered rating. The test
checked two persisted assistant responses and the learner message, a three-day
future-date notice, and the due queue. Closing and reopening the Activity and
ViewModel preserved the saved Library item and empty due queue.

An independent verifier used a read-only database transaction to confirm one
source, one document, seven chunks, one insight, one saved item, three tutor
messages, one retained review, one review-history row, and one completed job
with one attempt. Tutor roles were assistant, user, assistant. The rating and
review both retained `interval_days=3`; the due time was October 21, 2026 at
22:55:02 UTC, three days after the October 18 rating. Canonical insight evidence
matched its linked source chunk. Persisted model provenance and the running
loopback llama-server matched `qwen2.5-7b-instruct-q4_k_m`.

The runner's private configuration file and owned adb reverse mapping were
removed. The existing six fixture tests also passed separately in 22.020 seconds,
and debug assembly, JVM test task, and lint completed successfully. The new live
test is skipped by default; CI compiles it but needs an authorized device and
isolated live stack to execute it. See the
[explicit live acceptance commands](../android/README.md#opt-in-android-ui-against-the-live-backend).

This establishes integration for one public article and model configuration.
It does not assess general factual accuracy or Socratic teaching quality. The
learner reply restates the generated central idea; it is not an independent
comprehension evaluation. Relaunch remains within the same process. Process
death, reboot, expanded/fold transitions, offline tutor/review writes, and live
service-restart recovery remain outside this check.

## Live model results

With the current source-ID contract, all seven live smoke stages pass against
`https://docs.python.org/3/tutorial/introduction.html`: health/authentication, URL
ingestion, feed/source attribution, saving/library, tutor start, the learner
explanation/follow-up, and a remembered review scheduled three days ahead. The
run used a fresh isolated PostgreSQL/pgvector database with migrated schema,
actual host API/worker processes, and llama.cpp b9551 Vulkan with official
Qwen2.5-7B-Instruct Q4_K_M weights on an RTX 4070 Laptop GPU (8 GB). No inference
responses were substituted. [Pinned setup and checksums](local-vulkan.md).

Context was 8,192 tokens with one inference slot and all model layers offloaded
to the NVIDIA device. Total device memory sampled every 500 ms during the
acceptance work reached a maximum observed 4,741 MiB, including ordinary display usage; this is
not an isolated model-memory benchmark or a guarantee for other workloads.

The source-ID prototype produced one manually reviewed acceptable narrow card.
A deliberately false learner claim about division types was corrected in one
three-turn tutor prototype. These observations do not validate general model
accuracy: feedback could be repetitive, correct answers were not always
explicitly assessed, and one follow-up question was ambiguous. Independent review
also accepted the final API card against its selected span. Tutor responses were
consistent with the cited integer examples, but described floor division as
returning an integer without qualifying operand types. With floating operands,
`17.0 // 3` returns the float `5.0`; this precision limitation remains. See
[Python numeric types](https://docs.python.org/3/library/stdtypes.html#numeric-types-int-float-complex).
Broad article
quality, entailment, and misconception-detection reliability remain unverified.

### Earlier direct-quote results

The full API workflow passed against `https://example.com/`, a short public
page, using llama.cpp b9551 on CPU with
[Qwen2.5-1.5B-Instruct-GGUF](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF).
All seven smoke stages passed: health/authentication, ingestion, feed/source
attribution, save/library, tutor start, the learner's explanation and follow-up,
and rating a remembered review with a future due date. This model/server test
used `--no-jinja --chat-template chatml`; those flags are model-specific.

The longer Python introduction at
`https://docs.python.org/3/tutorial/introduction.html` fetched and extracted
successfully, but the same small model returned a quote absent from the supplied
passages. The job failed with `Model returned evidence absent from the source`
and published no insights. Larger-article and general model-quality acceptance
did not pass under the former direct-quote contract. Evidence validation rejected
the unsupported output as intended. No model weights are bundled.

A fresh diagnostic used seven passages (16,282 passage characters). The 1.5B
model returned three paraphrases as evidence, all absent from every passage,
and exceeded the six-tag limit on two cards. Stronger quotation instructions and
explicit passage indices produced tag counts within the limit in that trial but
still produced invented evidence. An unchanged single-passage request also
returned invented evidence and an invalid passage index. All responses finished
normally; these were not
truncated JSON responses. Neither experimental prompting nor a smaller passage
budget was shipped as a fix. The 7B model also edited quotations and selected
incorrect passage numbers with that contract. Those invalid direct-quote
responses were rejected. The current explicit span-selection contract was then
verified test-first; it removes copying as a model task rather than repairing
its generated quotations.

### Android process-death acceptance (October 19, 2026)

Run on the physical Pixel 10 Pro Fold (API 37, folded outer display, 1080x2364
at 390 dpi) with only `org.openlearning.app.acceptance`. The six fixture tests
passed again through `connectedAcceptanceAndroidTest` (7 reported, 1 skipped:
the opt-in live test). Process death was then driven by
`scripts/android_process_death.py --serial SERIAL`, which starts a throwaway
loopback mock of the `/api/v1` contract on the host (no database, model, or
cloud; the on-device fixture lives in the instrumentation process and dies with
it), adds and removes its own `adb reverse`, configures the app through its
Connection dialog, queues an import that stays "processing", presses Home, kills
with `am kill` (never `force-stop`, which cancels WorkManager), and relaunches
with `am start`. Each kill was confirmed by a changed `pidof`; evidence is
uiautomator dumps and screenshots under the ignored `.omc/process-death/`.

| State before kill | After kill and relaunch | Expected |
|---|---|---|
| Review tab, explanation revealed | Review tab; reveal lost | yes |
| Explore tab, typed query "spaced" | Explore tab; query kept | yes |
| Learn tab, tutor open, unsent text | Learn list; tutor and unsent text lost | tutor yes, text no |
| Saved Library card, queued import (Room) | Both present | yes |

The unsent tutor text was predicted to survive but does not: it is
`rememberSaveable` inside `Tutor` (`MainActivity.kt:226`), which is composed only
while the in-memory `ScreenState.selected` is set, so nothing restores it. The
selected item, chat history, and revealed state (`LearningViewModel.kt:153-160`)
are in memory only; the user lands on the destination list. This is a product
gap, not a test failure; no app code was changed.

WorkManager resumption: with the job held at "processing", polls arrived at
exponential-backoff intervals (10, 20, 40, 80, 160 s) across three kills.
After a fourth kill with no relaunch, WorkManager restarted the process on its
own (new pid) about 60 s later and polled the job. Once the mock reported the
job complete, Recent imports showed Completed.

Not covered: fold/unfold, device reboot, backend restart or outage during
recovery, expanded layouts, a real backend or model (mock responses only), and
the non-acceptance app. Mock responses establish client behavior only.
Standard `adb` calls were all that were used on the device; settings, size,
density, and device state were unchanged, and the reverse list matched baseline.

## Acceptance still pending

- General article/model-quality acceptance remains pending beyond the bounded
  live workflows above. Controlled inference tests establish API behavior;
  generation never silently substitutes demo cards.
- Device-reboot recovery and folded/unfolded layout transitions remain
  unverified; process death is covered above, with the in-memory tutor gap noted. Earlier host emulator attempts failed; the device results
  above use physical hardware.
- Backend tests emit an upstream Starlette TestClient deprecation warning.
- Backend service restart/recovery still needs live acceptance checks.

The manual [live smoke script](../scripts/smoke_live.py) uses real API requests
to check health/authentication, ingestion, feed attribution, saving, two tutor
turns, and review scheduling. With the API, worker, and model running, use a
fresh source and database so the review is initially due:

```sh
# From backend/, with OL_API_TOKEN already set:
export OL_SERVER_URL='http://127.0.0.1:8000'
export OL_SMOKE_URL='https://example.com/'
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
reading uses cached cards and manual refresh, verified with the device fixture
checks above.

Validated source-span references and their canonical quotations establish provenance,
not logical entailment or source truth. Valid IDs can accompany unsupported
explanations; ID selection removes fabricated quote copying as a rejection signal.
General live model quality, misconception detection, and opposing-viewpoint
generation have not been independently validated. This milestone does not
complete the entire requested platform or an Android-to-live-model acceptance run.
