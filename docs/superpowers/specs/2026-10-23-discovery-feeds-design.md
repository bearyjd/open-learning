# Discovery slice 1: feeds and a "to read" queue (backend)

Goal: Open Learning finds articles on its own. The user subscribes to RSS/Atom feeds; the worker fetches them on a schedule and fills a "to read" queue; the user ingests or dismisses items. Private, self-hosted, no paid APIs or cloud keys, no new model calls (deterministic only). Android is out of scope.

## Requirements
- Migration 0006: `feeds` (id uuid, url unique, title, created_at, last_fetched_at, etag, last_modified, last_error fixed-code string, consecutive_failures) and `discovered_items` (id uuid, feed_id fk cascade, url, title, published_at, snippet (<=500 chars plain text), status new|ingested|dismissed, created_at, unique(feed_id,url)). Downgrade drops both.
- API (all under authenticated /api/v1, existing envelope/error style): POST /feeds {url} (validate http/https, reuse the existing SSRF-safe URL validation/fetch used by article ingest; fetch once to validate it parses; cap feeds at 50, 409 on duplicate); GET /feeds; DELETE /feeds/{id}; GET /discover?status=new&limit&cursor (newest first, paginated, hard cap 100); POST /discover/{id}/ingest (reuses the existing URL ingest path, marks item ingested, returns the job); POST /discover/{id}/dismiss; POST /feeds/{id}/refresh.
- Worker: periodic refresh (interval setting, default 6h, jitter), conditional GET (ETag/Last-Modified), per-feed response size cap (1 MiB), item cap per fetch (50), timeouts, redirects re-validated per hop (as article fetch does), exponential backoff after failures, feed auto-paused (not deleted) after 10 consecutive failures. Parse RSS 2.0 and Atom with a safe parser (no XXE/entity expansion; prefer stdlib with defusedxml only if already a dependency, otherwise justify one small dependency). Strip HTML from snippets. Never store full article text at discovery time.
- Security: SSRF protections identical to article fetch; item URLs validated before storing; fixed error codes only (no remote body in errors/logs); limits on counts and sizes; no secrets in logs.
- Tests (TDD): unit tests for parsing (RSS/Atom, malformed, XXE/billion-laughs, HTML in snippets, huge feed), SSRF refusals (private IPs, redirects to private, non-http), dedupe, caps, backoff/pause, API auth + validation + pagination + ingest/dismiss flows, migration up/down on SQLite and Postgres. Mock all network; no live fetches.
- Docs: docs/architecture.md section, docs/security.md threat notes, docs/verification.md honest unverified list, docs/ROADMAP.md row.

## Non-goals / anti-goals
No scraping of arbitrary sites, no web search API, no cloud LLM, no model-based ranking yet (slice 2: topic suggestions and ranking from saved insights, local model optional), no Android UI, no auto-ingest (user decides what to ingest).
