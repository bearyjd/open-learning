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
