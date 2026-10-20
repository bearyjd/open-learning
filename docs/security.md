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
purge, which runs when a job fails, when the worker is idle, and on each note
submission. Completed note text is stored as a document
like any imported article, so backups contain it. Notes are deduplicated by the
SHA-256 of their normalized text. The internal `note:<hash>` source key is never
returned to clients, which receive `source_kind: "note"`, an empty `source_url`
and the label. Workers log only the job id and exception type.

Queue caps: `OL_NOTE_MAX_PENDING_JOBS` (default 20, 1 to 1000) bounds queued plus
running note jobs. Beyond it, `POST /api/v1/ingest/text` and note job retries answer
429 with `Retry-After: 30` and store nothing; on PostgreSQL admission is serialised
with a transaction advisory lock so concurrent requests cannot overshoot.

Forgetting notes: `GET /api/v1/notes` lists notes (title, label, document id, never the
internal key). `DELETE /api/v1/notes/{document_id}` hard-deletes the note document,
its chunks, insights, saved items, interactions, tutor history, review items and
review history, and its source, in one transaction. Queued or failed note jobs still
holding the same text are cancelled and their text cleared, so a running job cannot
recreate the note. `DELETE /api/v1/jobs/{job_id}` removes a note job of any status
(409 for URL jobs). Importing the same text again afterwards creates new rows.
Limits of forgetting: it deletes live data only. Database backups and snapshots
still contain the note until they are rotated. PostgreSQL leaves dead tuples until
vacuum and may keep the text in WAL; SQLite leaves free pages in the file until
`VACUUM`. The Android app's Room cache may still hold cards from the note until it
refreshes or the app data is cleared. Today's stored feed may keep stale ids, which
are filtered out when read.

Downgrading migration 0003: stop the API and the worker first. The downgrade
refuses to run while note jobs are queued or running (on PostgreSQL it takes a
`SHARE ROW EXCLUSIVE` lock on `jobs` before checking, so no job can slip in). It
permanently discards the text, title and label held by failed note jobs, so a retry
returns 409 afterwards. Documents and sources of notes remain, and their
`note:<sha256>` source keys become visible to older code as `source_url`.

Known limitations (notes): each accepted note write stores up to about
`OL_MAX_DOCUMENT_CHARS` characters in the `jobs` row; there is no queue cap or
purge of old failed jobs yet. Concurrent identical notes can each call the model
once before the first import lands; model spend is bounded by the existing
spending ledger.

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
