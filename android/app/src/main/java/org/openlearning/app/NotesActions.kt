package org.openlearning.app

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/**
 * The notes list and server limits, split out of the view model. The list is held in [state] (memory only) and
 * every transition goes through the pure reducers in NotesListState.kt.
 * [onForgotten] drops the tutor memory and open detail of a document that was just forgotten.
 */
internal class NotesActions(
    private val scope: CoroutineScope,
    private val forgotten: ForgottenNotes,
    private val queue: ForgetQueue,
    private val repo: () -> LearningRepository?,
    private val state: MutableStateFlow<ScreenState>,
    private val onForgotten: () -> Unit,
) {
    /** Best-effort: a failed or malformed answer yields the defaults, never an error. */
    suspend fun fetchLimits(repository: LearningRepository): EffectiveLimits {
        val fetched = runCatching { repository.api.limits() }.onFailure { if (it is CancellationException) throw it }.getOrNull()
        return effectiveLimits(fetched)
    }

    fun load() {
        val repository = repo() ?: return
        state.update { it.copy(notes = it.notes.refreshStarted()) }
        val generation = state.value.notes.generation
        scope.launch {
            try {
                val fetched = queue.visibleNotes(repository.config.scope, repository.api.notes().toSummaries())
                if (repo() === repository) state.update { it.copy(notes = it.notes.refreshed(fetched, generation)) }
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (error: Exception) { if (repo() === repository) state.update { it.copy(notes = it.notes.refreshFailed(error.userMessage(), generation)) } }
        }
    }

    /**
     * Offline-first: the note is hidden on this device at once (queued in Room, so it survives process death) and the
     * server DELETE is replayed by [ForgetWorker] when a connection allows. Fails only if the local write fails.
     */
    private suspend fun deleteAndPurge(repository: LearningRepository, documentId: String, title: String?): ForgetResult =
        try { ForgetResult.Done(queue.forgetNoteOffline(repository.config.scope, documentId, title)) }
        catch (cancelled: CancellationException) { throw cancelled }
        catch (error: Exception) { forgotten.remove(repository.config.scope, documentId); ForgetResult.Failed(error) }

    /** The outcome plus the queue state (waiting and failed counts, ids only) read from Room. */
    private suspend fun banner(repository: LearningRepository, result: ForgetResult): String {
        val scopeKey = repository.config.scope
        return forgetBanner(result, queue.pendingForgetCount(scopeKey).first(), queue.failedForgets(scopeKey).first().size)
    }

    fun forget(note: NoteSummary) {
        val repository = repo() ?: return
        if (note.documentId in state.value.notes.forgetting) return
        state.update { it.copy(notes = it.notes.forgetStarted(note.documentId)) }
        scope.launch {
            val result = deleteAndPurge(repository, note.documentId, note.title)
            if (repo() !== repository) return@launch
            when (result) {
                is ForgetResult.Failed -> state.update { it.copy(notes = it.notes.forgetFailed(note.documentId, result.message())) }
                is ForgetResult.Done -> {
                    onForgotten()
                    val banner = banner(repository, result)
                    state.update { it.withoutDocument(note.documentId, result.purged).copy(notes = it.notes.forgetSucceeded(note.documentId, banner)) }
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
                is ForgetResult.Failed -> state.update { it.copy(error = result.message()) }
                is ForgetResult.Done -> {
                    onForgotten()
                    val banner = banner(repository, result)
                    state.update { it.withoutDocument(item.documentId, result.purged + item.id).copy(notice = Notice(banner)) }
                }
            }
        }
    }

    /** Failed rows only: the row goes back to pending and its worker is scheduled again. */
    fun retryForget(documentId: String) {
        val repository = repo() ?: return
        scope.launch { queue.retryForget(repository.config.scope, documentId) }
    }

    /** The list is memory-only and only kept while its screen is open. */
    fun close() = state.update { it.copy(notes = it.notes.closed()) }

    fun messageShown() = state.update { it.copy(notes = it.notes.messageShown()) }
}
