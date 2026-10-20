package org.openlearning.app

import androidx.lifecycle.SavedStateHandle
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Test
import retrofit2.HttpException
import retrofit2.Response
import java.io.IOException

class TutorRestoreTest {
    private val item = Insight(id = "idea-1", title = "Cached")

    @Test fun selectionIsSavedAsIdentifiersOnly() {
        val handle = SavedStateHandle()
        TutorMemory(handle).remember("scope-a", "idea-1", learning = true)
        assertEquals(SavedTutor("scope-a", "idea-1", true), TutorMemory(handle).recall("scope-a"))
        assertTrue(handle.keys().all { handle.get<Any>(it).let { value -> value is String || value is Boolean } })
    }
    @Test fun closingTheDetailClearsTheSavedSelection() {
        val memory = TutorMemory(SavedStateHandle())
        memory.remember("scope-a", "idea-1", learning = false)
        memory.clear()
        assertNull(memory.recall("scope-a"))
    }
    @Test fun selectionFromAnotherConnectionIsNeverRestored() {
        val memory = TutorMemory(SavedStateHandle())
        memory.remember("scope-a", "idea-1", learning = true)
        assertNull(memory.recall("scope-b"))
    }
    @Test fun restoredSelectionResolvesFromCacheWithoutNetwork() = runBlocking {
        assertEquals(ResolvedInsight.Found(item), resolveInsight("idea-1", cached = { item }, fetch = { error("offline") }))
    }
    @Test fun uncachedSelectionIsFetched() = runBlocking {
        assertEquals(ResolvedInsight.Found(item), resolveInsight("idea-1", cached = { null }, fetch = { item }))
    }
    private fun http(code: Int) = HttpException(Response.error<Any>(code, "".toResponseBody()))
    @Test fun serverNotFoundMeansTheItemIsGone() = runBlocking {
        assertEquals(ResolvedInsight.NotFound, resolveInsight("gone", cached = { null }, fetch = { throw http(404) }))
    }
    @Test fun transientFailuresMeanUnavailableNotGone() = runBlocking {
        assertEquals(ResolvedInsight.Unavailable, resolveInsight("x", cached = { null }, fetch = { throw IOException("offline") }))
        assertEquals(ResolvedInsight.Unavailable, resolveInsight("x", cached = { null }, fetch = { throw http(503) }))
    }
    @Test fun cancellationIsNotSwallowedDuringRestore() {
        assertThrows(CancellationException::class.java) {
            runBlocking { resolveInsight("idea-1", cached = { null }, fetch = { throw CancellationException("stop") }) }
        }
    }
    @Test fun draftKeyIsTiedToScopeAndItem() {
        assertEquals("tutor-draft:scope-a:idea-1", tutorDraftKey("scope-a", "idea-1"))
        assertNotEquals(tutorDraftKey("scope-a", "idea-1"), tutorDraftKey("scope-a", "idea-2"))
        assertNotEquals(tutorDraftKey("scope-a", "idea-1"), tutorDraftKey("scope-b", "idea-1"))
    }

    private val saved = SavedTutor("scope-a", "idea-1", true)
    private fun decide(
        resolved: ResolvedInsight = ResolvedInsight.Found(item), repoScope: String = "scope-a",
        selectedId: String? = null, remembered: SavedTutor? = saved, repoCurrent: Boolean = true,
    ) = decideRestore(saved, repoScope, repoCurrent, selectedId, remembered, resolved)

    @Test fun untouchedFoundSelectionIsRestored() = assertEquals(RestoreDecision.Restore(item), decide())
    @Test fun userSelectingFirstSkipsRestore() = assertEquals(RestoreDecision.Skip, decide(selectedId = "idea-2"))
    @Test fun userClosingFirstSkipsRestore() = assertEquals(RestoreDecision.Skip, decide(remembered = null))
    @Test fun userReselectingDifferentItemSkipsRestore() =
        assertEquals(RestoreDecision.Skip, decide(remembered = SavedTutor("scope-a", "idea-2", false)))
    @Test fun scopeMismatchIsIgnored() = assertEquals(RestoreDecision.Skip, decide(repoScope = "scope-b", resolved = ResolvedInsight.NotFound))
    @Test fun staleRepositoryIsIgnored() = assertEquals(RestoreDecision.Skip, decide(repoCurrent = false))
    @Test fun notFoundClearsAndUnavailableKeeps() {
        assertEquals(RestoreDecision.Clear, decide(resolved = ResolvedInsight.NotFound))
        assertEquals(RestoreDecision.Skip, decide(resolved = ResolvedInsight.Unavailable))
    }
    @Test fun unavailableDoesNotClearSavedMemory() {
        val memory = TutorMemory(SavedStateHandle())
        memory.remember("scope-a", "idea-1", learning = true)
        val d = decide(resolved = ResolvedInsight.Unavailable, remembered = memory.recall("scope-a"))
        if (d == RestoreDecision.Clear) memory.clear()
        assertEquals(saved, memory.recall("scope-a"))
    }

    @Test fun firstNullConfigDoesNotConsumeRestore() {
        val first = connectionStep(restorePending = true, hasConfig = false)
        assertEquals(ConnectionStep(restore = true, restorePending = true), first)
        assertEquals(ConnectionStep(restore = true, restorePending = false), connectionStep(first.restorePending, hasConfig = true))
    }
    @Test fun restoreRunsOnceAndReconnectClears() {
        val second = connectionStep(restorePending = false, hasConfig = true)
        assertFalse(second.restore)
        assertTrue(second.clearMemory)
        assertFalse(connectionStep(true, true).clearMemory)
    }
}
