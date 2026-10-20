# Open Learning roadmap

## 1. Current PR and verified baseline

As of October 18, 2026, [PR #1: Add native Android learning foundation and private
article loop](https://github.com/bearyjd/open-learning/pull/1) is merged into
`main` at `6ed9d6c`. Its backend and Android CI jobs passed. This is the bootstrap
baseline for subsequent focused PRs.

The baseline includes native Android screens, article ingestion, local inference,
source-linked cards, saving, tutoring, review scheduling, and lexical search.
Evidence includes 68 PostgreSQL tests, six physical-device tests against controlled
HTTP responses, four Android JVM tests, and a seven-stage real backend/model
workflow with Qwen 2.5 7B on an 8 GB GPU. See the pinned
[verification record](https://github.com/bearyjd/open-learning/blob/ec5b5ca60296964b57cb1ca5a5b40df24e966a9d/docs/verification.md).

[PR #3](https://github.com/bearyjd/open-learning/pull/3) adds the complete
Android-to-live-model acceptance test. The live test passed on a physical phone,
with independent database verification, and its backend and Android CI jobs
passed. PR #3 is merged into `main`. See its pinned
[verification record](https://github.com/bearyjd/open-learning/blob/e6ed60683b5806124f97dbaf6505700290cebf63/docs/verification.md#android-ui-against-the-live-stack).

General factual accuracy, reliable Socratic tutoring, and process-death, reboot,
and fold-transition recovery remain unverified. A valid source ID proves
provenance; it does not prove the generated explanation follows from the passage.
PR-B establishes evaluation before further quality improvements and model selection.

## 2. Delivery rules

Preserve GPLv3 licensing, a maintainable single-user installation, native Android
with offline storage and no core Google Play Services dependency, and local
inference by default. Cloud inference remains optional. Keep the canonical store
in PostgreSQL, deployment Podman-compatible, and access private and authenticated.

The labels below are planning labels, not assigned GitHub numbers. Each item is
one focused PR with a user-visible outcome. Create new branches from current
`main`; dependent work waits for its prerequisite or uses an explicitly documented
stack. Keep unrelated changes in separate PRs.

For every implementation PR: write a failing behavior test, implement the
smallest passing change, refactor while green, run relevant regression checks,
then obtain a separate reviewer/verifier pass. Record the red/green evidence,
acceptance result, limitations, and rollback implications in the PR. Documentation
changes use link/content checks and separate review. Never describe fixture
tests as live acceptance or a proposed feature as shipped.

## 3. Immediate delivery: close the learning loop

### PR-A — Verify the complete Android article workflow

**Status:** Implemented and verified in [PR #3](https://github.com/bearyjd/open-learning/pull/3),
which is merged. Its test covers same-process Activity/ViewModel relaunch;
process-death and reboot recovery remain PR-D work.

**Deliver:** Run import → processing → feed → save → two tutor turns → due review
through the Android UI against the actual API, worker, database, and local model.
Fix only blockers in that path and document reproducible acceptance steps.

**Accept:** An isolated acceptance package and owned test data complete the loop;
saved cards and review dates persist after relaunch. Add regression tests for
observed failures. Retain sanitized evidence and record timings/errors without
publishing private reading material. **Depends on:** #1.

### PR-B — Establish model and tutoring quality gates

**Status:** Evaluation tooling and the first local baseline are in
[PR #4](https://github.com/bearyjd/open-learning/pull/4). The baseline fails the
semantic gate: eight contract-valid outputs, three meeting the rubric, and two
critical failures under independent AI-assisted review. See
[model evaluation](model-evaluation.md). Improving the failing behavior takes
priority over expanding ingestion; broad model quality remains unverified.
The held-out evaluation in [PR #5](https://github.com/bearyjd/open-learning/pull/5)
records a prompt candidate that failed to improve these samples; the candidate
was reverted. A compact local model was compared next.
The text-only Qwen3-VL 4B comparison also failed the fixed semantic gate on
both primary and holdout samples; see the [bound results](model-evaluation.md#compact-local-model-comparison).
No model change has been selected. Source-grounded claims, explicit learner
assessment, and useful follow-up questions remain the next quality work.

**Deliver:** A small, versioned corpus of legally reusable passages and questions,
including misleading premises, partial excerpts, unsupported questions, and
deliberate misconceptions. Score card support, citation relevance, correct answer
assessment, useful follow-up questions, and appropriate uncertainty. Define
explicit release thresholds before comparing candidates.

**Accept:** Deterministic tests reject invalid contracts; independently reviewed
sample outputs measure semantic support and tutoring quality. Valid references
paired with false prose must appear in the evaluation. Publish failures as well
as successes; narrow prompts or abstain when evidence is insufficient.
**Depends on:** #1; incorporates PR-A failures.

### PR-C — Add optional OpenRouter with spending and privacy controls

**Deliver:** Validate OpenRouter through the existing OpenAI-compatible backend
adapter. Keep keys outside Git and phone storage. Add explicit cloud opt-in,
model/provider selection, schema-capability checks, request/output limits, usage
reporting, a spend ledger, and an upstream key-limit backstop.
Explain which excerpts and tutor messages leave the machine. Never silently
switch local requests to a cloud service.

**Accept:** Mocked provider tests cover authentication errors, throttling,
timeouts, unsupported schemas, malformed output, provider selection, and budget
exhaustion. Reserve estimated maximum cost before dispatch and reconcile usage;
require `require_parameters: true` when routing structured-output requests;
fail closed when pricing or budget is unknown. Retries consume the same budget;
local estimates cannot guarantee the provider's final bill.
Run paid acceptance only with an explicit spending allowance. Benchmark a small
set of currently available low-cost models against PR-B, comparing total task
cost, latency, and quality. Free offerings are optional candidates, not an
availability or privacy guarantee. Verify current [model metadata](https://openrouter.ai/docs/api/api-reference/models/list-all-models-and-their-properties),
require [schema-compatible routes](https://openrouter.ai/docs/guides/features/structured-outputs),
and configure [provider/privacy restrictions](https://openrouter.ai/docs/guides/routing/provider-selection)
using the [data policy](https://openrouter.ai/docs/guides/privacy/data-collection).
**Depends on:** PR-B for selection; #1 for adapter compatibility.

### PR-D — Make recovery and fold behavior dependable

**Deliver:** Recover pending ingestion, cached reading state, tutor history, and
review state across process death, backend/worker restart, and device reboot;
verify compact, expanded, and folded/unfolded transitions.

**Accept:** Physical-device and service-restart scenarios show no duplicate
publication, lost saved cards, or repeated review ratings. Check disconnected
recovery and reauthentication. Use isolated app data and avoid global device
setting changes. **Depends on:** PR-A; may proceed alongside PR-B/C.

## 4. Source ingestion: one format or adapter per PR

All imports preserve source identity, original text, excerpt boundaries, and
import failures. Extracted partial content must never imply whole-book coverage.

| Proposed PR | Deliverable | Acceptance | Dependency |
| --- | --- | --- | --- |
| PR-E1: Text/Markdown ingest (backend) | `POST /api/v1/ingest/text` for user-provided plain text/Markdown through the source pipeline | Encoding, control characters, size limits, request body cap, duplicate imports, retry, lease safety, and migrations on SQLite and PostgreSQL tested | PR-A/D |
| PR-E1b: Note forgetting and caps | `GET /api/v1/notes` and `GET /api/v1/jobs?kind=note` (paginated), hard `DELETE` of a note and its derived data, `DELETE` of note jobs, pending-job cap and bounded retention of failed-job text | Full-graph deletion, job cancellation, delete-during-generation, caps, retention purge, and PostgreSQL races tested | PR-E1 |
| PR-E2: Note editing and source management | Edit notes with versioned citations and Android file picker, attribution rendering and Room migration | Edits, source-version citations, and Android attribution tested | PR-E1 |
| PR-F: RSS subscriptions | Add subscriptions, bounded polling, deduplication, and article handoff | Fixture feeds cover malformed entries, redirects, repeat polling, failures, and fetch security | PR-E source contract |
| PR-G: PDF import | Extract text with page attribution and explicit extraction limits | Text PDFs, malformed files, page mapping, size limits, and scanned-document errors tested; OCR separate | PR-E |
| PR-H: DRM-free EPUB import | Parse chapters with stable attribution | Legal fixtures cover archive traversal, oversized expansion, duplicate imports, and chapter order | PR-E |
| PR-I: Kobo exports | Import supported user-provided annotation/export files | Sanitized fixtures preserve book references and distinguish annotations from unavailable full text | PR-H/source contract |
| PR-J: Libby exports | Import supported user-provided exports with documented limitations | Validate currently supported formats and terms, fail clearly for unsupported inputs | PR-E/source contract |

Kobo and Libby work uses lawful user exports. DRM bypass and private service API
integration are outside scope.

## 5. Retrieval and personalized learning

| Proposed PR | Deliverable | Acceptance | Dependency |
| --- | --- | --- | --- |
| PR-K: Embeddings and semantic search | Versioned embedding jobs, indexing, deletion/rebuild, lexical fallback | A judged retrieval set measures relevance; document changes/deletions cannot leave stale visible results; provider privacy/budget controls apply | PR-B/E |
| PR-L: Cross-document Explore | Retrieval-grounded answers with inspectable citations and abstention | Answerable and unsupported questions exercise relevance, conflicting sources, injection attempts, and deleted sources; assess entailment separately from valid IDs | PR-B/K |
| PR-M: Personalized finite feed | At most ten daily cards with topic diversity and explainable preferences | Deterministic tests cover sparse libraries, duplicates, muted topics, day boundaries, concurrent requests, and a clear end state | PR-B/E; K if semantic ranking used |
| PR-N: Learning organization | Collections, editable preferences, and understandable progress | Test ownership, offline reconciliation, accessibility, and transparent metric definitions; avoid unsupported learning claims | PR-D/M |

## 6. MVP exit and release operations

The first personal-use MVP requires PR-A, PR-B, PR-D, and PR-E with quality gates passing and
local inference supported. OpenRouter is an optional MVP enhancement. RSS,
additional formats, semantic retrieval, and personalization are staged expansions.

**PR-O — Upgrade, backup, and security release readiness:** Validate migrations
from the previous release, encrypted/private backup handling, restore into a fresh
installation, credential rotation, dependency/license checks, and installation
instructions. Exercise failure and recovery using owned data. Release after the
chosen MVP dependencies pass independent review; repeat upgrade/restore checks
for later schema changes.

## 7. Research sources and later learning tools

Add OpenAlex, Crossref, arXiv, and public-domain book adapters in separate PRs
after the ingestion contract is stable. Each adapter must verify its current
official API/terms, preserve DOI or other stable identifiers and attribution,
and distinguish metadata, abstracts, excerpts, and legally accessible full text.
Accept using distributable fixtures, rate-limit/retry tests, duplicate detection,
and evidence that abstracts cannot produce purported full-paper or full-book
summaries. Catalog metadata and lawful borrowing links can follow Kobo/Libby
imports without assuming a private library API.

Later PRs may add legitimately available YouTube or podcast transcripts and
Android TextToSpeech or Piper playback. Define transcript rights, provenance,
offline behavior, and acceptance criteria before implementation. Improved review
scheduling and cross-discipline synthesis should follow measured retrieval and
learning-quality results. Validate untested workflows before assigning dates or
GitHub PR numbers.

Defer FSRS, OCR, audio/video/transcripts, social features, hosted multi-user
operations, and analytics infrastructure until a concrete user need justifies
their own scoped PRs. Revisit this sequence after each accepted milestone using
measured failures, quality, latency, and operating cost.
