# Historical note verification results

Moved from [verification.md](verification.md) without changing the recorded results.

### E2b-3 device re-run after the tombstone change (2026-10-22, one run, no retries)

Instrumented suite: 72 tests run, 4 failed: three `ForgetQueueAcceptanceTest` cases (restart, 401-retry, 5xx) and
`NoteAcceptanceTest.offlineForgetHidesNowThenDeletesOnReconnect`. Script `scripts/android_notes.py`: exit 1 with
`online_waiting_gone` false and `list_forgotten_message` false. Diagnosis (static, read-only agent, high confidence): stale tests and
script, not app bugs. Since 8f8467b a successful delete leaves a CONFIRMED `PendingForget` tombstone (7-day TTL), so the tests that
waited for `forgets().isEmpty()` could never pass, and the script matched the waiting phrase inside the forget banner and waited
for the removed "Note forgotten" text. The 404 case (`a404CountsAsSuccess`) was not among the device failures; it was changed the
same way as a precaution, because it had the same stale wait.

Fixes (code only, NOT re-run on a device): those waits now expect `[FORGET_CONFIRMED]`; the 5xx test switches the fixture to 204 only
after the first 503 was served, then waits for a second DELETE; `ListFixture` records a 404 DELETE as deleted so the 404 check sees
the note gone; the script matches the whole live waiting line only (and only on a dump that shows the list), waits for the DELETE
before counting it, and requires "Note hidden on this device". The forget banner no longer embeds a waiting count (it went stale).
It still tells the person when earlier forgets failed ("N forgotten notes could not be deleted. Retry from Your notes."), because
failed rows need manual action; they stay visible, with Retry and Stop trying, only inside Your notes. Offline forget is not yet
re-verified on a device after these fixes.

Still unverified: offline forget end to end, the script's offline scenario, 429/413 after E2b-3, confirm-dialog FLAG_SECURE, fold/recreate
with the list open, SAF picker via DocumentsUI, the token-rotation notice, sweep worker timing.

## E2b-4 note versions (backend only)

Run on SQLite and on a disposable `pgvector/pgvector:pg16` container with a separate migration
database, as CI does: `ruff check .`, `alembic upgrade head`, `alembic check`, downgrade to 0002 and
back, and the full pytest suite. New tests: `tests/test_note_versions.py` (edit creates a linked version
and supersedes the old one, version chain, submitted title/label, malformed/unknown/article/superseded
ids, unchanged text, edit to another note's or an old version's text, plain ingest matching a superseded
version, dedupe against the current version, two queued edits of one version, forget cancelling a pending
edit, forget of the latest version not resurrecting the previous one, forget of an old version, list
fields, job list field), `tests/test_note_version_reads.py` (feed including today's cached feed, library,
search and due reviews hide superseded insights; superseded insight still readable by id; `source_version`
on insights and tutor citations; tutor on a superseded insight is 404 with no model call or writes; note text endpoint with `no-store`, 404s and auth; forget committed while
the edit's model call runs) and `test_0005_document_versions_roundtrip` in `tests/test_migrations.py`.
Four existing exact-shape tests were widened for the additive fields.

Review fixes (adversarial review of PR #24), same environment: `tests/test_note_version_races.py` (forget
committed before an edit's job insert gives 404 and no job; threaded PostgreSQL test where forget waits on
the edit request's document lock and then cancels the new job and clears its text; stale and target-gone
edit jobs drop their text at once; the tutor writes nothing when an edit publishes during inference),
`tests/test_note_lineage.py` (versions share the root `note_id`; DELETE with any version's id forgets the
whole note, including a pending edit of the latest; `?version_only=true`; lineage after a version-only
forget of the root; threaded PostgreSQL test where a version published while forget waits on the lineage
locks is forgotten too), history and save/feedback/review answers on superseded insights, idempotent
retried edits, finished job outcomes kept on forget, `Pragma: no-cache`, and the 0005 `note_id` backfill,
indexes and failed-edit-job downgrade guard. The new behaviour tests were seen failing before their fix,
except `test_version_only_forget_keeps_other_versions` and `test_whole_note_forget_unknown_and_article_are_404`
(regression guards that passed before) and the `note_id`/index assertions in `test_0005` (written with the
migration). Mutation checks: removing the lineage re-select pass fails the lineage threaded test, and
removing the tutor's post-inference check fails the tutor test, every time. Committing right after the edit
request's document lock (releasing it before the insert) fails the edit-versus-forget threaded test in 3 of
5 runs only, because forget's job scan can still land after the request commits; that test proves the
fixed path is correct, not that every regression is caught.

Second review round: per-note forget advisory lock (threaded PostgreSQL test with two overlapping
whole-note forgets and a publish between their passes; without the lock PostgreSQL reported a deadlock),
an identical edit losing the publish race completes on the winner's version (deterministic interleaving),
edit jobs whose target version is truly deleted before or during inference fail stale without text,
version-only forget refused (409) for a current version with earlier versions, forgotten ids cleared from
remaining edit jobs, and the `X-Forgotten-Document-Ids` header on 204.

Version cap (Codex P2 on PR #24: an unbounded lineage could make the header exceed 64 KB after the
forget committed): `MAX_NOTE_VERSIONS = 100`, enforced on `POST /ingest/text` (409 with a fixed
message) and again in the worker publish path under the replaced document's lock (job fails with
the same message, payload text/title/label nulled). `tests/test_note_version_limit.py`: refused at
the cap, accepted one below, worker overflow fails with payload cleared, and a lineage of exactly
100 versions is forgotten with a header under 8 KB. The header contract is at most
`MAX_NOTE_VERSIONS` ids.

NOT verified:

- **Android is not lineage-aware yet (required client change).** After a whole-note forget the app purges
  only the id it sent; a cached older version would stay readable offline. No versions exist until Android
  sends `replaces_document_id`; the Android E2b-4 PR must consume `X-Forgotten-Document-Ids` first.
- No threaded PostgreSQL race test for two edit workers of one version; that interleaving is tested
  sequentially. Edit versus forget is covered by the two threaded tests above.
- Android does not use any of this yet. Gson ignores unknown fields, so the additive fields should not
  break the current app, but no Android build or device run was done for this slice.
- Forget locks hash-matching jobs and unfinished edit jobs in one pass ordered by id, then linked
  completed jobs in a second pass (the pre-existing shape); no threaded test exercises overlapping forgets.
