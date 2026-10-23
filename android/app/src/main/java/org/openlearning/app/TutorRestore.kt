package org.openlearning.app

import androidx.lifecycle.SavedStateHandle
import kotlinx.coroutines.CancellationException
import retrofit2.HttpException

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

/** Scoped by connection so a stale draft cannot reappear under another server or account. */
fun tutorDraftKey(scope: String, insightId: String) = "tutor-draft:$scope:$insightId"

sealed interface ResolvedInsight {
    data class Found(val insight: Insight) : ResolvedInsight
    /** Confirmed missing on the server (HTTP 404). */
    data object NotFound : ResolvedInsight
    /** Transient failure (offline, 5xx, ...): the saved selection must be kept for a later retry. */
    data object Unavailable : ResolvedInsight
}

/** Cache first, then server. Only a 404 means the item is gone; any other failure is transient. */
suspend fun resolveInsight(id: String, cached: suspend (String) -> Insight?, fetch: suspend (String) -> Insight): ResolvedInsight =
    cached(id)?.let { ResolvedInsight.Found(it) } ?: try {
        ResolvedInsight.Found(fetch(id))
    } catch (cancelled: CancellationException) {
        throw cancelled
    } catch (error: HttpException) {
        if (error.code() == 404) ResolvedInsight.NotFound else ResolvedInsight.Unavailable
    } catch (_: Exception) {
        ResolvedInsight.Unavailable
    }

data class ConnectionStep(val restore: Boolean, val restorePending: Boolean) {
    /** Reconnecting (anything but the one pending restore) discards the saved selection. */
    val clearMemory: Boolean get() = !restore
}

/** The first emission with a real config consumes the pending restore; a null config does not. */
fun connectionStep(restorePending: Boolean, hasConfig: Boolean) =
    ConnectionStep(restore = restorePending, restorePending = restorePending && !hasConfig)

sealed interface RestoreDecision {
    data class Restore(val insight: Insight) : RestoreDecision
    data object Skip : RestoreDecision
    data object Clear : RestoreDecision
}

/** Decides what to do once the saved item has been resolved, guarding against user actions that raced it. */
fun decideRestore(
    saved: SavedTutor, repoScope: String, repoCurrent: Boolean, selectedId: String?,
    remembered: SavedTutor?, resolved: ResolvedInsight, hidden: Set<String> = emptySet(),
): RestoreDecision = when {
    saved.scope != repoScope || !repoCurrent || selectedId != null || remembered != saved -> RestoreDecision.Skip
    else -> when (resolved) {
        // A note forgotten on this device must not reopen from a saved selection, even if the server still holds it.
        is ResolvedInsight.Found -> if (resolved.insight.isHiddenBy(hidden)) RestoreDecision.Clear else RestoreDecision.Restore(resolved.insight)
        ResolvedInsight.NotFound -> RestoreDecision.Clear
        ResolvedInsight.Unavailable -> RestoreDecision.Skip
    }
}
