package org.openlearning.app

/** Fixed strings only: a server response body never reaches Room or the screen. */
const val FORGET_REJECTED_ERROR = "Could not delete (the server rejected the request)"
const val FORGET_REFUSED_ERROR = "Could not delete (server refused the request)"
const val FORGET_GAVE_UP_ERROR = "Could not delete (gave up after repeated attempts)"
const val FORGET_SCHEDULE_ERROR = "Could not delete (this device could not schedule it)"
/** The only strings a failed row may show, whatever is stored. */
val FORGET_FIXED_ERRORS = setOf(FORGET_REJECTED_ERROR, FORGET_REFUSED_ERROR, FORGET_GAVE_UP_ERROR, FORGET_SCHEDULE_ERROR)
/** Retries stop here (runAttemptCount counts from 0): the row is marked failed, still hidden, with Retry and Stop trying. */
const val FORGET_MAX_ATTEMPTS = 20

const val FORGET_PENDING = "pending"
const val FORGET_FAILED = "failed"
/** The server deleted the note. The row stays as a durable tombstone (it keeps hiding the note) until a list answer settles it. */
const val FORGET_CONFIRMED = "confirmed"
/** A confirmed row is kept this long (measured from the start of the list request) before a list answer may delete it. */
const val FORGET_CONFIRMED_TTL_MS = 7L * 24 * 60 * 60 * 1000

/** Matches note ingest: the server answers 429 with Retry-After: 30. */
const val FORGET_BACKOFF_SECONDS = NOTE_BACKOFF_SECONDS

fun forgetWorkName(scope: String, documentId: String) = "forget-$scope-$documentId"

/** What a DELETE /notes/{id} answer means for the queued row. */
sealed interface ForgetReplay {
    /** Deleted, or the server already had nothing to delete. */
    data object Done : ForgetReplay
    /** Permanent: the row stays hidden with [error] until the person retries. */
    data class Failed(val error: String) : ForgetReplay
    data object Retry : ForgetReplay
    /** 404: a wrong route, proxy or server also answers 404, so it is Done only after [forgetVerify404] checked the notes list. */
    data object Verify : ForgetReplay
}

/**
 * [code] is null when no answer arrived (IOException, no network). [attempt] is WorkManager's runAttemptCount: a retry
 * on the last allowed attempt becomes a permanent "gave up". Answers: 2xx and 410 done; 404 is verified first (see [forgetVerify404]); 401 and 403 rejected;
 * 408, 425, 429, 5xx and no answer retry; every other status (3xx, other 4xx) is refused.
 */
fun forgetReplay(code: Int?, attempt: Int = 0): ForgetReplay {
    val replay = when {
        code == null -> ForgetReplay.Retry
        code in 200..299 || code == 410 -> ForgetReplay.Done
        code == 404 -> ForgetReplay.Verify
        code == 401 || code == 403 -> ForgetReplay.Failed(FORGET_REJECTED_ERROR)
        code == 408 || code == 425 || code == 429 || code >= 500 -> ForgetReplay.Retry
        else -> ForgetReplay.Failed(FORGET_REFUSED_ERROR)
    }
    return if (replay == ForgetReplay.Retry && attempt + 1 >= FORGET_MAX_ATTEMPTS) ForgetReplay.Failed(FORGET_GAVE_UP_ERROR) else replay
}

/**
 * A 404 on the DELETE counts as "already gone" only when the notes list answered, is complete (all pages read) and does not
 * list the note. [serverIds] is null when the list call failed. Anything else is unverified: retry, capped like any failure.
 */
fun forgetVerify404(serverIds: Set<String>?, complete: Boolean, documentId: String, attempt: Int): ForgetReplay =
    if (serverIds != null && complete && serverIds.none { it.equals(documentId, ignoreCase = true) }) ForgetReplay.Done
    else forgetReplay(null, attempt)

/** Server confirmed but the local cleanup threw: retried like any other failure, and capped by the same attempt limit. */
fun forgetCleanupFailed(attempt: Int): ForgetReplay = forgetReplay(null, attempt)

/** Every row of the scope hides its note: pending, failed or confirmed. */
fun hiddenIds(rows: List<PendingForget>, scope: String): Set<String> = rows.filter { it.scope == scope }.mapTo(HashSet()) { it.documentId.lowercase() }

/** [hidden] holds lower-case ids (see [hiddenIds]); the candidate is lowered before the lookup. */
fun Set<String>.hides(documentId: String) = documentId.lowercase() in this

/** The read-time filter shared by feed, library, search, review and the notes list. Blank ids are never hidden. */
fun <T> List<T>.hideForgotten(hidden: Set<String>, documentId: (T) -> String?): List<T> =
    if (hidden.isEmpty()) this else filterNot { documentId(it)?.let { id -> id.isNotBlank() && hidden.hides(id) } == true }

/** Queued, running or failed note imports already linked to the document: forgetting must stop and drop them. */
fun activeRowsToCancel(linkedNoteRows: List<PendingIngest>, documentId: String): List<PendingIngest> =
    linkedNoteRows.filter { it.documentId.equals(documentId, ignoreCase = true) && it.isNoteJob() && !isCompletedState(it.status) }

enum class ForgetStart { RUN, LEAVE_PENDING }

/** Mirrors IngestWorker: only the connected account's credentials may be used; any other scope waits for its account. */
fun forgetStart(activeScope: String?, rowScope: String) = if (activeScope == rowScope) ForgetStart.RUN else ForgetStart.LEAVE_PENDING
