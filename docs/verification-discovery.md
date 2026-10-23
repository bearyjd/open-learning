# Discovery verification

## Feed discovery (PR-F, backend only)

Verified on 2026-10-23 with Python 3.13.13 and frozen dependencies: `uv run
--frozen ruff check .` passed; `uv run --frozen pytest -q` produced **470
passed, 48 skipped on SQLite** and **514 passed, 4 skipped on PostgreSQL**.
PostgreSQL 16.15 ran in a uniquely named disposable `pgvector/pgvector:pg16`
container with tmpfs storage, a random localhost port and a separate migration
database, matching CI's database separation. SQLite skips require PostgreSQL;
PostgreSQL skips the SQLite ingest regression and three vanished-row
simulations.

Migration 0006 upgrade/downgrade, constraints, cascade deletion and Alembic
metadata checks passed on both databases, as did the CI
downgrade-to-0002/upgrade cycle. New discovery tests mock all feed HTTP/DNS
operations and cover parsing, encoded DTDs, snippets, SSRF/redirects, limits,
conditional polling, deduplication, backoff, pause/recovery, auth, pagination
and concurrent admission/refresh/item decisions. TDD failures preceded
implementation; the SQLite double-ingest regression returned two 202s before its
fix and now returns 202/409 with one job. Independent security and integration
reviews found no unresolved issues after that fix. The full suite emits one
upstream Starlette TestClient deprecation warning; no dependency changed.

The review fixes were also test-first: retention regressions produced eight
failures before implementation, and the composite-index assertion failed before
the schema change. Regressions cover rotating URLs bounded to the newest 200,
configured caps, deterministic timestamp/ID ties, feed isolation, 30-day
terminal expiry on parsed/304/failed refreshes, retained deduplication/status,
ingestion continuing after metadata pruning, and rollback after insert/delete
failures. Migration 0006 checks `(status, created_at, id)` on upgrade and
re-upgrade after downgrade on both databases. Independent code review found no
blocking issues.

Still unverified: live RSS/Atom provider behavior and production polling timing,
jitter/backoff and recovery over sustained operation. Android subscription and
queue UI are outside this slice; no Android build or device result is claimed.

## Topics and relevance ranking (backend only)

Initial verification on 2026-10-23: frozen Ruff checks passed. The frozen full
suite produced **498 passed, 49 skipped on SQLite** (50.89 seconds) and **543
passed, 4 skipped on PostgreSQL** (97.78 seconds). PostgreSQL 16.15 ran in an
owned, uniquely named disposable `pgvector/pgvector:pg16` container with tmpfs
storage, random loopback port 33177 and a separate migration database. The owned
container was subsequently removed with `podman rm --force`; `podman container
exists` returned 1, confirming it was absent.

Migration 0007 upgrade/downgrade and Alembic metadata checks passed on both
databases. The manual CI migration sequence (head, check, downgrade to 0002,
upgrade, check) also passed. Topic tests initially produced 22 failures before
implementation; one initial fixture was corrected. The SQLite migration 0007
check failed once before implementation. Independent review added four failing
query-redaction tests before the fix. Independent code, security and
documentation reviews approved the resulting change.

New tests use fixtures without external network requests. Evidence covers topic
extraction, current-document visibility, bounded reads, pin validation and
admission, authentication, relevance pagination and default ordering. The
self-adversarial pass after initial commit `49b5a0f` found that private query
values leaked with `root_path='/ol'`, and topic/discovery error responses lacked
`no-store`. A mounted-path predicate fix and localized pure ASGI middleware
addressed both findings. Of 31 added privacy tests, 11 failed and 20 passed
before the fixes. A further immutability regression failed before the cache
middleware copied the ASGI response mapping and header list before adding
headers. The final privacy selection passed all 32 tests; the earlier combined
topics/privacy/discovery selection passed 74 tests before that last regression
was added. An independent verifier approved the final code with no remaining
issues. Final SQLite verification after these fixes produced **530 passed, 49
skipped** (50.40 seconds). Final PostgreSQL verification produced **575 passed,
4 skipped** (96.27 seconds). Frozen Ruff checks passed with both final runs.
SQLite skips are PostgreSQL-only checks, including migration parameters.
PostgreSQL skips comprise one SQLite ingest regression and three vanished-row
simulations that PostgreSQL foreign keys forbid. Migration 0007
upgrade/downgrade and metadata tests passed on both databases in the final
suites; the CI downgrade-to-0002/upgrade/check sequence passed in the initial
verification.

No Android build or device run, model call, live feed provider test, or
sustained production polling verification is claimed for this slice.
