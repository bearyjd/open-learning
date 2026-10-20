package org.openlearning.app

import androidx.lifecycle.SavedStateHandle
import kotlinx.coroutines.CancellationException

/** Identifiers only: no insight text, tokens, or chat history is ever written to saved state. */
data class SavedTutor(val scope: String, val insightId: String, val learning: Boolean)

class TutorMemory(private val handle: SavedStateHandle) {
    fun remember(scope: String, insightId: String, learning: Boolean) {
        handle[SCOPE] = scope; handle[INSIGHT] = insightId; handle[LEARNING] = learning
    }
    fun clear() { handle.remove<String>(SCOPE); handle.remove<String>(INSIGHT); handle.remove<Boolean>(LEARNING) }
    /** Returns the saved selection only if it belongs to the given connection scope. */
    fun recall(scope: String): SavedTutor? {
        val saved = handle.get<String>(SCOPE)
        val id = handle.get<String>(INSIGHT)
        if (saved != scope || id.isNullOrBlank()) return null
        return SavedTutor(saved, id, handle.get<Boolean>(LEARNING) == true)
    }
    private companion object {
        const val SCOPE = "tutor.scope"; const val INSIGHT = "tutor.insight"; const val LEARNING = "tutor.learning"
    }
}

fun tutorDraftKey(insightId: String) = "tutor-draft:$insightId"

/** Cache first, then server; null when the item no longer exists or cannot be reached. */
suspend fun resolveInsight(id: String, cached: suspend (String) -> Insight?, fetch: suspend (String) -> Insight): Insight? =
    cached(id) ?: try { fetch(id) } catch (cancelled: CancellationException) { throw cancelled } catch (_: Exception) { null }
