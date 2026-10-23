# Discovery slice 2: topics and relevance ranking (backend, deterministic)

Goal: Open Learning learns what the user cares about from what they already keep, and ranks the to-read queue (slice 1: feeds + discovered_items) by it. Private, no paid APIs or cloud keys, NO model calls (deterministic only), no new dependencies. Android out of scope.

## Requirements
- Topics are derived at read time from the user's CURRENT data only: insights/notes/saved items of non-superseded, non-forgotten documents (reuse current_documents()). Nothing derived is persisted, so forgetting a note or version removes its influence immediately and leaves no residue. Bound all scans (e.g. newest 500 documents/insights, text truncated per item).
- Extraction: lowercase tokenization, stopword list (English), min length, unigrams + bigrams, simple TF-IDF-like weighting across the user's documents, top N (default 20, max 50). Deterministic ordering and tie-breaks. Unicode-safe, no regex backtracking risks.
- Pinned topics: user can pin/unpin explicit topics (small table `pinned_topics`: id, term unique, created_at; cap 30; terms normalized/length-capped; migration 0007 with downgrade). Pinned terms get a fixed boost.
- API (authenticated /api/v1, existing envelope/error style): GET /topics (derived top-N with weight + count, plus pinned), POST /topics/pinned {term}, DELETE /topics/pinned/{id}. GET /discover gains `sort=newest|relevance` (default newest, unchanged behavior) and each item gains `score` and `matched_topics` (max 5 terms). Relevance = weighted overlap of topic terms with item title (higher weight) + snippet; deterministic; tie-break newest then id; pagination must stay stable and bounded for relevance sort (document the approach, e.g. score a bounded window of newest N items).
- Security/privacy: terms from notes are the user's own content: never log them, never put them in error messages; GET /topics is no-store; cap sizes; validate/normalize pinned terms; no SSRF surface added.
- Tests (TDD): extraction determinism and stopwords/bigrams/unicode; forgotten/superseded documents do not contribute; pinned CRUD + caps + validation; relevance ordering and stable pagination; default sort unchanged; auth on every route; migration 0007 up/down on SQLite and Postgres; performance bound test (large corpus stays within caps).
- Docs: architecture.md, security.md notes, verification.md honest unverified list, ROADMAP row.

## Non-goals
No embeddings/LLM ranking (later), no web search, no auto-ingest, no Android UI, no persisted topic profile.
