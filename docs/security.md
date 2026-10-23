# Private deployment and backups

Use a single-user installation on a trusted private network, a VPN such as
Tailscale, or USB forwarding. Compose binds the API to loopback by default and
does not publish PostgreSQL. There is no multi-user authorization model. A bearer
token grants access to the entire library. Generate a random token of at least
32 characters, keep it out of shell history and Git, and rotate it by changing
the environment and recreating both API and worker containers. Update Android
after rotation. The app's credential is protected with the Android Keystore;
local cached reading content is still sensitive.

For access outside the host, use HTTPS and a private firewall rule. Android's
HTTP support requires explicit opt-in in connection settings; a private VPN protects traffic
in transit but does not replace server authentication. Never publish the port
to the unrestricted internet. Do not disable TLS certificate verification.

The article fetcher only permits public HTTP(S) destinations on standard ports.
It checks resolved addresses and redirects, pins the validated destination to
avoid DNS rebinding, applies deadlines and byte limits, and avoids environment
proxies. Private LAN URLs, localhost, and cloud metadata endpoints are rejected.
This is intentionally separate from the administrator-configured local AI
endpoint, which can be private. Keep the backend patched and apply egress
restrictions as an additional deployment defense.

Only extracted plain text is rendered; scripts and HTML are not displayed in a
WebView. There is no file upload endpoint; the only endpoint accepting user-supplied
content is `POST /api/v1/ingest/text`, described below. Future upload
adapters must enforce compressed and decompressed limits and prevent archive
path traversal. Source text can contain malicious model instructions; the AI
prompt treats it as data. Insight output selects a known source-span ID, which
the backend resolves to canonical text; unknown IDs and model-written quote/index
fields are rejected. Tutor output also selects a supplied span before its answer
and question. Both paths verify the canonical text against the original passage. These checks
establish provenance, not factual correctness or logical entailment. ID selection
removes fabricated quote copying as a failure signal, so valid references can
still accompany unsupported prose.
Tutor feedback remains labeled AI interpretation and can be wrong.

Text note endpoint: `POST /api/v1/ingest/text` (bearer-authenticated) accepts
`title`, `text` and an optional `label` as plain text; Markdown is stored as text
and never rendered. Limits: text 100 to `OL_MAX_DOCUMENT_CHARS` characters
(default 60,000; longer input is rejected, never truncated), title 1 to 500, label
at most 100 and a single line. Text is normalized (CRLF/CR to LF, Unicode NFC,
trimmed) and must be valid UTF-8 (lone surrogates are rejected). NUL, C0 controls
other than tab and newline, DEL and C1 controls are rejected; titles and labels
allow no control characters at all and additionally reject the Unicode line and
paragraph separators (U+2028, U+2029). Explicit bidirectional controls
(U+202A-202E, U+2066-2069) are rejected in all three fields to block "trojan
source" spoofing; text keeps U+2028/U+2029 because it is body text. Other format
characters such as the zero-width joiner in emoji sequences are allowed.
Rejections use fixed messages, and every 422 body-validation response (all routes,
including behind a `root_path`) omits pydantic's echoed `input` and `ctx` (for
example the `max_length` limit), so submitted text is never reflected. The status
code and the `type`, `loc` and `msg` fields are unchanged, and query and path
validation errors are not altered. The SQLAlchemy engine uses
`hide_parameters=True`, so database errors and logs cannot carry note text.
Request bodies are capped by middleware that checks the largest declared
`Content-Length` and the streamed byte count and itself answers 413 (stopping at the
first chunk over the cap, so memory stays bounded for chunked or lying clients),
before authentication. The cap is `12 * OL_MAX_DOCUMENT_CHARS + 64 KiB` (about
784 KB by default) for `POST /api/v1/ingest/text` only and 64 KiB for every other
route, which bounds what an unauthenticated client can make the server buffer.
There is no rate limiting, as elsewhere in this single-user API.

The note text is held in the `jobs` row (`payload_*`) so a worker restart or a
retry can finish it, and is cleared in the transaction that completes the job.
**A failed note job keeps its text in the database** so it can be retried, but only
for a bounded time: `OL_NOTE_FAILED_PAYLOAD_HOURS` (default 168, measured from job
creation) and at most the newest `OL_NOTE_FAILED_PAYLOADS` (default 20, `0` keeps
none) failed jobs retain text. Older text is cleared with the error `Note text
expired; submit it again`. Queued, running and URL jobs are never touched by this
purge. The purge runs only when the worker is active (a job fails or it is idle) or
when a note is submitted, so text can outlive the limit ("at least" that long) while
neither the worker nor ingest runs. Completed note text is stored as a document
like any imported article, so backups contain it. Notes are deduplicated by the
SHA-256 of their normalized text. The internal `note:<hash>` source key is never
returned to clients, which receive `source_kind: "note"`, an empty `source_url`
and the label. Workers log only the job id and exception type.

Queue caps: `OL_NOTE_MAX_PENDING_JOBS` (default 20, 1 to 1000) bounds queued plus
running note jobs. Beyond it, `POST /api/v1/ingest/text` and note job retries answer
429 with `Retry-After: 30` and store nothing; on PostgreSQL admission is serialised
with a transaction advisory lock so concurrent requests cannot overshoot. SQLite has
no such lock, so the cap can overshoot slightly under concurrent submissions.

Forgetting notes: `GET /api/v1/notes` lists notes (title, label, document id and
`char_count`, the number of characters (not bytes) in the stored text; never the text or
the internal key). `char_count` is computed with `length(documents.text)`, which reads
the whole stored text of every listed row (on PostgreSQL this detoasts it). Estimated at
1 byte per character that is up to about 6 MB per default 100-row page and about 30 MB
at the 500-row maximum at the default document limit; with multibyte text it can be up
to about 4 times that, so keep pages small. There is no stored length column. Notes are
listed newest first, with `limit` (1 to 500, default 100) and `offset` (default 0).
`GET /api/v1/jobs?kind=note` (same `limit`/`offset`) lists note jobs as `id`,
`status`, `created_at` and `document_id` only, never text, title, label or error, so a
failed near-duplicate job can be found and removed. `document_id` is null until a job
completes (and for jobs completed before migration 0004); `GET /api/v1/jobs/{id}` and
the retry response carry the same optional field. A job that completes by deduplication
links to the already-imported document. `DELETE /api/v1/notes/{document_id}`
hard-deletes the note document, its chunks, insights, saved items, interactions, tutor history,
review items and
review history, and its source, in one transaction. Queued, running or failed note
jobs still holding the same text are cancelled and their text cleared; cancelling a
running job closes the race in which its worker would recreate the note. Completed note
jobs whose `document_id` points at the note are deleted with it; completed jobs without a
link (created before migration 0004) are left alone.
Race with duplicate submissions: forget holds the document lock for its whole transaction.
Note jobs that already exist are cancelled by its content-hash pass; a duplicate submitted
after that pass can only complete by linking the document through the worker's dedupe path,
which share-locks the document first, so it waits for forget's commit, finds the document
gone and imports the text afresh instead of linking a deleted note.
`DELETE /api/v1/jobs/{job_id}` removes a note job of any status (409 for URL jobs). Importing the
same text again afterwards creates new rows.
Limits of forgetting: it deletes live data only. Database backups and snapshots
still contain the note until they are rotated. PostgreSQL leaves dead tuples until
vacuum and may keep the text in WAL; SQLite leaves free pages in the file until
`VACUUM`. Another Android install's Room cache may still hold cards from the note until it
refreshes or the app data is cleared; forgetting from the app itself purges that device's cards (see above), and a refresh drops cards that are neither saved nor in the feed. Stored daily feeds (all past `daily_feeds`
rows, not only today's) may keep stale insight ids, which are random UUIDs and are
filtered out when read.

Forgetting cannot reach copies outside this database: the model provider (a local
endpoint, or OpenRouter and its upstream providers) may have received the text; text
already sent by a running job cannot be recalled; surviving `cloud_attempt` rows keep
`request_bytes` and `generation_id`; cancelled and completed `jobs` rows keep
timestamps and the `Note deleted` error; PostgreSQL statement logs and replicas may
contain the text.

Editing notes: `PATCH /api/v1/notes/{document_id}` takes `{"title"?, "label"?}` (unknown
fields and an empty body are 422) and returns the note. Both fields use the ingest
validators, including the control-character and bidi rules and fixed error messages that
never echo input; a null or blank label clears it, a null title is rejected. It changes
metadata only: no model call, no new version, the text and insights are untouched. Unknown
field names are reported in the 422 `loc` (pydantic `extra_forbidden`), the same as ingest,
so a field name you send is echoed back there but never a value. Unknown
ids and URL documents answer 404 `Note not found`. It locks the document row first and
then its source; forgetting locks the document, then note jobs by id, then insights and
review items, and deletes the source last (full lock order in the `open_learning/forget.py`
docstring), so a concurrent forget gives 404 or 204, never a 500. This partly fixes the old limitation that resubmitting the same text
keeps the first title: the title and label can now be changed after import, but
resubmitting identical text still deduplicates to the first import and ignores the new
title and label.

Note versions (E2b-4, see architecture.md): `GET /api/v1/notes/{id}/text` is the one
endpoint that returns stored note text, on purpose, for any version of a note (current or
superseded) and never for article documents. It sends `Cache-Control: no-store`, including
on its 404. Editing text creates a new version; the replaced version stays stored but is
hidden from the feed, library, search, due reviews and the default notes list, and is NOT
made visible again when the newer version is forgotten. `DELETE /api/v1/notes/{id}` forgets
every version of the note (lineage id `documents.note_id`), including unfinished edit jobs and
their text; `?version_only=true` forgets one version. An edit request holds the version it
replaces locked until its job is committed, so a concurrent forget either answers first (the
edit gets 404) or cancels the new job and clears its text.
The tutor answers 404 for a superseded insight before any model call, and again (without
writes) if an edit publishes a newer version while the model runs. Save, feedback and review
answers on a superseded insight answer 404, and `GET /api/v1/history` omits its tutor messages. An edit or a plain
import whose text matches an existing version fails with a fixed message that never echoes
the text; like other failed note jobs it keeps its text until the bounded retention purge. An
edit that fails because its version was superseded or forgotten (a retry cannot succeed) drops
its title, text and label at once.

`GET /api/v1/limits` (authenticated) returns `max_document_chars` (`OL_MAX_DOCUMENT_CHARS`),
`min_text_chars`, `max_title_chars` and `max_label_chars`, the same values ingest
validates against. All new response fields are additive and optional.

Android handling of those limits (E2b-2): the app never trusts the server's numbers unbounded. Clamp ceilings:
`max_document_chars` and `min_text_chars` at 200,000 characters, `max_title_chars` and `max_label_chars` at 2,000
(the backend defaults are 500 and 100, `MAX_TITLE_CHARS`/`MAX_LABEL_CHARS` in `notes.py`, so 2,000 is only a safety
ceiling, not a mismatch). A missing, non-positive or contradictory value falls back to the app default. A shared
text may exceed the note limit by 10,000 characters (so the editor can show it as over-limit) but never 256,000
characters, which is the hard cap. The share is judged only after the `/limits` fetch finished (loaded or failed), so
the defaults cannot reject a share the real limit allows. A "too long" rejection is shown once and is dropped if the
activity is recreated before it is shown. `FLAG_SECURE` on the editor and the notes list is reference-counted per
window (`SecureWindow`): the first screen sets it and it is cleared only when the last one leaves.

POLICY REVERSAL (E2b-3). Through E2b-2 the Android app cleared nothing locally unless the server confirmed the
delete. That rule is gone. Forgetting a note on Android now means: HIDDEN ON THIS DEVICE IMMEDIATELY, DELETED ON THE SERVER
LATER, when a connection is available. Until the server confirms, the server copy still exists. A device that never
reconnects (lost, wiped before it reconnects, or offline for good) leaves the server copy in place. There is no undo
and no cancel: once confirmed in the dialog, the note is hidden and the delete is queued.

How it works: forgetting writes a `PendingForget` row in one Room transaction together with the local purge (cached
cards of that document, the linked import rows, and any queued, running or failed import job linked to it, which is
cancelled and its text deleted), then schedules a unique worker `forget-<scope>-<documentId>` (network required, 30 s
exponential backoff). The row holds only the scope, the document id, a time (of the last move to pending or confirmed), a status
(`pending`, `failed` or `confirmed`) and a fixed error string; never a title, text or server response body. Feed, library, search, reviews and the notes
list are filtered against these rows at read time from Room, so a process death cannot bring a hidden note back.
Replay: 2xx, 404 or 410 turn the row into a durable `confirmed` tombstone and repeat the local purge (see "Durable tombstone" below); 401 or 403 marks the row `failed` and it stays hidden
(the notes list shows "Could not delete (the server rejected the request)" with a Retry button, which sets it back to
pending); 429, 5xx and no network retry. The worker only runs for the connected account: while another account is
active the row stays pending and is scheduled again when its account connects. The `purgeInactiveScopes` path and every
other purge never delete `PendingForget` rows, like unsent note text; `forgetsOutside` (used for the notice) skips confirmed rows. A local cleanup that throws after the server
confirmed is repeated by retrying the whole job (a repeated DELETE answers 404), at most `FORGET_MAX_ATTEMPTS` times, after which the row is marked failed.
A 404 on the DELETE counts as "already gone" only after the notes list answered, was read completely and does not list the note;
otherwise the job retries like any other transient failure.

Limitation, token rotation: the queue and the hiding are keyed by the account scope, a hash of server and token. A note forgotten
under one token or server is hidden only while that scope is connected. After connecting with another token or server the note is
visible again on this device and has not yet been deleted on the server; its row waits until the original scope connects again, and
the app shows a notice that says so. There is no migration of queued forgets between scopes, so rotating the token before the
server DELETE ran can leave the server copy in place.
The old in-memory retry of failed local cleanups is removed: the queue supersedes it. Unlinked
(pre-migration) import rows are matched by the note's own title (the document title, not the idea headline); with no
match and several unlinked rows none are deleted, so a title residue can remain rather than other notes' rows going.

Durable tombstone and single writer (structural fix). There is no in-memory marker any more. A confirmed row stays in
`PendingForget` with status `confirmed`, and the same table hides the note from every read, pending, failed or confirmed.
`LearningRepository.writeCards` is the only code that writes `CachedInsight` (a JVM text scan fails if a DAO method that writes
`CachedInsight` is called through `dao`, `dao()` or `dao::` anywhere else in the app sources; it is a source scan, not a runtime
guarantee, and aliasing or reflection would evade it). It reads only the (id, inFeed) projection of the existing rows, not
every payload. In ONE Room transaction it reads the durable set, clears the list being replaced, puts the allowed
rows and prunes, so a feed, library, save or detail response that was in flight when a note was forgotten (or after the server
confirmed, or after a process restart) cannot write its cards back, however late it lands. Nothing waits on the network inside
that transaction. The screen state is a derived view: the published state combined with the live hidden set, with hidden
documents removed from the feed, library, results, open detail, chat and its citations, review list, revealed source and notes
list and Recent imports (completed import rows by document id) on every emission. A stale publication is scrubbed whenever it lands; the explicit checks that remain are the restore
decision for a saved tutor selection and the card writer. The hidden set also PURGES the retained state: whenever it changes,
the same removal is applied to the stored state, so when a confirmed row is later pruned nothing forgotten is still held in memory
to reappear. The view and the purge use only the hidden set of the account that owns the state; during an account switch the
view is blank until that account's set arrives. The ingest worker deletes, instead of saving, a completed import row whose
document is hidden, so a delayed import cannot show a forgotten title.
Single publication path: EVERY write to the retained screen state goes through `ScreenPublisher`, which purges the result with a
hidden set inside the same atomic update before it is stored. Only Room-flow collectors (`publishFlow`) may use the collector's
snapshot, and only when it belongs to the state's account (a snapshot of another account is unknown, so Room is read). Every user,
network or restore-origin publication (`publishFresh`: select, restore, search, reviews, reveal, notes refresh, chat reply) reads
the hidden ids from the DAO right then, and chat reads the selected note through that check before sending. `publishLocal` is for
changes that add no document content. A publication is applied once; it is dropped when the state's account is no longer the one
the response belongs to (a connection switch during the read); a hidden set arriving mid-publication is applied by purging, never
by re-running the transform. When any note is newly hidden the whole tutor chat is cleared and the tutor memory dropped, and a late
reply citing a hidden document is dropped with its question (this replaces the earlier accepted tutor-prose residual). Source scans
(TEXT scans, not runtime proofs; aliasing and reflection are not caught) cover direct writes to any `MutableStateFlow<ScreenState>`,
use of the removed `publish`, and content fields in `publishLocal`. This is best-effort against the interleavings known so far,
found by seven adversarial review passes, not a guarantee. It is still NOT device-verified.
Two-sided hidden protocol (sixth pass): a publication that read the hidden set before a forget committed could store the forgotten
note before any Room-flow collector caught up. Forget side: `ForgetQueue.forgetNoteOffline` (and `confirmed`) calls `onHidden` right
after its transaction commits and before returning, which is `ScreenPublisher.purgeNow(scope, ids)`: an atomic, idempotent purge of
the retained state that also records the ids in a purge floor (ids age out as described in the release rules below). Read side: every `publishFresh`/`publishFreshWith` store also purges with the floor, and AFTER the store
re-reads the DAO hidden ids and applies the idempotent purge only (never the transform again). Ordering argument, C = forget commit,
P = its purge (after C), S = the atomic store, R = the publication's re-read (after S): S before P is removed by P; S after P is
removed by the floor purge inside S; C before R makes R purge. So for every order of {first read, C, S} the id is gone once P and R
ran. The derived view scrubs with the Room set united with the floor. The tutor chat generation is bumped only by
`ChatSession.reset()`, which also clears the chat and the busy flag, so a double bump (collector, then forget completion) cannot
leave the chat busy. Still best-effort and not device-verified; the `ScreenPublisher`, `ViewModel` and Room-flow wiring is untested at runtime.
Floor release rules (seventh pass, one P2: forget commit, resumed worker marks the row failed, Room delivers the id before `onHidden`,
`purgeNow` adds it at age 0, then "Stop trying" deletes the row and one arrival lacking the id only aged it to 1, so the floor hid a
note the person wanted back, indefinitely). An id now leaves the floor on the first of: a Room arrival holding it; an explicit release
(`ForgetQueue.onReleased`, wired to `ScreenPublisher.release`, called after EVERY delete of PendingForget rows: `stopTrying` and the
TTL prune in `reconcile`; a text scan test checks this); two arrivals lacking it; or a 10-minute time bound (`PURGE_FLOOR_TTL_MS`,
injected clock) so a missed release cannot hide a note forever. Releasing is safe because a pending, failed or confirmed row is also in
the Room set, which hides the note independently of the floor. Best-effort and NOT device-verified.
Ingest update is transactional: the worker reads the hidden ids and writes (or deletes) the job row in ONE Room transaction
(`updateJobUnlessHidden`), so a forget that commits between a response naming the document and the update is still seen. A
processing row that has no document id yet cannot be matched by `forgetNoteOffline` (titles are never stored or matched); the
residual is that such a row stays until its worker answers, at which point the transactional update deletes it. A worker that
never answers leaves the unlinked row, which holds only what the person typed or imported.
Confirmation is one transaction too: the row is upserted as `confirmed` (even if it went missing or failed) and the cards and
linked completed import rows are purged. Stop trying deletes the row (the person's override).
Pruning, TTL 7 days: each `GET /notes` answer settles confirmed rows (`reconcile`). A confirmed row older than 7 days, measured
from the START of the list request (so a row confirmed while it was in flight is never judged by it), is deleted only when the
page was complete (fewer than 100 notes) and does not list it. If the server still lists it after 7 days it goes back to
pending and its worker is enqueued again (the DELETE is idempotent). Anything else is kept, including every row after an
incomplete page. Hidden rows therefore persist until a list answer reconciles them. With more than 100 notes (`NOTES_PAGE_LIMIT`) the page is
never complete, so pruning never runs: confirmed rows then persist, which is safe (they only hide) but unbounded. A periodic unique work `forget-sweep`
(every 6 hours, network required, KEEP) resumes pending rows and reconciles when a confirmed row is past its TTL.
Document ids are validated before anything is committed (at most 64 characters of hex and dashes, what the server issues);
anything else is refused with a local message and never queued, because an oversized id would make WorkManager throw on every
launch. Scheduling is per row and exception safe, and it waits for WorkManager to accept the work: a request that cannot be
built (a bad id) is permanent and the row is marked failed ("this device could not schedule it"), an enqueue operation that
fails is transient and the row stays pending for the next resume (only a bad id ever marks a row failed). Work is enqueued
with REPLACE for a new forget and for Retry and KEEP for the idempotent resume, which runs on every connect, every sweep and
whenever Your notes opens. REPLACE is safe because the worker re-reads its row at start and exits cleanly when it is not
pending, and the DELETE is idempotent. The worker never reports a failed result for a row outcome (a rejected row is marked
failed and the work succeeds), so no failed work poisons a chain. The confirmation cleanup removes only rows linked to the
document, never the title or legacy fallback. After the commit nothing can undo the forget: a failure to cancel an import or
to schedule is recovered by the resume, not reported as "nothing was hidden"; the resume cannot crash the app.
Reviews: a review carries its own `document_id` (additive on the server; an older server sends nothing, which reads as blank).
A review with a blank id FAILS CLOSED always, whether or not a forget exists yet (so a later forget can still scrub it): its document is looked up (cache, then the
server, at most 20 lookups, none after the first network failure) and a review that cannot be resolved is dropped (a blank id from the payload, the cache or the network counts as unresolved: a blank cached value falls through to the network and no blank mapping is ever stored); a 404 on one
lookup drops only that review. The derived view removes reviews by their own id, the recorded id and the revealed source.
Failing statuses: 2xx, 404 and 410 are done; 408, 425, 429, 5xx and no answer retry, giving up after 20 attempts (the row is
failed with "gave up", still hidden); 401 and 403 fail as "rejected by the server"; every other status (3xx, other 4xx) fails
as "server refused". Failed rows offer Retry and "Stop trying", which deletes the queue row and shows the note again on the
next refresh; the note stays on the server. Error text is one of four fixed strings, never a title or server text.
Notice: when pending or failed rows belong to a scope other than the connected one (another server, or another token), a notice
says "Notes you forgot while connected to another server or token are visible again on this device and are not yet deleted on the server." once per app start. It
names no server; the stored previous-server key of earlier builds is deleted. Reconnecting with that account finishes them;
there is no other way to delete them from this device.
Residuals: an import of the same text that is still in flight on the server can republish the note after a forget (the confirm
dialog says "A note that is still being imported may reappear."). Reviews from older servers depend on
the lookup, so offline they are hidden. Untested wiring (no device or Robolectric run): the ViewModel collectors, the worker's
use of the hidden set and the Room helpers are compile-checked only; the pure functions behind them are unit-tested. Hidden rows persist until a list reconcile. A forgotten note cannot be un-hidden except
through Stop trying on a failed row.

The lock-order guarantees between forget and the worker's dedupe path are PostgreSQL-only.
On SQLite with the API and the worker as separate processes, a forget can commit between
the worker's read of the document and its job update and leave a dangling `document_id`
(pre-existing behaviour; the single-process default is unaffected).

Migration 0004 was amended before release and never shipped in its earlier form, so there
is no schema drift for released databases.

Downgrading migration 0004: it only drops `jobs.document_id` and
`jobs.replaces_document_id` (the latter is reserved and unused); notes and jobs survive,
but job-to-document links are lost, so a later upgrade starts with null links and
forgetting will not delete the old completed jobs. On PostgreSQL it takes the same
`SHARE ROW EXCLUSIVE` lock on `jobs` as 0003.

Downgrading migration 0003: stop the API and the worker first. The downgrade
refuses to run while note jobs are queued or running (on PostgreSQL it takes a
`SHARE ROW EXCLUSIVE` lock on `jobs` before checking, so no job can slip in). It
permanently discards the text, title and label held by failed note jobs, so a retry
returns 409 afterwards. Documents and sources of notes remain, and their
`note:<sha256>` source keys become visible to older code as `source_url`.

Known limitations (notes): each accepted note write stores up to about
`OL_MAX_DOCUMENT_CHARS` characters in the `jobs` row, bounded by the pending-job cap
and the failed-text retention above. Concurrent identical notes can each call the model
once before the first import lands; model spend is bounded by the existing
spending ledger.

Android note text (E2a): a note you write or import lives only in the app's Room
database on the device, in two places: the editor draft (`NoteDraft`) and, once you
add it, the queued job row (`PendingIngest.text`). The text is never put in
WorkManager input data, logs, `toString()` output (all types that carry it redact it),
saved instance state, or uiautomator evidence kept in the repository. The job row's text is
cleared when the job completes or is cancelled, and the draft is deleted on submit. While the
editor is on screen the window is `FLAG_SECURE` (blank recents thumbnail, no screenshots), the
editor is excluded from autofill (see the corrected mechanism below), and the keyboard is asked not to learn from the fields
(`IME_FLAG_NO_PERSONALIZED_LEARNING`, autocorrect off); a keyboard may ignore that request.

Autofill opt-out mechanism (corrected): `LocalView.current` is the AndroidComposeView, not the `DialogLayout`
(inside a Dialog the `DialogLayout` is its parent). AndroidComposeView overrides `getImportantForAutofill()` and
returns `IMPORTANT_FOR_AUTOFILL_YES`, so flagging it has no effect. `View.isImportantForAutofill()` instead walks
the ancestors and stops at one marked `NO_EXCLUDE_DESCENDANTS`. `ExcludeFromAutofill()` therefore flags
ANCESTORS only, never the compose view: in a dialog the `DialogLayout` (the `DialogWindowProvider`) and its
window's decor view; in the activity (note editor) only the nearest ancestor. Previous values are restored on
dispose (reverse order). `autofillTargetsFor` throws a fixed-message `IllegalStateException` when no ancestor exists (asserted by a JVM test);
the composables catch it, `Log.w` a fixed string (no view or text content), fall back to the root view if it differs
from the compose view, otherwise skip. They never crash. The Connect dialog also calls `AutofillManager.cancel()` on dismissal (best effort) to
drop any pending, unsaved autofill context. The original note-editor opt-out shipped in E2a flagged the
AndroidComposeView itself and was ineffective; it is corrected by this change. The server URL and token fields
also ask the keyboard not to learn (`IME_FLAG_NO_PERSONALIZED_LEARNING`, autocorrect off), and the token stays
masked. The pinned Compose version has no `ContentType.None`, so no per-field `contentType` is set. Trade-off:
the token cannot be filled from a password manager.
While the note editor is open the activity's whole ComposeView (its nearest ancestor) is excluded from autofill; the
previous value is restored on dispose.

Residual virtual-view caveat: the `AndroidAutofillManager` `notifyViewEntered` path has no `isImportantForAutofill`
check; the ancestor flag suppresses the contents of the assist structure. The effect on Bitwarden is unverified, and
accessibility-based managers are unaffected.

Limits: this is an opt-out request, not enforcement. `importantForAutofill` is honoured by framework-autofill
services only; accessibility-based managers (some third-party password managers) read the view tree through
accessibility and ignore it. It affects autofill requests only. Whether the save sheet is suppressed on a real
device has not been observed.

Disk hygiene: Room runs with `PRAGMA secure_delete=ON`, which overwrites freed pages with zeros
on a best-effort basis. After clearing note text the app runs `wal_checkpoint(TRUNCATE)` and reads
its result: the call shortens the write-ahead log to zero bytes, it does not overwrite the old
bytes, and it can report "busy" while another reader is open (the app then logs a fixed message
and carries on, so a stale WAL can remain until the next checkpoint). This is not flash erase:
wear levelling and filesystem journals can keep old blocks, and full-device encryption is the real
protection.

Backups: `allowBackup="false"` is set and `android:dataExtractionRules` excludes the `database` and
`sharedpref` domains from both cloud backup and device-to-device transfer (Android 12+ ignores
`allowBackup` for transfers, which is why the rules exist). The attribute is present in the merged
manifest and a JVM test parses the rules, but no real backup or device transfer has been run.

Local retention: a failed or waiting note keeps its text so you can retry; Remove on the job row
deletes it (and the server job, unless another local row shares it). Completed notes have no Remove:
forgetting the note is the way. Forgetting hides the note on this device at once and queues the server delete
(see the policy reversal above); it does not need a connection to start. It deletes every cached card of that document (scanning
all rows, not only the feed or library) and the completed note entries in Recent imports that are linked
to that document (the app stores the `document_id` the server links to the job when it completes).
Entries made before the link existed (Room schema v3) cannot be matched: only when no entry is linked to
the forgotten document are the completed entries without a link removed, so no title outlives the note.
That fallback can remove an entry of another pre-migration note; entries linked to other documents are
never touched. After each refresh, cached cards that
are neither saved nor in the feed are deleted (offline detail for search or review items is lost;
they are fetched again online). When a different server or token is connected, the previous
connection's cached cards and its completed import rows are deleted at once. Unsent note text (queued,
failed, waiting, paused or running notes) and note drafts are never deleted by that purge, so a
mistyped token cannot destroy a note: those rows stay on the device, hidden and not sent, and
reconnecting the original account resumes them. Remove (or forgetting) is the way to clear them. The
cards are purged immediately, not after a first successful call on the new connection. 
An in-flight submission cancelled at the wrong moment can still leave a note on the
server whose response never arrived; the server deduplicates by hash and Forget removes it.
Open risks: the app fetches `GET /api/v1/limits` once per connection, best-effort (a failure or a
missing or contradictory field falls back to 60,000 / 100 / 100 / 100 characters), so a server that lowers
its limit mid-session still answers 422 (a 422 keeps the
text so you can Remove it); and resubmitting the same text under a new title keeps the
first title because the server deduplicates by content hash.

Normal API access logs are disabled in deployment because URLs and query strings
can expose reading interests. Do not enable model prompt logging on shared
machines. Container/database administrators can read the entire library. Use
encrypted storage and encrypted backup destinations where appropriate. Do not
commit exports, database dumps, `.env`, credentials, or reading history.

## Backup and restore

From the repository root, with the same `.env` as the running installation:

```sh
mkdir -p private-backups
chmod 700 private-backups
podman compose --env-file .env -f infrastructure/compose.yaml exec -T db \
  pg_dump -U openlearning -d openlearning -Fc > private-backups/knowledge.dump
```

Store `private-backups` outside the checkout or add it to your private ignore
rules. Back up `.env` separately in an encrypted password manager. To restore,
stop API and worker first and restore into a fresh database using the same
PostgreSQL major version:

```sh
podman compose --env-file .env -f infrastructure/compose.yaml stop api worker
podman compose --env-file .env -f infrastructure/compose.yaml exec -T db \
  pg_restore -U openlearning -d openlearning --clean --if-exists < private-backups/knowledge.dump
podman compose --env-file .env -f infrastructure/compose.yaml run --rm migrate
podman compose --env-file .env -f infrastructure/compose.yaml up -d api worker
```

Test restores periodically on an isolated instance. A dump contains private
reading content even if the original source was public. Uninstalling Android
removes its local cache; the canonical library remains in PostgreSQL.

Notes list (E2b-2): "Your notes" shows `GET /api/v1/notes` rows (title, label, character count, date).
The rows live in view-model memory only and are re-fetched on every open and on Refresh; titles and labels
are never written to Room, and the list is empty after process death. `FLAG_SECURE` is set on the window
while the list is shown (recents thumbnail and screenshots are blank) and the previous flag state is restored
when it closes; the list has no text inputs, so no autofill handling applies. Forget from the list asks
for confirmation (the dialog says the note is hidden here now, deleted from the server when a connection is
available, and that there is no undo), then hides the note and queues the delete (see the policy reversal above for what
the queue stores and replays; see above for which import rows go). The list shows one line, "N forgotten notes waiting for
the server" (hidden at 0), and one row per rejected delete with Retry; none of these rows carry a title. Share-as-note: shared text that is not a link (an http(s) URL with at most 300
other characters) opens the note editor pre-filled, never submits, shows an over-limit share as over-limit
instead of truncating it, and asks before replacing text already typed; links still fill the import field.
The title is the first non-blank line cut to 80 code points. Retry-After: the server answers 429 with
`Retry-After: 30`; note ingestion's WorkManager backoff starts at 30 s (exponential), and the worker never
reschedules itself. E2b-2 is not device-verified (see verification).
