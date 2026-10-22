package org.openlearning.app

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import retrofit2.HttpException

/**
 * The notes list and server limits, split out of the view model. The list is held in [state] (memory only) and
 * every transition goes through the pure reducers in NotesListState.kt.
 * [onForgotten] drops the tutor memory and open detail of a document that was just forgotten.
 */
internal class NotesActions(
    private val scope: CoroutineScope,
    private val forgotten: ForgottenNotes,
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
            retryPurges(repository)
            try {
                val fetched = repository.api.notes().toSummaries()
                if (repo() === repository) state.update { it.copy(notes = it.notes.refreshed(fetched, generation)) }
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (error: Exception) { if (repo() === repository) state.update { it.copy(notes = it.notes.refreshFailed(error.userMessage(), generation)) } }
        }
    }

    /**
     * Online-only: nothing is cleared locally unless the server confirms the note is gone (204 or 404, so a retry after a
     * lost answer succeeds). The tombstone is set first so an in-flight refresh cannot write cards back; it is dropped only
     * when the server did not delete. A cleanup error after the server delete keeps it (the note stays hidden).
     */
    private suspend fun deleteAndPurge(repository: LearningRepository, documentId: String, title: String?): ForgetResult {
        val scopeKey = repository.config.scope
        forgotten.add(scopeKey, documentId)
        try {
            val response = repository.api.forgetNote(documentId)
            if (!response.isGone()) throw HttpException(response)
        } catch (cancelled: CancellationException) { forgotten.remove(scopeKey, documentId); throw cancelled }
        catch (error: Exception) { forgotten.remove(scopeKey, documentId); return ForgetResult.Failed(error) }
        return try { ForgetResult.Done(repository.purgeDocument(documentId, title)) }
        catch (cancelled: CancellationException) { throw cancelled }
        catch (error: Exception) {
            android.util.Log.w("OpenLearning", "Could not clean up a forgotten note on this device")
            forgotten.purgeFailed(scopeKey, documentId, title)
            ForgetResult.PurgeFailed
        }
    }

    /**
     * Re-runs the local cleanup of notes the server deleted but this device could not clean (see [ForgottenNotes.purgeFailed]).
     * Called on connect and whenever the list is opened. Memory only: a process death before a retry forgets the need.
     */
    suspend fun retryPurges(repository: LearningRepository) {
        val scopeKey = repository.config.scope
        for (pending in forgotten.pendingPurges(scopeKey)) {
            try {
                val purged = repository.purgeDocument(pending.documentId, pending.title)
                forgotten.purgeDone(scopeKey, pending.documentId)
                if (repo() === repository) state.update { it.withoutDocument(pending.documentId, purged) }
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (error: Exception) { android.util.Log.w("OpenLearning", "Retrying the cleanup of a forgotten note failed") }
        }
    }

    fun forget(note: NoteSummary) {
        val repository = repo() ?: return
        if (note.documentId in state.value.notes.forgetting) return
        state.update { it.copy(notes = it.notes.forgetStarted(note.documentId)) }
        scope.launch {
            val result = deleteAndPurge(repository, note.documentId, note.title)
            if (repo() !== repository) return@launch
            when (result) {
                is ForgetResult.Failed -> state.update { it.copy(notes = it.notes.forgetFailed(note.documentId, result.message().orEmpty())) }
                else -> {
                    onForgotten()
                    val purged = (result as? ForgetResult.Done)?.purged.orEmpty()
                    state.update { it.withoutDocument(note.documentId, purged).copy(notes = it.notes.forgetSucceeded(note.documentId, result.message() ?: "Note forgotten.")) }
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
                else -> {
                    onForgotten()
                    val purged = (result as? ForgetResult.Done)?.purged.orEmpty()
                    state.update { it.withoutDocument(item.documentId, purged + item.id).copy(notice = Notice(result.message() ?: "Note forgotten.")) }
                }
            }
        }
    }

    /** The list is memory-only and only kept while its screen is open. */
    fun close() = state.update { it.copy(notes = it.notes.closed()) }

    fun messageShown() = state.update { it.copy(notes = it.notes.messageShown()) }
}
