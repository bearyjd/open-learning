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
- Work for a different account is hidden and does not run; reconnecting the original
  account resumes it, and an existing server job is polled without resubmitting the
  URL. Switching accounts deletes the old cards and completed rows only; unsent note
  text and drafts are kept.
- Tutor messages and source citations render, recall answers stay hidden until
  revealed, and a remembered rating is sent before the due queue updates.

Tests reset only the acceptance package's data and do not change device network,
rotation, or fold settings. No personal reading data is used. See the
[device test commands](../android/README.md#device-acceptance-tests).
CI compiles both acceptance APKs; connected execution requires a device or emulator.

These checks validate the client against controlled API responses, separately
from the real backend/model workflow below. They do not establish model quality,
device-reboot recovery or physical-hinge behavior. Activity/ViewModel recreation
runs within the same app process. The app reported a 443 × 970 dp portrait window
during testing, covering compact bottom navigation; the expanded rail was later
observed under an emulated unfolded posture (see Fold/unfold acceptance).
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

### Fold/unfold acceptance (October 19, 2026) - partial

Pixel 10 Pro Fold (API 37), only `org.openlearning.app.acceptance`, same mock
server as above. The user authorized `cmd device_state` posture switching for
this task only; no reboot, `wm size/density`, or setting change. Baseline
recorded first: `wm size` 1080x2364, `wm density` 390, device state `0 CLOSED`,
empty `adb reverse --list`. States printed: 0 CLOSED, 1 HALF_OPENED, 2 OPENED,
3 REAR_DISPLAY_STATE, 4 CONCURRENT_INNER_DEFAULT, 5 REAR_DISPLAY_OUTER_DEFAULT.

```
adb shell cmd device_state print-states
python3 scripts/android_fold.py --serial SERIAL   # 0 -> 2 -> 1 -> 2 -> 0, reset in finally
```

`scripts/android_fold.py` reuses `android_process_death.py` (mock server,
connect flow, uiautomator helpers), records the baseline, always runs
`cmd device_state state reset` in `finally` (also on Ctrl-C/SIGTERM), and then
compares state, size, density, and reverse list with the baseline (equal after
every run). Evidence is uiautomator XML only; no screenshots were kept.

| Check | Expected | Actual |
|---|---|---|
| Folded window (CLOSED, outer) | bar, below 840 dp | 1080x2364 px, w443dp h970dp, bottom bar |
| Unfolded window (OPENED, inner) | rail, 840 dp or more | 2076x2152 px, w852dp h883dp, NavigationRail |
| Selected destination (Explore), fold -> unfold | kept | kept |
| Explore query "spaced", fold -> unfold | kept | kept |
| Activity recreation | recreated | not established: only a global `configuration_changed` event appeared, no `onCreate`/relaunch line (the first script version also counted the wrong event names) |
| Unsaved Connection text; tutor draft; unfold -> fold | text lost; draft kept | not verified (see gap) |
| Crash/ANR scan | none | not completed |

The unfolded width is 852 dp, only 12 dp above the 840 dp breakpoint
(`MainActivity.kt:73-87`): a small density or font-scale change would flip the
inner display back to the bottom bar.

Gap found: folding to CLOSED (including the final `reset` to the folded
baseline) sleeps the display and shows the lockscreen, and the script never
unlocks. Every leg after the first fold-closed (unfold -> fold, tutor draft,
crash scan) therefore could not be read, and the run stopped. The script now
runs all unlocked checks first (unsaved settings text and Explore query on
fold -> unfold, tutor draft across HALF_OPENED/OPENED) and records that the
closing fold locks the device; that full run still needs an unlocked device
and a corrected recreation signal. After the runs the posture was reset, the
baseline matched, the reverse mapping was removed, the mock server stopped,
and the package uninstalled.

Not covered: the unfold -> fold return leg on the outer display, device
reboot, backend restart or outage, a real backend or model, and
physical-hinge-only behavior. `cmd device_state` emulates posture; it does not
prove hinge sensors, inner-display app-continuity quirks, or what a user sees
when physically opening the device.

### Reboot recovery acceptance (October 19, 2026)

Pixel 10 Pro Fold (API 37), only `org.openlearning.app.acceptance`, host mock as above. The user authorized exactly one `adb reboot` for this task and unlocked the phone by hand; nothing else on the device was changed (no `wm size/density`, `device_state`, or settings; the script never unlocks).

```
python3 scripts/android_reboot.py --serial SERIAL
python3 scripts/android_reboot.py --serial SERIAL --resume-after-boot-id OLD --baseline JSON
```

Baseline: `wm size` 1080x2364, density 390, device state `0 CLOSED`, empty `adb reverse --list`, boot id `9daf442d-42e5-4a4e-9978-fbd2cfe8fc12`. The script built state through the UI (saved Library card, queued import held at "processing", Explore query "spaced"), ran `adb reboot`, and after boot the boot id was `21f7db1d-918a-4419-a010-5329af05e169` (changed). The `adb reverse` list was empty after boot, so the mapping had to be re-added.

What needed the user: on this device `adb` does not come back until the first unlock, so the first run (300 s boot wait) gave up and cleaned up while the phone sat at the boot screen. That run's mock was stopped, so a second `--resume-after-boot-id` run restarted the mock (same port, `saved` set true to match the fixture), waited for adb and the unlock (about 2 minutes), then ran the checks. Unlock was detected via `am get-started-user-state 0` = RUNNING_UNLOCKED plus keyguard not showing.

WorkManager before app launch: after unlock and before the script started the app, the mock logged one `GET /api/v1/jobs/remote-1` and the app process already existed (pid 14759). So WorkManager resumed the import by itself. Caveat: polls made while the first run's mock was down are not recorded, and the window only starts at the second run.

| Check | Expected | Actual |
|---|---|---|
| Stored credentials after reboot | no reconnect screen | Discover shown, no reconnect dialog |
| Queued import | listed, resumes | listed "Processing"; Completed after the mock completed it |
| Saved Library card | present | present (caveat: mock also reports it saved, so this does not isolate the Room DB) |
| Explore query "spaced" | lost (cold start) | lost, empty search |
| Crash/ANR | none | none: no AndroidRuntime or ANR lines; one boot-time `ActivityManager` line, "Unable to find ...acceptance.test/u10" (absent instrumentation package, not a crash); the script now ignores it, the final run reported it as a failure |

Cleanup: mock stopped, mapping removed, package uninstalled, dumps deleted, and size, density, device state, and reverse list equal the baseline.

Not covered: backend restart, a real backend or model, the fold return leg, physical-hinge behavior, and reboot with a mock that never stopped (the first mock ended with the failed run).

## Android note ingestion (E2a): what is and is not verified

Stale "Note queued" banner after the job completed: fixed (cleared when that note's job finishes or is removed, never a newer notice); JVM-tested (`NoteLogicTest`) for the `noticeAfterJobUpdate` logic only, not device-verified. The ViewModel wiring (the jobs collector in `LearningViewModel` that applies it) is covered only by the instrumented `NoteAcceptanceTest.queuedNoticeDisappearsAfterCompletion`, not by any JVM test, and that test has not been run on a device.

Device acceptance for notes has been run on one physical phone against a mock backend only (see the
device runs below). CI only compiles the instrumented tests.

Verified in CI (93 JVM tests, lint, debug/acceptance/androidTest builds, 21 script tests): note
validation (code-point bounds, CRLF, NFC, control, bidi, C1 and lone-surrogate rejection), file reading
(size limit before open, capped read, 60,000 code-point cap, UTF-8 and BOM handling, title from file name
cut on a code-point boundary), the replace-typed-text confirmation decision, attribution and link rules
(blank document ids never match), fixed error messages that never echo a response body, JSON request
shape, redacted `toString()` on every type holding note text, forget tombstones, the worker decision
function (completed/cancelled clear text, 0 rows never resurrects and deletes only a job the run created,
404/409 resubmit once from local text, 429 retry, 413/422 failed), Remove hidden on completed notes,
the bounded wait helper, checkpoint result handling, and removal of a forgotten document from screen
state. The data-extraction rules are parsed by a JVM test and the attribute is confirmed in the merged
debug manifest. The `scripts/tests/test_android_notes.py` checks cover the acceptance script's helpers
(including typing URLs and tokens) and confirm its mock logs only body length and SHA-256.

Written but not run: `NoteAcceptanceTest` (offline queue posts once, worker input keys,
text cleared after completion, 413/422/429 messages, no link nodes on notes, forget
cancel/confirm/offline, remove before submit, draft after `recreate()`), the Room
`MigrationTest` 1 to 2, `DatabaseHygieneTest` (secure_delete reads back 1, the WAL is 0 bytes after
`wal_checkpoint(TRUNCATE)`, completed-note and stale-card deletion, other-scope purge keeping unsent text and drafts, the mistyped-token A to B to A case, a stale purge doing nothing, shared server
job count, `@Update` not resurrecting a removed row) and `scripts/android_notes.py` (mock backend,
uiautomator XML only).

CI (`.github/workflows/ci.yml`) runs `assembleDebug testDebugUnitTest lintDebug` and only assembles the
androidTest APK, so every instrumented test (including `DatabaseHygieneTest`) is compile-only until run
on a device. JVM-tested: the purge predicate, the stale-purge decision, redacted `toString`, retry and
conflict plans. Instrumented only, not yet run: Room purge SQL and transactions, the WAL checkpoint,
`ViewModel` wiring (purge job cancel on reconnect, `removeNoteJob`, `forgetNote`). Since E2b-2 forgetting purges only the rows linked to
that document (fallback for pre-migration unlinked rows; see security).

Not verified: any device behavior, the real backend with the app, process death of the
editor on hardware, Compose text-field performance at 60,000 characters, an actual backup or
device transfer (only the configuration is checked), FLAG_SECURE/autofill/keyboard-learning effects
on a real keyboard, and whether the race between cancelling a submitting worker and the server
accepting the POST can still leave a server note (it cannot be closed client-side). Compose `recreate()` keeps the view model, so it does not prove draft
restore after process death; the script's kill check does, once run.

## Acceptance still pending

- General article/model-quality acceptance remains pending beyond the bounded
  live workflows above. Controlled inference tests establish API behavior;
  generation never silently substitutes demo cards.
- Device-reboot recovery is verified once on hardware (see above); a rerun with the mock
  running continuously across the reboot remains. Fold/unfold is partly covered
  (rail at 852 dp and state kept on fold -> unfold); the return fold leg,
  recreation evidence, and unsaved-text behavior still need an unlocked-device
  run. Process death of the tutor flow, including restore of the open item and
  unsent draft, was checked on a physical Pixel 10 Pro Fold (see below) against
  a mock backend only; the real backend and model were not exercised. Earlier
  host emulator attempts failed; the device results above use physical hardware.
- Backend tests emit an upstream Starlette TestClient deprecation warning.
- Backend service restart/recovery still needs live acceptance checks.

### Process-death check (acceptance build, mock backend)

Run with an adb-driven scratch script against a loopback mock of the `/api/v1`
contract (no real backend, database or model), using only
`org.openlearning.app.acceptance` and `am kill` while backgrounded; PIDs
confirmed to change in every case. The Bitwarden save-password prompt was
dismissed manually by the user.

| Scenario | Expected | Actual |
| --- | --- | --- |
| Tutor item + unsent draft, `am kill` | Item restored, 'Start explanation' shown, draft kept, chat history empty (by design) | As expected |
| Review reveal, `am kill` | Reveal lost (by design) | As expected |
| Explore query, `am kill` | Query kept | As expected |
| Idea-2 returns 404 while app dead | Falls back to list, selection cleared | As expected; stays on list afterwards |
| Idea-2 returns 503 while app dead | No crash, list this session | As expected |
| Next session after 503, mock ok | Tutor restored | As expected |
| Library save / queued import job after 4 kills | Kept | Library card kept; job polled again by WorkManager without relaunch (process restarted) |

Not verified: a 'Completed' badge for the import job after the backend
finished was not observed in the UI within the script's window (inconclusive).
Not covered: device reboot, fold/unfold return leg, backend restart, real
backend/model.

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

RSS ingestion; PDF/EPUB imports; Android notes list screen and note editing (E2b); Kobo/Libby export adapters;
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

Device attempt (Pixel 10 Pro Fold, acceptance build, mock backend, one supervised run): on the Connect screen the
server URL and access token were typed once and no Bitwarden or other autofill/save-password sheet appeared (the
script's uiautomator dump after the token step listed only the app's own window and no autofill text). The script then
reached the note editor and the 145-character draft survived `am kill` (counter 145 / 60,000), but the run stopped at
the next step: "Add note to learning feed" was not in the uiautomator tree (the button is below the visible editor, so
the script would need to scroll). Submit, no-link, forget cancel/confirm and 413 scenarios are therefore INCONCLUSIVE,
and the instrumented suites (including `ConnectAutofillTest`) were not run. One observation of a quiet token step is
evidence, not proof, for the autofill opt-out; the earlier aborted attempts (a save sheet appeared) were on builds without
the fix. The note-editor field itself was not exercised by autofill beyond the draft typing in this run.

Second supervised device attempt (same device and build, script fix 96ae34b scrolls within the app window, at most 3
swipes, and taps only fully on-screen nodes): again no Bitwarden or other autofill/save-password sheet appeared at the
token step or at any dump before the stop. The draft survived process death, and "Add note to learning feed" was now
reached and tapped once. The app showed "Note queued. It is sent to your server as soon as it is reachable." and a
"Note: Acceptance note / Completed" row under Recent imports, but the run stopped at the next step because no
"Your note: Acceptance note" card was in the uiautomator tree (the feed had not shown it within the script's wait). The
script crashed before printing results or saving the mock request log, so the POST count and hash, no-link, forget
cancel/confirm and 413 scenarios remain INCONCLUSIVE, and the instrumented suites were not run. Whether the missing card
is a script gap (a Refresh or tab change may be needed) or an app defect is undetermined. No reruns were made.

Third supervised device attempt (same device, script commits 4852649 and c4f8ef9): no Bitwarden or other
autofill/save-password sheet appeared at any dump (token step quiet again, so three consecutive builds with the fix
were quiet). PASS: connect; draft survived process death (counter 145); submit posted exactly once to
`/api/v1/ingest/text`; the "Your note: Acceptance note" card appeared with no Refresh tap (the earlier miss was a
script gap: the card was in the lazy feed below the visible window, so it was absent from the dump; the app does
refresh the feed after a completed note in `IngestWorker.runNote`, as it does after a completed URL import); no
source-link nodes on the list or the detail; forget Cancel sent 0 DELETEs and Confirm sent 1. The run then crashed
at "Write or import a note" (the feed list was still scrolled past it after the detail view; the script now scrolls
back, bounded), so the 413 scenario is INCONCLUSIVE, 429 is not scripted, and the instrumented suites were not run
because not every scenario completed. No reruns were made after the crash (device stopped per the zero-retry rule).

Fourth supervised device run (same device, APKs rebuilt up to date, script commits 7ff0e62 and 4eb875e): autofill
prompt YES/NO: NO. No Bitwarden, autofill or save-password sheet appeared at any dump; four consecutive builds with the
fix were quiet at the token step. The script ran every scenario to completion with no retries: connect PASS; draft
survived process death (counter 145) PASS; submit posted exactly once PASS; the logged body length (182) and SHA-256
matched the exact JSON the app should send, `{"text":...,"title":...}` compact with no label (Gson emits `text`
before `title`; key order is not contractual, so the script accepts either order and an empty-label variant, and the
first comparison, which assumed title-first, reported a mismatch of the same length until the order was corrected);
note card appeared with no Refresh tap PASS; no source-link nodes on list or detail PASS; forget Cancel 0 DELETEs and
Confirm 1 DELETE PASS; 413 shows the fixed "larger than your server accepts" message and the SENTINEL response body is
not in the UI PASS. Instrumented suites (acceptance package, one run each, no retries): `DatabaseHygieneTest` 9/9,
`MigrationTest` 1/1, `ConnectAutofillTest` 1/1 (connect dialog window excluded from autofill) PASS.
`DeviceAcceptanceTest` 5/6 (FAIL: `onlineSaveSurvivesOfflineRelaunchAndAccountSwitch`, Compose wait timed out after
15 s). `NoteAcceptanceTest` 2/8: PASS `draftSurvivesActivityRecreate` and `noteEditorAncestorIsExcludedFromAutofill`;
FAIL (Compose or DB wait timed out after 20 s) `removingAQueuedNoteBeforeSubmitSendsNothing`,
`serverErrorsMapToFixedMessagesWithoutEchoingTheBody` (NoteAcceptanceTest.kt:101 area, awaiting the job row),
`textIsClearedFromRoomAfterCompletion`, `offlineForgetKeepsEverything`,
`completedNoteShowsNoSourceLinksAndForgetCancelDoesNotDelete`. The failures were not diagnosed or retried and no app
code was changed; the same behaviours passed in the adb script against the same build, so a test-harness or timing
cause (instrumentation device state, WorkManager scheduling under test) is suspected but unconfirmed.
`LiveLearningLoopTest` was not run (needs a real backend and model).

Follow-up (static diagnosis, NOT re-run on a device): the 6 failures above are test bugs, not app bugs. In
`onlineSaveSurvivesOfflineRelaunchAndAccountSwitch` the tail asserted that alpha's saved card returns after a switch to
beta, but the scope-change purge deliberately deletes other scopes' cards (saved ones included) and the fixture was
already closed; it now asserts the purge (alpha cards gone, unsent draft kept) and reconnects alpha to a fresh fixture.
In `NoteAcceptanceTest` Compose `performClick` does not scroll, so the submit button (below the fold, IME open) and
other off-screen targets were never clicked; clicks now scroll first, submit waits for the button to be enabled, and
timeouts log the text-submit count and the semantics tree. These fixes are compile-only (assembleAcceptanceAndroidTest);
they have not been run on a device.

Fifth supervised device run (same device, APKs built from 485c3d9, one run of each, no retries; autofill prompt YES/NO:
NO, no Bitwarden, autofill or save-password sheet at any dump, five consecutive builds with the fix quiet at the token
step). `scripts/android_notes.py` completed every scenario: connect, draft after `am kill` (145), 1 POST with matching
hash, card without Refresh, no link nodes on list or detail, forget Cancel 0 / Confirm 1 DELETE, 413 fixed message with
no sentinel body in the UI, and the new 429 scenario PASS (fixed busy/retrying message, no sentinel in the UI, statuses
429 then 202, 2 POSTs with the same body hash, note completed, message cleared). Instrumented suites: `DeviceAcceptanceTest`
6/6 (the previously failing `onlineSaveSurvivesOfflineRelaunchAndAccountSwitch` now passes), `DatabaseHygieneTest` 9/9,
`MigrationTest` 1/1, `ConnectAutofillTest` 1/1. `NoteAcceptanceTest` 6/9 (9 tests, not 8): the
`removingAQueuedNoteBeforeSubmitSendsNothing`, `serverErrorsMapToFixedMessagesWithoutEchoingTheBody` and
`textIsClearedFromRoomAfterCompletion` failures from the fourth run are fixed. 3 still fail, all in `completeOneNote`
(NoteAcceptanceTest.kt:169): `completedNoteShowsNoSourceLinksAndForgetCancelDoesNotDelete`,
`forgetConfirmDeletesOnceAndPurgesRoom`, `offlineForgetKeepsEverything`. The timeout log (fixture text only) shows 1 text
POST, the job row completed ("Note: Lecture 3 / Completed") and the feed list showing "Today's ideas" with the note card
not in the semantics tree; the card sits in the lazy feed below the visible window, and the wait does a plain
`onAllNodesWithText` without scrolling. Classification: test bug (the wait must scroll to the card, as `scrollTo` does
afterwards), not an app bug, which the adb script corroborates (it scrolls and finds the card). Fixed afterwards,
compile-only and NOT re-run on a device: the `completeOneNote` wait now scrolls to the card (bounded by the 15 s
`waitUntil`, no sleeps). The three tests remain unverified on device until re-run.
Observation: the "Note queued..." banner was still showing after the job completed. Nothing clears it on completion:
`LearningViewModel.kt:204` sets `notice` and only `clearError()` (line 344, user dismiss) or another notice replaces it. `LiveLearningLoopTest`
was not run.

Still unverified on device: the SAF file picker through DocumentsUI, the remove-job race (cancel versus a submitting
worker), offline forget at a real network loss (the instrumented test fakes it with a fixture flag and has not passed),
the forget flows in `NoteAcceptanceTest` (blocked by the line-169 test bug; the script covers forget Cancel/Confirm),
fold/unfold and activity recreate with a note open, and whether Bitwarden's save sheet is suppressed (not observed in
five quiet runs, which is evidence rather than proof).

Connect-dialog and note-editor autofill opt-out (`fix/android-token-no-autofill`) is only partly device-verified (see above). The
earlier E2a claim that the note editor was excluded from autofill was wrong: it flagged the AndroidComposeView,
which ignores the flag. The corrected mechanism flags ancestors. JVM tests cover the target selection, restore and
fail-loudly logic against fakes (not real views); the instrumented `ConnectAutofillTest` and
`NoteAcceptanceTest.noteEditorAncestorIsExcludedFromAutofill` are compile-checked only. Whether Bitwarden's save
sheet is suppressed on a real device has not been observed.

## E2b-2 Android notes list (partly device-verified, see "E2b-2 device run" below)

Implemented: notes list screen ("Your notes" on Discover), targeted forget, server limits, share-as-note and the
30 s note backoff. JVM tests cover the list state reducer, row formatting, body selection, forget dialog copy, share
routing and deferral, limits and file cap math, and the purge predicate; they pass in CI. Instrumented
`NotesListAcceptanceTest` (list rows, forget Cancel sends 0 DELETEs, Confirm sends 1 and purges only that note's
import rows, a failed or offline delete keeps rows, FLAG_SECURE only while the list is shown, shared text opens
the editor pre-filled without submitting) is compile-checked only: not run on a device. The `scripts/android_notes.py` notes-list
scenario (open list, forget one of two notes, the other row remains; uiautomator dumps only) has pure-helper unit tests but
has not been run against a device. The script cannot check the Room import-row property or FLAG_SECURE; the
instrumented tests do. Not verified: pull of a real server's `/notes`, the WorkManager 30 s backoff on a device, share
intents from real apps, and the pre-migration fallback on a migrated database.

### E2b-2 review fixes (still NOT device-verified)

New JVM tests (all pass in CI; nothing here was run on a device): `ShareEntryTest` (fresh, recreated, from-history,
new-intent and consumed share decisions; deferral table incl. the late-draft cold start; repeat-share coalescing; dialog
wording), `ShareRoutingTest` additions (share cap and hard ceiling, code-point counting, second link), `NotesListStateTest`
additions (load before delete, out-of-order responses, stale failure, partial forget, redacted `toString`),
`NoteWorkLogicTest` purge branches (mixed state, title match, blank title, pure pre-migration), `NoteLogicTest`
forget outcome mapping, `NoteLimitsTest` clamping, `SecureRefCountTest`. Not covered by any test: the Activity wiring
(`onCreate`/`onNewIntent`/`consumeShare`), the `DisposableEffect` that clears the list, and `FLAG_SECURE` counting on a
real window; the instrumented tests were not extended or run.

### E2b-2 second review fixes (still NOT device-verified)

Fixed: the share cap now waits for the limits fetch (`limitsLoaded`, reset on scope change); the notes list generation
no longer resets on close; a failed local purge is retried in memory on connect and list open; the forget-from-detail
purge title is the document title (`sourceTitle`) instead of the idea headline; a mismatching title no longer deletes
all unlinked pre-migration rows; `draftReady` is set in a `finally`. New JVM tests (pass in CI, no device run):
`ShareEntryTest` (limits-late cold start with a 100k share, deferral on `limitsLoaded`, draft ready on throw, stale
load, ordering), `NotesListStateTest` (late answer after close and reopen, close keeps counting), `NoteLogicTest`
(pending purge retry per scope, purge title), `NoteWorkLogicTest` (no-match keeps other rows, single-row cleanup, NFC
titles). Not covered: the Activity and view model wiring of the retry and flags. A process death before a purge retry
still leaves the rows (see docs/security.md).

### E2b-2 device run (Pixel 10 Pro Fold, one run, no retries)

Supervised run on 2026-10-22 against the acceptance build. No Bitwarden, autofill or save-password sheet, system dialog or
foreign window appeared at any point (the script's foreground guard never fired; the foreground was the launcher before
and after).

`scripts/android_notes.py` (all scenarios, exit 0, uiautomator dumps only, dumps deleted): PASS for draft survives process
death (counter 145), submit posts exactly once with the expected hash and shows no source link, forget Cancel sends 0 DELETEs
and Confirm sends 1, notes list (rows shown, forget one of two sends 1 DELETE for it and 0 for the other, forgotten row gone,
other row remains), 429 then retry (2 POSTs, same body hash, message cleared), 413 fixed message, no sentinel text in the UI.

Instrumented run (`am instrument`, NotesListAcceptanceTest, NoteAcceptanceTest, DeviceAcceptanceTest, MigrationTest,
DatabaseHygieneTest, ConnectAutofillTest; LiveLearningLoopTest skipped): 36 tests, 5 failed (7 failure entries, two are
`@After` fallout), 31 passed. MigrationTest, DatabaseHygieneTest and ConnectAutofillTest passed in full. Failures:

- `NoteAcceptanceTest.queuedNoticeDisappearsAfterCompletion` (NoteAcceptanceTest.kt:90): test bug. `waitText("Note queued")`
  is an exact match, but the banner reads "Note queued. It is sent to your server as soon as it is reachable." (the semantics
  dump shows it present). Line 93 already uses `substring = true`.
- `NotesListAcceptanceTest.offlineDeleteKeepsRows` (NotesListAcceptanceTest.kt:106) and
  `NoteAcceptanceTest.offlineForgetKeepsEverything` (NoteAcceptanceTest.kt:152): both time out waiting for a "was not forgotten"
  node. Classified (static diagnosis, see below): test bug in the offline fixture.
- `NotesListAcceptanceTest.sharedTextOpensTheEditorPrefilledAndSubmitsNothing`, `DeviceAcceptanceTest.shareIntentPrefillsArticleWithoutSubmittingIt`
  and `persistedQueuedImportResumes...` (DeviceAcceptanceTest.kt:132 `scenario.close()`, then `cleanUp` :56): the activity
  "never becomes DESTROYED (last transition RESUMED)" on `ActivityScenario.close()`. Classified as a test teardown bug
  (hypothesis, see below); not an assertion about app behavior.

Test-side fixes after static diagnosis (compile-only, NOT re-run on a device; no app code changed):

- Banner exact match (test bug): `queuedNoticeDisappearsAfterCompletion` now waits with `substring = true`.
- Offline fixture and pooled connections (test bug): the fixtures' "down" mode returned `DISCONNECT_AT_START` from `dispatch()`
  only. MockWebServer applies that policy at accept time through `Dispatcher.peek()` (verified: `peek()` exists in
  mockwebserver 4.12.0; that the policy is applied only there is recalled, not verified), so a pooled keep-alive connection
  from the earlier GETs ignored it and the DELETE got an empty 200, which `isGone()` counts as success. Both fixtures now
  override `peek()` and answer `DISCONNECT_AFTER_REQUEST` while down; the DELETE-count-is-0 assertions stay (added to
  `offlineDeleteKeepsRows`). The 500 variant is unchanged.
- ActivityScenario teardown after intent replacement (HYPOTHESIS, not verified): `MainActivity` replaces its intent with
  `ACTION_MAIN` after consuming a share, which may stop `ActivityScenario` matching the activity it launched with
  `ACTION_SEND`. The three tests and the `@After` of the three classes now use `ScenarioTeardown.kt` (`closeAfterFinish`):
  finish the activity, bounded wait for DESTROYED, then close, logging instead of throwing.

Still unverified: all fixes above on a device, confirm-dialog FLAG_SECURE, fold/recreate with the list open, SAF
picker via DocumentsUI, offline forget (E2b-3).

## E2b-3 Android offline forget (NOT device-verified)

Nothing in this slice has run on a device. The forget queue (`PendingForget`, `ForgetQueue`, `ForgetWorker`) is new, and the
policy it implements reverses the earlier "nothing is cleared locally unless the server confirms" rule (see security.md).

JVM tests (run in CI): replay decision table (200/204/404 done, 401/403 failed with fixed errors, 429/5xx/no answer retry,
other 4xx failed), read-time filter predicate, scope isolation of hidden ids, which queued note jobs are cancelled, worker
scope rule, work name and backoff constants, list copy (waiting line, failed line, queue summary, banner), forget-outcome copy,
confirm-dialog copy. Python: `scripts/tests` cover the offline-forget helpers and the mock's offline mode.

Compile-checked only (androidTest, not run): `ForgetQueueRoomTest` (PendingForget DAO insert idempotence, hidden ids, delete,
scope isolation, purgeInactiveScopes keeps PendingForget, read-time hiding in feed, library, search, reviews and the notes list,
forgetting cancels a queued ingest of the same note, retry and resume scheduling) and `ForgetQueueAcceptanceTest` (offline
forget, restart, reconnect, exactly one DELETE; 404 counts as success; 401 gives a failed row with Retry and the note stays
hidden; 5xx keeps the row pending and retries; another account's pending row survives a reconnect). The two earlier offline-forget
tests were rewritten for the new behaviour. Their fixtures use the `peek()` / `DISCONNECT_AFTER_REQUEST` pattern.

Behaviours with NO JVM test, because they are bound to Room, WorkManager or Compose and the JVM has none of them:
the single Room transaction in `forgetNoteOffline` and `confirmed`; the DAO queries (insert-ignore, counts, status updates);
the combined feed/library filter in `LearningRepository.cached`; `visibleReviews` fallback to the server; worker credential
lookup and `Result.retry()` for a local cleanup failure; unique-work KEEP and the network constraint; `resumePending` on connect;
the Compose rows (waiting line, failed row, Retry); and the process-death survival claim. The instrumented tests above check
these only once someone runs them.

Device-acceptance script: `scripts/android_notes.py` has an offline-forget scenario (mock offline, forget the remaining listed
note, expect the "forgotten note waiting for the server" line and no DELETE, then mock online and expect exactly one DELETE
within 150 s and the line gone). Written, not run.

E2b-3 review fixes (still NOT device-verified). New JVM tests (`ForgetHardeningTest`): marker kept after confirm and cleared by a
fresh list, enqueue policy, id validation, per-row scheduling failure, linked-only confirmation purge, review resolution (cap,
short circuit, hidden), restore of a hidden note, marker ordering, post-commit best effort, token-rotation copy, Stop trying
state and copy, replay table (410, 408, 425, attempt cap), malformed payload, dialog residual copy. Untested: APPEND_OR_REPLACE
chaining in real WorkManager, a late network response in flight, the Room transaction and `Credentials.takePreviousServer`,
the Stop trying dialog, and the notice banner. Compile-only instrumented additions are in `ForgetQueueRoomTest`.

E2b-3 structural fix (durable tombstone, single card writer, derived view): STILL NOT device-verified. JVM tests added or changed:
`CardWriterTest` (`cardRows`: hidden, blank id, inFeed and saved flags), `ForgetPruneTest` (`confirmedDecisions` TTL edge, incomplete
page, still listed, request start versus delivery, other scopes; `serverPage`; sweep trigger), `HiddenViewTest` (`withoutHidden` for
every field, citations, identity), `ForgetStructuralTest` (enqueue policy table with REPLACE and KEEP, enqueue error
classification, `resolveReviews` with `documentId` and 404, review JSON from an old server, notice computation, and three
source scans). The source scans are text checks, not behaviour: only `CardWriter.kt` may call `dao.put(`; no `ForgottenNotes`
marker remains; `confirmed()` calls `confirmForget` and not `deleteForget`; `NotesActions` calls `queue.reconcile(`.
Compile-only (androidTest, not run): `ForgetGuardRoomTest` covers the seven interleavings (forget then stale write; write then
forget; confirm then stale write; simulated restart then stale write; reconcile inside the TTL and on an incomplete page;
confirmed and still listed after the TTL flips to pending; `forgetsOutside` excludes confirmed). `ForgetQueueRoomTest` was updated.
NOT tested at all: the `writeCards` transaction on a real database as it runs; `ForgetWorker` and `ForgetSweepWorker` in WorkManager
(REPLACE versus a running worker, the awaited enqueue, the periodic schedule, never returning a failed result); the derived
`state` flow in the view model (scope switch, combine timing); `NotesActions.load` wiring beyond the source scan; resume
wrapping in the view model; the once-per-start notice; `Credentials` deleting the old previous-server key; the Compose screens;
and the server's `document_id` on reviews (a backend change in a separate PR). The earlier `ForgetHardeningTest` marker tests
were removed with the marker.

E2b-3 fourth-pass fixes (still NOT device-verified). New JVM tests: `ReviewScrubTest` (blank id from cache, network or payload is
dropped and never mapped; a blank cached value falls through to the network; non-blank resolves), `PublishTest` (`published`: a
late search, review, notes, jobs, detail or chat publication for a hidden document is not stored; nothing returns after the
tombstone is pruned; another account's set is not applied), `JobWriteDecisionTest` (`jobWriteDecision`), and
`SingleWriterScanTest.onlyThePublisherWritesTheScreenState` (a TEXT scan: no direct write to a screen-state flow outside
`ScreenPublisher.kt`). Compile-only (androidTest, not run): `ForgetGuardRoomTest` covers the transactional `updateJobUnlessHidden`
(forget committing between the response and the update). Untested: `ScreenPublisher` itself (the snapshot re-check loop, the Room
flow collector, `publishFresh` DAO read), its wiring in the view model and `NotesActions`, the real transaction on a device, and the
residual of an unlinked processing row whose worker never answers.

E2b-3 fifth-pass fixes (best-effort against known interleavings; found by 5 adversarial passes; still NOT device-verified). New JVM
tests: `ScreenPublisherTest` (real publisher, fake hidden source: old-scope publication dropped, transform runs once, DAO-fresh read
beats a lagging snapshot, other-scope snapshot read from Room, chat cleared on a gained id, reply with a hidden citation dropped with
its question, hidden selection never chats), `SingleWriterScanTest` (TEXT scans: any `MutableStateFlow<ScreenState>` name, no `publish(`,
no content fields in `publishLocal`, guarded manual retry). Compile-only: `ForgetGuardRoomTest` holds the update transaction open with a
hook while a forget starts (it cannot prove Room's lock order). Untested: the view model wiring (`showDetail`, `chat`), Room flows.

E2b-3 sixth-pass fixes (best-effort; found by 6 adversarial passes; still NOT device-verified). Two-sided protocol: the forget commit
is followed at once by `ScreenPublisher.purgeNow` (via `ForgetQueue.onHidden`), and every `publishFresh*` store is followed by a DAO
re-read and an idempotent purge (never a transform re-run); the ordering argument is in `docs/security.md` and the
`ScreenPublisher.purgeNow` comment. New JVM tests (written first, run RED against stubs: 13 of 31 failed): `ScreenPublisherTest`
(read, commit, store; commit between store and re-read; store then commit; commit, read, store; stale snapshot and read; purge
idempotent and scope-checked; derived view includes the purge ids), `PurgeFloorTest`, `ChatSessionTest` (collector bump, new chat,
forget-completion bump: not stuck busy; a late final never clears a newer chat's busy flag), and `SingleWriterScanTest` (TEXT scans:
`ForgetQueue` calls `onHidden` after its transaction; the view model wires it to `purgeNow`; no `chatGeneration` outside `ChatSession`).
Compile-only (androidTest, not run): `ForgetQueueRoomTest.forgetCommitCallsTheHiddenHookAfterTheCommit`.
NOT tested: `ScreenPublisher` under real concurrency, the view model and `NotesActions` wiring at runtime, Room flows and their
delivery order, and the floor's two-arrival aging against real Room emissions.
