package org.openlearning.app

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch

/**
 * The notes list and server limits, split out of the view model. The list is held in [state] (memory only) and
 * every transition goes through the pure reducers in NotesListState.kt.
 * [onForgotten] drops the tutor memory and open detail of a document that was just forgotten. Hidden documents are
 * scrubbed from the published state by the view model's derived view, so nothing here checks the queue before publishing.
 */
internal class NotesActions(
    private val scope: CoroutineScope,
    private val queue: ForgetQueue,
    private val repo: () -> LearningRepository?,
    private val state: ScreenPublisher,
    private val onForgotten: () -> Unit,
    private val resume: suspend (scope: String) -> Unit,
    private val clock: () -> Long = System::currentTimeMillis,
) {
    /** Best-effort: a failed or malformed answer yields the defaults, never an error. */
    suspend fun fetchLimits(repository: LearningRepository): EffectiveLimits {
        val fetched = runCatching { repository.api.limits() }.onFailure { if (it is CancellationException) throw it }.getOrNull()
        return effectiveLimits(fetched)
    }

    fun load() {
        val repository = repo() ?: return
        state.publishLocal { it.copy(notes = it.notes.refreshStarted()) }
        val generation = state.value.notes.generation
        scope.launch {
            try {
                // The clock is read before the request: a row confirmed while it is in flight is never judged by its answer.
                val startedAt = clock()
                val raw = repository.api.notes()
                val page = serverPage(raw)
                // Hidden notes are scrubbed by the derived view; the answer only settles confirmed rows and resumes pending ones.
                launch { resume(repository.config.scope) }
                try { queue.reconcile(repository.config.scope, page.ids, page.complete, startedAt) }
                catch (cancelled: CancellationException) { throw cancelled }
                catch (error: Exception) { android.util.Log.w("OpenLearning", "Could not settle confirmed forgets") }
                if (repo() === repository) state.publishFresh(repository.config.scope) { it.copy(notes = it.notes.refreshed(raw.toSummaries(), generation)) }
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (error: Exception) { if (repo() === repository) state.publishLocal { it.copy(notes = it.notes.refreshFailed(error.userMessage(), generation)) } }
        }
    }

    /**
     * Offline-first: the note is hidden on this device at once (queued in Room, so it survives process death) and the
     * server DELETE is replayed by [ForgetWorker] when a connection allows. Fails only if the local write fails.
     */
    private suspend fun deleteAndPurge(repository: LearningRepository, documentId: String, title: String?): ForgetResult =
        try { ForgetResult.Done(queue.forgetNoteOffline(repository.config.scope, documentId, title)) }
        catch (cancelled: CancellationException) { throw cancelled }
        catch (error: Exception) { ForgetResult.Failed(error) }

    /** The outcome plus guidance for failed forgets (a count only, read from Room at forget time). */
    private suspend fun banner(repository: LearningRepository, result: ForgetResult): String =
        forgetBanner(result, queue.failedForgets(repository.config.scope).first())

    fun forget(note: NoteSummary) {
        val repository = repo() ?: return
        if (note.documentId in state.value.notes.forgetting) return
        state.publishLocal { it.copy(notes = it.notes.forgetStarted(note.documentId)) }
        scope.launch {
            val result = deleteAndPurge(repository, note.documentId, note.title)
            if (repo() !== repository) return@launch
            when (result) {
                is ForgetResult.Failed -> state.publishLocal { it.copy(notes = it.notes.forgetFailed(note.documentId, result.message())) }
                is ForgetResult.Done -> {
                    onForgotten()
                    val banner = banner(repository, result)
                    state.publishLocal { it.copy(notes = it.notes.forgetSucceeded(note.documentId, banner)) }
                }
            }
        }
    }

    /** Forget from a note's detail screen; same rules as [forget]. */
    fun forgetInsight(item: Insight) {
        val repository = repo() ?: return
        if (!item.isNote() || item.documentId.isBlank()) return
        scope.launch {
            val result = deleteAndPurge(repository, item.documentId, purgeTitle(item))
            if (repo() !== repository) return@launch
            when (result) {
                is ForgetResult.Failed -> state.publishLocal { it.copy(error = result.message()) }
                is ForgetResult.Done -> {
                    onForgotten()
                    val banner = banner(repository, result)
                    state.publishLocal { it.copy(notice = Notice(banner)) }
                }
            }
        }
    }

    /** Failed rows only: the row goes back to pending and its worker is scheduled again. */
    fun retryForget(documentId: String) {
        val repository = repo() ?: return
        scope.launch { queue.retryForget(repository.config.scope, documentId) }
    }

    /** Failed rows only: the row is deleted, the note is shown again on the next refresh, and the server keeps it. */
    fun stopTrying(documentId: String) {
        val repository = repo() ?: return
        scope.launch {
            if (queue.stopTrying(repository.config.scope, documentId) && repo() === repository) {
                load()
            }
        }
    }

    /** The list is memory-only and only kept while its screen is open. */
    fun close() = state.publishLocal { it.copy(notes = it.notes.closed()) }

    fun messageShown() = state.publishLocal { it.copy(notes = it.notes.messageShown()) }
}
