package org.openlearning.app

import androidx.work.ListenableWorker
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withTimeoutOrNull

val COMPLETED_STATES = listOf("completed", "complete", "succeeded", "success", "done")
fun isCompletedState(status: String) = status.lowercase() in COMPLETED_STATES

/** A completed note has nothing left to cancel: forgetting its ideas is the way to remove it. */
fun PendingIngest.canRemove() = isNoteJob() && !isCompletedState(status)

/** After a scope change only completed rows holding no text go; a row with unsent text is kept for its own account. */
fun PendingIngest.purgeableAfterScopeChange() = text == null && isCompletedState(status)

/**
 * The completed note rows to delete when forgetting [documentId] (whose title was [title]): those linked to it, plus
 * unlinked rows (made before schema v3, so they cannot be matched by id):
 * - once any completed row anywhere carries a document id, unlinked rows go only when their title equals [title];
 * - in a purely pre-migration state (no row has an id) the unlinked rows whose title matches go, and when none match
 *   every unlinked row goes, so no title outlives the note.
 * Rows linked to other documents are never touched. Residual: an unlinked row with the same title as the forgotten
 * note is removed even if it belonged to a different note, and in the pure pre-migration state with no title match
 * other notes' unlinked rows are removed too. Those rows are only completed history; the server keeps the notes.
 */
fun noteRowsToPurge(completedNoteRows: List<PendingIngest>, documentId: String, title: String? = null): List<PendingIngest> {
    val linked = completedNoteRows.filter { it.documentId == documentId }
    val unlinked = completedNoteRows.filter { it.documentId == null }
    val wanted = title?.let(::normalizedNoteTitle)
    val titled = if (wanted == null) emptyList() else unlinked.filter { normalizedNoteTitle(it.title) == wanted }
    val pureLegacy = completedNoteRows.none { it.documentId != null }
    return linked + if (pureLegacy && titled.isEmpty()) unlinked else titled
}

/** The list shows a blank title as "Untitled", so rows compare the same way. */
private fun normalizedNoteTitle(title: String?) = title.orEmpty().trim().ifBlank { "Untitled" }

/** The server answers 429 with Retry-After: 30, so note retries never start sooner than this. */
const val NOTE_BACKOFF_SECONDS = 30L
const val URL_BACKOFF_SECONDS = 10L
fun backoffSeconds(kind: String) = if (kind == "note") NOTE_BACKOFF_SECONDS else URL_BACKOFF_SECONDS

/** A purge belongs to the connection that started it; a later switch (A to B to A) makes it stale. */
fun shouldRunPurge(activeScope: String?, purgeScope: String) = activeScope == purgeScope

/** 404 and 409 on an existing server job both mean the server no longer has it. */
fun isJobGone(code: Int?) = code == 404 || code == 409

/** Resubmit from local text only when the failed call was on a server job id and the text still exists. */
fun shouldResubmit(row: PendingIngest, code: Int?) = isJobGone(code) && row.jobId != null && row.text != null

enum class WorkOutcome { SUCCESS, FAILURE, RETRY }

fun WorkOutcome.toResult(): ListenableWorker.Result = when (this) {
    WorkOutcome.SUCCESS -> ListenableWorker.Result.success()
    WorkOutcome.FAILURE -> ListenableWorker.Result.failure()
    WorkOutcome.RETRY -> ListenableWorker.Result.retry()
}

/** What the worker writes next and how it ends. [refresh] asks for a feed refresh once the row is saved. */
data class NoteUpdate(val row: PendingIngest, val outcome: WorkOutcome, val refresh: Boolean = false)

/** The server answered with a job. Text is cleared as soon as the job is completed or cancelled. */
fun noteAfterResponse(row: PendingIngest, job: IngestJob): NoteUpdate {
    val state = job.status.lowercase()
    val completed = state in COMPLETED_STATES
    val finished = completed || state == "cancelled"
    val next = row.copy(jobId = job.id, status = job.status, error = job.error, text = if (finished) null else row.text,
        documentId = if (completed) job.documentId?.takeIf { it.isNotBlank() } ?: row.documentId else row.documentId)
    val outcome = when {
        completed -> WorkOutcome.SUCCESS
        state == "failed" || state == "error" || state == "cancelled" -> WorkOutcome.FAILURE
        else -> WorkOutcome.RETRY
    }
    return NoteUpdate(next, outcome, refresh = completed)
}

/** The call failed. 429 and network/5xx errors retry; any other 4xx is permanent. [message] is already a fixed string. */
fun noteAfterError(row: PendingIngest, code: Int?, message: String): NoteUpdate {
    val permanent = code != null && code in 400..499 && code != 429
    val shown = if (isJobGone(code) && row.jobId != null) NOTE_EXPIRED else message
    return NoteUpdate(row.copy(status = if (permanent) "failed" else "waiting", error = shown), if (permanent) WorkOutcome.FAILURE else WorkOutcome.RETRY)
}

enum class SaveFollowUp { CONTINUE, DROP, DROP_AND_DELETE_SERVER_JOB }

/** [rowsChanged] 0 means the user removed the row; a job this run just created would then be orphaned on the server. */
fun afterSave(rowsChanged: Int, createdServerJob: Boolean) = when {
    rowsChanged > 0 -> SaveFollowUp.CONTINUE
    createdServerJob -> SaveFollowUp.DROP_AND_DELETE_SERVER_JOB
    else -> SaveFollowUp.DROP
}

/** The server deduplicates by content hash, so another local row can hold the same job id. */
fun mayDeleteServerJob(jobId: String?, otherHolders: Int) = jobId != null && otherHolders == 0

/** True when [settled] held before [timeoutMs] ran out. */
suspend fun <T> awaitSettled(flow: Flow<T>, timeoutMs: Long, settled: (T) -> Boolean): Boolean =
    withTimeoutOrNull(timeoutMs) { flow.first(settled) } != null
