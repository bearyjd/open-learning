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
            try {
                val fetched = repository.api.notes().toSummaries()
                if (repo() === repository) state.update { it.copy(notes = it.notes.refreshed(fetched, generation)) }
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (error: Exception) { if (repo() === repository) state.update { it.copy(notes = it.notes.refreshFailed(error.userMessage(), generation)) } }
        }
    }

    /** Online-only: nothing is cleared locally unless the server confirms the note is gone (204 or 404). */
    fun forget(note: NoteSummary) {
        val repository = repo() ?: return
        val scopeKey = repository.config.scope
        if (note.documentId in state.value.notes.forgetting) return
        state.update { it.copy(notes = it.notes.forgetStarted(note.documentId)) }
        scope.launch {
            forgotten.add(scopeKey, note.documentId)
            try {
                val response = repository.api.forgetNote(note.documentId)
                if (!response.isGone()) throw HttpException(response)
                val purged = repository.purgeDocument(note.documentId)
                if (repo() === repository) {
                    onForgotten()
                    state.update { it.withoutDocument(note.documentId, purged).copy(notes = it.notes.forgetSucceeded(note.documentId)) }
                }
            } catch (cancelled: CancellationException) { forgotten.remove(scopeKey, note.documentId); throw cancelled }
            catch (error: Exception) {
                forgotten.remove(scopeKey, note.documentId)
                if (repo() === repository) state.update { it.copy(notes = it.notes.forgetFailed(note.documentId, forgetFailureMessage(error))) }
            }
        }
    }

    /** The list is memory-only and only kept while its screen is open. */
    fun close() = state.update { it.copy(notes = NotesListState()) }

    fun messageShown() = state.update { it.copy(notes = it.notes.messageShown()) }
}
