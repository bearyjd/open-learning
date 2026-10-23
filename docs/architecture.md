# First milestone

The Android app is a native Kotlin/Compose client. Retrofit calls the authenticated
FastAPI API, Room caches cards for offline reading, and WorkManager refreshes in
the background when a connection is available. Manual dependency construction
keeps a single-user installation small. The device never connects directly to
PostgreSQL or the inference server.

PostgreSQL is the canonical store. SQLAlchemy models and Alembic migrations
define the schema. A separate worker claims durable database jobs; no Redis,
scheduler service, or vector service is required. API and worker must run the
same code and use the same database. Job completion publishes insights only
after validating generated JSON and resolving supplied source-span IDs to their
original text and document chunks. The model selects a reference before drafting
one narrow card; it does not generate the quoted source text. Unknown references
and legacy quote/index fields fail the entire batch. Tutoring selects a source
span before its answer and recall question. Both paths check canonical quotations
against their original source text; invalid tutor responses write no chat or
review state.
Jobs have a kind. `url` jobs fetch an article. `note` jobs come from
`POST /api/v1/ingest/text` and carry their title, text and label in the job row
until completion; they skip fetching and then follow the same chunk, model and
publish path. A note is a `Source` of kind `note`, keyed internally by `note:`
plus the SHA-256 of its normalized text, so identical text is imported once (the
first title and label win and a repeat makes no model call), while different text
under the same title becomes a separate source. API responses expose
`source_kind` and `source_label`, and `source_url` is empty for notes. Note
forgetting, metadata edit and versions are described below. Downgrading migration 0003 needs the API
and worker stopped; it discards failed-note text and labels (retry then returns
409) and leaves `note:<sha256>` keys visible as `source_url` to older code.
Model errors become retryable failed jobs rather than fabricated fallback cards.

The inference boundary is an OpenAI-compatible `/v1/chat/completions` endpoint.
llama.cpp, Ollama, and optional remote providers use the same adapter. No model
weights or API keys are distributed. Embedding storage is reserved in the schema;
the initial search path is lexical. Tutor feedback is AI interpretation grounded
in a quoted passage, not an independent verification of factual truth. An insight
with a valid citation can still make unsupported claims; source selection does
not establish logical entailment.

Discovery is bounded to ten cards from imported material. This milestone does
not crawl the web, learn a recommendation profile, or guarantee ten new cards per
day. Review ratings update a simple deterministic schedule with stored history.
Later recommendations can add topic diversity and ranking without replacing
document provenance or the review API.

See [future adapters](../integrations/README.md) for Kobo/Libby boundaries. The
database includes extension points for topics, collections, preferences, and
embeddings; schema presence alone does not imply complete user-facing features.

## Note versions (E2b-4)

Editing a note's text creates a new version; old versions are kept, not destroyed.
The client resubmits through `POST /api/v1/ingest/text` with the usual `title`,
`text` and optional `label` plus `replaces_document_id` (a UUID). The same body cap
(413), pending-job cap (429), validation and no-echo rules apply. The API rejects an
unknown or non-note id (404), a superseded version (409, only the latest version can
be edited) and normalized text identical to the replaced version (422). The job is
queued with `jobs.replaces_document_id`. When it completes, the new document gets
`version` N+1 and `replaces_document_id`, and the replaced document is marked
`superseded` in the same transaction (lock order: replaced document, job, source).

Superseded versions keep their text and derived insights but are hidden from the
feed (including today's cached feed), library, search, due reviews and
`GET /api/v1/notes`. `GET /api/v1/notes?include_superseded=true` lists them. A
superseded insight stays readable by id (`GET /api/v1/insights/{id}`, kept on purpose so
an old link or citation still resolves), but `GET /api/v1/history` omits its tutor
messages, and save, feedback, review answers and the tutor on it answer 404 without
writing anything. These writers share-lock the document and re-check that it is current
before locking the insight, so an edit published mid-request cannot slip a write onto the
superseded version; the tutor re-checks after its model call. `superseded` is a stored flag, not derived
from "has a successor".

Every version carries a lineage id, `documents.note_id`: the root version's id, copied
to each new version. **`DELETE /api/v1/notes/{id}` with any version's id forgets the
whole note**: every version in the lineage, their insights, saved items, reviews and
tutor history, their linked jobs, and unfinished edit jobs of any version (cancelled,
text cleared), in one transaction that locks the versions in id order. Edit jobs that
remain (cancelled, failed, or completed on a version that is kept) have the forgotten id
cleared from `replaces_document_id`, so job metadata keeps no forgotten ids. A version
published while the forget waits for those locks is found by a second pass and forgotten
too. Forgets of one note are serialised by a transaction-scoped PostgreSQL advisory lock on
the lineage id, taken before any document lock, so two overlapping forgets cannot deadlock
over a version published between their passes. The answer stays 204 with no body (the
current Android client expects a 2xx and ignores the body) and adds the header
`X-Forgotten-Document-Ids`: every forgotten document id, comma-separated and sorted.

**Required client change before any client can create versions:** Android today purges and
tombstones only the id it sent, so after a whole-note forget a cached older version would
stay readable offline. In practice no versions exist yet, because Android does not send
`replaces_document_id`. The Android E2b-4 PR must first make forget lineage-aware: purge and
tombstone every id in `X-Forgotten-Document-Ids`, falling back to the versions listed by
`GET /api/v1/notes?include_superseded=true` when it has no header (for example a forget
verified after a 404). See the ROADMAP row. This is a client-facing change from the first E2b-4 draft, where DELETE removed
one version only: forgetting a note must not silently leave its other versions behind.
`DELETE /api/v1/notes/{id}?version_only=true` forgets one version, but only a superseded
version or a note's only version: for the current version of a note that still has earlier
versions it answers 409 "This note has earlier versions; forget the whole note instead",
so the earlier versions' text is never silently left behind.
**Forgetting only the latest version does not make the previous version current
again**: it stays superseded and hidden until it is forgotten too. That is the
privacy-safe choice: text the user replaced never comes back by itself. A newer
version's `replaces_document_id` may then point to a forgotten id; it is a plain id
without a foreign key, and the lineage id keeps working after its root is forgotten.

Note keys are content hashes, so text identical to any existing note document
(current or superseded, the same note or another) cannot become a version. An edit to
such text, or a plain ingest whose text matches a superseded version, fails with the
fixed error "This text matches another note or version; change it or forget that note
first". A plain ingest matching a current note still deduplicates as before. Two edits
of the same version both queue; the first to complete wins and the other fails with
"This note was changed or forgotten; edit its latest version". If both carry identical
text (a client retrying the same edit before the first ran), the second completes on the
version the first published, or fails with that stale message once that version has been
superseded. A retry POST sent after the new version is published still gets 409, because
the version it names is no longer the latest.

`GET /api/v1/notes/{id}/text` returns `document_id`, `title`, `label`, `version` and
`text` for any note version, with `Cache-Control: no-store` and `Pragma: no-cache`
(also on its 404). It returns 404 for article documents.

Edits orphan the old version's study state: saved items, review items and tutor history
stay attached to the superseded version's insights, which are hidden, and are not carried
over to the new version's insights. Whether to migrate them is a product decision that is
still pending; today an edit effectively resets saves and reviews for that note.

Forget keeps the outcome of a job that already finished: a failed or cancelled job whose
text matches (or an unfinished-edit job of a forgotten version that has already failed)
keeps its status and error but has its title, text and label cleared; only queued and
running jobs become `cancelled`.

Additive response fields: `NoteOutput.version`, `.replaces_document_id`,
`.superseded`; `InsightOutput.source_version` and `Citation.source_version` (1 for
articles and unedited notes); `replaces_document_id` on job responses and the note job
list. Migration 0005 adds `documents.version` (default 1), `documents.replaces_document_id`,
`documents.superseded` (default false) and `documents.note_id` (backfilled to the
document's own id), plus indexes on `documents.note_id` and `jobs.replaces_document_id`.
Its downgrade refuses while superseded versions, queued/running edit jobs, or failed edit
jobs that still hold text exist, because older code would show hidden versions again or
import edits as separate notes. Upgrading again after a downgrade resets lineage: every
document becomes its own root (`note_id = id`), which is harmless because the downgrade
only succeeds when no superseded version exists.
