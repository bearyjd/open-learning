package org.openlearning.app

import java.util.concurrent.ConcurrentHashMap

/** In-memory cache of documents being forgotten, so an in-flight refresh cannot write cards back. The PendingForget table is the durable source. */
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
    override fun toString() = "NoteDraftFields(text=<redacted>)"
}

sealed interface FilePlan {
    data object Apply : FilePlan
    /** The editor already holds typed text: ask before overwriting it. */
    data object ConfirmReplace : FilePlan
}

fun filePlan(draft: NoteDraftFields): FilePlan = if (draft.text.isBlank()) FilePlan.Apply else FilePlan.ConfirmReplace

/** Fills the text from the file; a title the person already typed is kept. */
fun NoteDraftFields.withFile(file: NoteFileResult.Loaded) = copy(title = title.ifBlank { file.title }, text = file.text)

fun noteCounter(text: String, max: Int = NOTE_MAX_CHARS): String = "${formatCount(text.codePointCount(0, text.length))} / ${formatCount(max)}"

fun PendingIngest.isNoteJob() = kind == "note"

/** What the job row shows in place of a URL. */
fun PendingIngest.displayName(): String = if (isNoteJob()) "Note: ${title.orEmpty().ifBlank { "Untitled" }}" else url

/** A banner message. [jobId] ties a "queued" notice to the note job it describes; other notices have none. */
data class Notice(val text: String, val jobId: String? = null)

private val TERMINAL = setOf("completed", "complete", "succeeded", "success", "done", "failed", "error", "cancelled")
fun PendingIngest.isTerminal() = status.lowercase() in TERMINAL

/** Clears a job-bound notice once its job is finished or gone; any other notice is returned untouched. */
fun noticeAfterJobUpdate(current: Notice?, jobs: List<PendingIngest>): Notice? {
    val jobId = current?.jobId ?: return current
    val job = jobs.firstOrNull { it.id == jobId }
    return if (job == null || job.isTerminal()) null else current
}

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

/** The server no longer has the job (404/409): resubmit from local text as a fresh job, if the text still exists. */
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

/** [outcome] completes "so ...", e.g. "the job was not removed". */
fun forgetFailureMessage(error: Throwable, outcome: String): String = when (error) {
    is java.io.IOException -> "Can't reach your server, so $outcome. Try again when you're online."
    else -> "The server could not complete that, so $outcome. Try again."
}

/** How forgetting a note ended. Forgetting is offline-first: it fails only when the local write fails. */
sealed interface ForgetResult {
    data class Done(val purged: List<String>) : ForgetResult
    data class Failed(val error: Throwable) : ForgetResult
}

/** The banner text for a finished forget. A failure never echoes error text. */
fun ForgetResult.message(): String = when (this) {
    is ForgetResult.Done -> FORGET_QUEUED_MESSAGE
    is ForgetResult.Failed -> FORGET_LOCAL_FAILED_MESSAGE
}

/**
 * The title to match unlinked import rows against. [Insight.title] is the model-written headline of one idea; the
 * import row and the notes list hold the note's own title, which the server returns as the document title (sourceTitle).
 */
fun purgeTitle(item: Insight): String? { val title: String? = item.sourceTitle; return title }
