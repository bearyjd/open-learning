# Discovery verification

## Feed discovery (PR-F, backend only)

Verified on 2026-10-23 with Python 3.13.13 and frozen dependencies:
`uv run --frozen ruff check .` passed; `uv run --frozen pytest -q` produced
**470 passed, 48 skipped on SQLite** and **514 passed, 4 skipped on PostgreSQL**.
PostgreSQL 16.15 ran in a uniquely named disposable `pgvector/pgvector:pg16`
container with tmpfs storage, a random localhost port and a separate migration
database, matching CI's database separation. SQLite skips require PostgreSQL;
PostgreSQL skips the SQLite ingest regression and three vanished-row simulations.

Migration 0006 upgrade/downgrade, constraints, cascade deletion and Alembic metadata
checks passed on both databases, as did the CI downgrade-to-0002/upgrade cycle.
New discovery tests mock all feed HTTP/DNS operations and cover parsing, encoded
DTDs, snippets, SSRF/redirects, limits, conditional polling, deduplication, backoff,
pause/recovery, auth, pagination and concurrent admission/refresh/item decisions.
TDD failures preceded implementation; the SQLite double-ingest regression returned
two 202s before its fix and now returns 202/409 with one job. Independent security
and integration reviews found no unresolved issues after that fix. The full suite
emits one upstream Starlette TestClient deprecation warning; no dependency changed.

The review fixes were also test-first: retention regressions produced eight
failures before implementation, and the composite-index assertion failed before
the schema change. Regressions cover rotating URLs bounded to the newest 200,
configured caps, deterministic timestamp/ID ties, feed isolation, 30-day terminal
expiry on parsed/304/failed refreshes, retained deduplication/status, ingestion
continuing after metadata pruning, and rollback after insert/delete failures.
Migration 0006 checks `(status, created_at, id)` on upgrade and re-upgrade after
downgrade on both databases. Independent code review found no blocking issues.

Still unverified: live RSS/Atom provider behavior and production polling timing,
jitter/backoff and recovery over sustained operation. Android subscription and
queue UI are outside this slice; no Android build or device result is claimed.

## Topics and relevance ranking (backend only)

Initial verification on 2026-10-23: frozen Ruff checks passed. The frozen full
suite produced **498 passed, 49 skipped on SQLite** (50.89 seconds) and
**543 passed, 4 skipped on PostgreSQL** (97.78 seconds). PostgreSQL 16.15 ran in
an owned, uniquely named disposable `pgvector/pgvector:pg16` container with tmpfs
storage, random loopback port 33177 and a separate migration database. Container
cleanup remains pending.

Migration 0007 upgrade/downgrade and Alembic metadata checks passed on both
databases. The manual CI migration sequence (head, check, downgrade to 0002,
upgrade, check) also passed. Topic tests initially produced 22 failures before
implementation; one initial fixture was corrected. The SQLite migration 0007
check failed once before implementation. Independent review added four failing
query-redaction tests before the fix. Independent code, security and documentation
reviews approved the resulting change.

New tests use fixtures without external network requests. Evidence covers topic
extraction, current-document visibility, bounded reads, pin validation and
admission, authentication, relevance pagination and default ordering. These are
initial results: the self-adversarial pass after the initial commit is still
pending, so final verification is not yet claimed.

No Android build or device run, model call, live feed provider test, or sustained
production polling verification is claimed for this slice.
