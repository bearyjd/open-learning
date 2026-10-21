package org.openlearning.app

import java.text.NumberFormat
import java.util.Locale
import java.util.concurrent.ConcurrentHashMap

/** Documents the user is forgetting. Set before the server call so an in-flight refresh cannot write cards back. */
class ForgottenNotes {
    private val keys = ConcurrentHashMap.newKeySet<String>()
    private fun key(scope: String, documentId: String) = "$scope\u0000$documentId"
    fun add(scope: String, documentId: String) { if (documentId.isNotBlank()) keys.add(key(scope, documentId)) }
    fun remove(scope: String, documentId: String) { keys.remove(key(scope, documentId)) }
    fun blocks(scope: String, insight: Insight) = insight.documentId.isNotBlank() && key(scope, insight.documentId) in keys
    fun allowed(scope: String, items: List<Insight>) = items.filterNot { blocks(scope, it) }
}

/** The editable note fields. Immutable: every edit is a new value. */
data class NoteDraftFields(val title: String = "", val label: String = "", val text: String = "") {
    val isEmpty get() = title.isEmpty() && label.isEmpty() && text.isEmpty()
}

fun noteCounter(text: String): String =
    "${NumberFormat.getIntegerInstance(Locale.US).format(text.codePointCount(0, text.length))} / ${NumberFormat.getIntegerInstance(Locale.US).format(NOTE_MAX_CHARS)}"

fun PendingIngest.isNoteJob() = kind == "note"

/** What the job row shows in place of a URL. */
fun PendingIngest.displayName(): String = if (isNoteJob()) "Note: ${title.orEmpty().ifBlank { "Untitled" }}" else url

private val RETRYABLE = listOf("failed", "error", "paused", "cancelled")
/** A cancelled note has no text left and the server already dropped it, so there is nothing to retry. */
fun PendingIngest.canRetry() = status.lowercase() in RETRYABLE && !(isNoteJob() && status.lowercase() == "cancelled")

sealed interface NoteRetry {
    /** Ask the server to retry its failed job. */
    data class ServerRetry(val jobId: String) : NoteRetry
    /** Just run the worker again with the stored text. */
    data object Requeue : NoteRetry
    data object Expired : NoteRetry
}

fun noteRetryPlan(job: PendingIngest): NoteRetry = when {
    job.jobId != null && job.status.lowercase() in listOf("failed", "error") -> NoteRetry.ServerRetry(job.jobId)
    job.text == null && job.jobId == null -> NoteRetry.Expired
    else -> NoteRetry.Requeue
}

/** The server refused the retry (409): resubmit from local text as a fresh job, if the text still exists. */
fun noteConflictPlan(job: PendingIngest): PendingIngest? =
    if (job.text == null) null else job.copy(jobId = null, status = "queued", error = null)

/** Drops a forgotten document from everything the screen still holds. [insightIds] are the purged cache rows. */
fun ScreenState.withoutDocument(documentId: String, insightIds: Collection<String>): ScreenState {
    fun gone(item: Insight) = item.documentId == documentId || item.id in insightIds
    return copy(
        feed = feed.filterNot(::gone), library = library.filterNot(::gone), results = results.filterNot(::gone),
        reviews = reviews.filterNot { it.insightId in insightIds },
        reviewSource = reviewSource?.takeUnless(::gone),
        revealed = if (reviewSource != null && gone(reviewSource)) false else revealed,
        selected = null, chat = emptyList(), chatBusy = false, learning = false,
    )
}

/** [outcome] completes "so ...", e.g. "the note was not forgotten". */
fun forgetFailureMessage(error: Throwable, outcome: String = "the note was not forgotten"): String = when (error) {
    is java.io.IOException -> "Can't reach your server, so $outcome. Try again when you're online."
    else -> "The server could not complete that, so $outcome. Try again."
}
