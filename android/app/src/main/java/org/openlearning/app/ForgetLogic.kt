package org.openlearning.app

/** Fixed strings only: a server response body never reaches Room or the screen. */
const val FORGET_REJECTED_ERROR = "Could not delete (the server rejected the request)"
const val FORGET_REFUSED_ERROR = "Could not delete (server refused the request)"

const val FORGET_PENDING = "pending"
const val FORGET_FAILED = "failed"

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
}

/** [code] is null when no answer arrived (IOException, no network). */
fun forgetReplay(code: Int?): ForgetReplay = when {
    code == null -> ForgetReplay.Retry
    code in 200..299 || code == 404 -> ForgetReplay.Done
    code == 401 || code == 403 -> ForgetReplay.Failed(FORGET_REJECTED_ERROR)
    code == 429 || code >= 500 -> ForgetReplay.Retry
    else -> ForgetReplay.Failed(FORGET_REFUSED_ERROR)
}

/** Every queued row of the scope hides its note, pending or failed. */
fun hiddenIds(rows: List<PendingForget>, scope: String): Set<String> = rows.filter { it.scope == scope }.mapTo(HashSet()) { it.documentId }

/** The read-time filter shared by feed, library, search, review and the notes list. Blank ids are never hidden. */
fun <T> List<T>.hideForgotten(hidden: Set<String>, documentId: (T) -> String?): List<T> =
    if (hidden.isEmpty()) this else filterNot { documentId(it)?.let { id -> id.isNotBlank() && id in hidden } == true }

/** Queued, running or failed note imports already linked to the document: forgetting must stop and drop them. */
fun activeRowsToCancel(linkedNoteRows: List<PendingIngest>, documentId: String): List<PendingIngest> =
    linkedNoteRows.filter { it.documentId == documentId && it.isNoteJob() && !isCompletedState(it.status) }

enum class ForgetStart { RUN, LEAVE_PENDING }

/** Mirrors IngestWorker: only the connected account's credentials may be used; any other scope waits for its account. */
fun forgetStart(activeScope: String?, rowScope: String) = if (activeScope == rowScope) ForgetStart.RUN else ForgetStart.LEAVE_PENDING
