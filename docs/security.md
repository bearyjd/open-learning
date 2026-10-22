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
the whole stored text of every listed row (on PostgreSQL this detoasts it): up to about
6 MB per default 100-row page and about 30 MB at the 500-row maximum at the default
document limit, so keep pages small. There is no stored length column, newest first, with `limit` (1 to 500, default 100) and `offset` (default
0). `GET /api/v1/jobs?kind=note` (same `limit`/`offset`) lists note jobs as `id`,
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

`GET /api/v1/limits` (authenticated) returns `max_document_chars` (`OL_MAX_DOCUMENT_CHARS`),
`min_text_chars`, `max_title_chars` and `max_label_chars`, the same values ingest
validates against. All new response fields are additive and optional.

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
forgetting the note is the way. Forgetting needs a connection to your server; nothing is cleared
locally unless the server confirms. It then deletes every cached card of that document (scanning
all rows, not only the feed or library) and, because a job has no document id to match on, every
completed note entry in Recent imports for that connection. After each refresh, cached cards that
are neither saved nor in the feed are deleted (offline detail for search or review items is lost;
they are fetched again online). When a different server or token is connected, the previous
connection's cached cards and its completed import rows are deleted at once. Unsent note text (queued,
failed, waiting, paused or running notes) and note drafts are never deleted by that purge, so a
mistyped token cannot destroy a note: those rows stay on the device, hidden and not sent, and
reconnecting the original account resumes them. Remove (or forgetting) is the way to clear them. The
cards are purged immediately, not after a first successful call on the new connection. Forgetting a
note deletes every completed note row of that connection, not only the forgotten note's (an app
limitation until E2b-2: the app does not yet read the `document_id` the server now links to
each job, so it cannot scope the deletion). An in-flight submission cancelled at the wrong moment can still leave a note on the
server whose response never arrived; the server deduplicates by hash and Forget removes it.
Open risks: the 60,000-character limit is
hardcoded in the app while the server's `OL_MAX_DOCUMENT_CHARS` decides (now advertised by `GET /api/v1/limits`; the app does not read it yet) (a 422 keeps the
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
