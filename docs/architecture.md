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

The daily insight feed is bounded to ten cards from imported material. RSS/Atom
subscriptions also populate a separate discovery queue, described below. Neither
path guarantees ten new cards per day. Discovery can derive topics from current
library content and rank queued items as described below. Review
ratings update a simple deterministic schedule with stored history. Later ranking
can add topic diversity without replacing document provenance or the review API.

See [future adapters](../integrations/README.md) for Kobo/Libby boundaries. The
database includes extension points for topics, collections, preferences, and
embeddings; schema presence alone does not imply complete user-facing features.

## Feed discovery (PR-F, backend only)

Authenticated `/api/v1/feeds` supports adding and listing subscriptions;
`DELETE /api/v1/feeds/{id}` removes one, and `POST /api/v1/feeds/{id}/refresh`
explicitly refreshes it. Adding a feed validates and fetches it once. Duplicate
subscriptions are rejected and there are at most 50 feeds. `GET /api/v1/discover`
lists the queue by status with cursor pagination, newest first, at most 100 items
per page, returning `items` and `next_cursor`. Cursors order by discovery creation
time and UUID, so equal timestamps and intervening inserts remain stable.
`POST /api/v1/discover/{id}/dismiss` dismisses an item;
`POST /api/v1/discover/{id}/ingest` hands its URL to the existing article-ingest
job path and marks it ingested. Discovery itself makes no model calls and never
automatically ingests an article.

The existing worker polls feeds using `OL_FEED_REFRESH_INTERVAL_SECONDS`
(default 21600, six hours), jitter and exponential failure backoff. Conditional
requests use stored ETag and Last-Modified validators. After ten consecutive
failures a feed is paused rather than deleted; an explicit refresh provides a
recovery path. Jitter is a stable per-feed offset of up to ten percent, surviving
worker restarts. `last_fetched_at` records the latest attempt, including failures;
an explicit refresh returns the feed state with its fixed `last_error` code.
Polling makes no AI ranking, scraping or external search calls.

Fetches hold no database transaction open. Refresh applies its result only if its
saved feed snapshot is still current. Feed and item mutations lock the parent
feed before changing queue state; PostgreSQL uses row locks and SQLite reserves
the writer before reading. Subscription admission also serializes the 50-feed
cap. Enqueueing an article job and marking its item ingested commit together.

RSS 2.0 and Atom responses are bounded to 1 MiB and 50 items per fetch, with
network deadlines and redirect limits. Discovery stores URLs, titles, publication
times and plain-text snippets of at most 500 characters, never full article text.
Each feed retains at most `MAX_STORED_ITEMS` (default 200), keeping the newest
rows by creation time and UUID. Refresh prunes excess rows and dismissed/ingested
metadata older than `ITEM_RETENTION_DAYS` (default 30, measured from discovery
creation) in the same transaction as insertion, including on unchanged or failed
fetches. Age pruning preserves `new` items; cap eviction preserves the newest
configured number of rows. Article jobs and their document links survive metadata
pruning. URL deduplication lasts while a row is retained; evicted URLs can be
discovered again.
Migration 0006 adds `feeds` and `discovered_items`, with per-feed URL deduplication,
`new`/`ingested`/`dismissed` item states and cascading removal of a feed's queue
items. Its `(status, created_at, id)` index serves newest-first queue pagination.
Downgrade drops these discovery tables. Android subscription and queue UI
are outside this backend slice. See [security](security.md) for fetch and XML
boundaries and [discovery verification](verification-discovery.md) for checks and remaining gaps.

## Discovery topics and relevance (backend only)

Authenticated `GET /api/v1/topics?limit=20` derives topics at read time, with a
limit from 1 to 50. Its data contains `topics` (`term`, `weight`, `count`) and
`pinned` (`id`, `term`, `created_at`). Extraction reuses `current_documents()` so
forgotten and superseded documents do not contribute. Reads are bounded to the
newest 500 current documents and 500 current insights, with text truncated in SQL.
Unicode tokenization, English stopwords, unigrams and bigrams produce deterministic
frequency-based weights; saved insights and explicit pins receive extra weight.
Derived topics are never stored, so the next read reflects forgetting or version
changes without a profile-cleanup job.

`POST /api/v1/topics/pinned` accepts `{term}` and returns 201, including when the
normalized term is already pinned. Terms contain at most five normalized Unicode
words and 80 characters; at most 30 pins are retained. Deleting a pin through
`DELETE /api/v1/topics/pinned/{id}` returns 204, or 404 for an unknown ID.
Migration 0007 adds only the explicit `pinned_topics` table; downgrade removes it.
Pins are independent user choices and persist until explicitly deleted.

`GET /api/v1/discover` accepts `sort=newest|relevance`; newest remains the default
with the existing ordering and cursor. Items expose `score` and `matched_topics`
(at most five). Relevance scores weighted topic overlap in title and snippet,
with title matches weighted higher, then breaks ties by newest creation time and
ID. It ranks only a bounded window of the newest 500 matching queue items.
The first page anchors that window by `(created_at, id)`; later inserts above the
anchor are excluded from subsequent pages. Relevance cursors bind the status,
sort, corpus and candidate ranking using hashes without plaintext topic terms.
A changed corpus or ranking returns a fixed 409 error requiring pagination to
restart. This is a bounded queue ranking, not a ranking of the entire archive.
Both topic and discovery responses use `Cache-Control: no-store`.

No model, embedding, paid API or external search call is involved. Ranking does
not fetch or automatically ingest articles. Android topics and ranking UI are
outside this slice. See [discovery verification](verification-discovery.md).

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

A note has at most `MAX_NOTE_VERSIONS` (100) versions, so the header holds at most 100 ids
(36 characters plus a comma each, about 3.7 KB) and always fits an HTTP header; without a bound
about 1,800 versions would exceed 64 KB, which a client rejects after the forget has committed.
`POST /api/v1/ingest/text` with `replaces_document_id` answers 409 "This note has reached the
version limit; forget it or start a new note" when the lineage already has 100 versions (counted
by `note_id` under the replaced document's lock). The worker re-checks under the same lock
before publishing, so concurrent edits cannot exceed the cap: the job fails with the same
message and its payload text, title and label are cleared.

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
