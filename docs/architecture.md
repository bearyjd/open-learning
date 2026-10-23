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
from "has a successor". Each version is forgotten on its own with
`DELETE /api/v1/notes/{id}`. **Forgetting the latest version does not make the
previous version current again**: it stays superseded and hidden until it is forgotten
too. That is the privacy-safe choice: text the user replaced never comes back by
itself. Forgetting a version also cancels unfinished edit jobs that would replace it
and clears their text. A newer version's `replaces_document_id` may then point to a
forgotten id; it is a plain id without a foreign key.

Note keys are content hashes, so text identical to any existing note document
(current or superseded, the same note or another) cannot become a version. An edit to
such text, or a plain ingest whose text matches a superseded version, fails with the
fixed error "This text matches another note or version; change it or forget that note
first". A plain ingest matching a current note still deduplicates as before. Two edits
of the same version both queue; the first to complete wins and the other fails with
"This note was changed or forgotten; edit its latest version".

`GET /api/v1/notes/{id}/text` returns `document_id`, `title`, `label`, `version` and
`text` for any note version, with `Cache-Control: no-store` (also on its 404). It
returns 404 for article documents.

Additive response fields: `NoteOutput.version`, `.replaces_document_id`,
`.superseded`; `InsightOutput.source_version` and `Citation.source_version` (1 for
articles and unedited notes); `replaces_document_id` on job responses and the note job
list. Migration 0005 adds `documents.version` (default 1), `documents.replaces_document_id`
and `documents.superseded` (default false). Its downgrade refuses while superseded
versions or queued/running edit jobs exist, because older code would show hidden
versions again or import edits as separate notes. A failed edit job that survives a
downgrade would import as a separate note if retried on older code.
