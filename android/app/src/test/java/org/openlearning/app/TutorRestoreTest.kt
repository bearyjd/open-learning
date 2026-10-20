package org.openlearning.app

import androidx.lifecycle.SavedStateHandle
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Test

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
        assertEquals(item, resolveInsight("idea-1", cached = { item }, fetch = { error("offline") }))
    }
    @Test fun uncachedSelectionIsFetched() = runBlocking {
        assertEquals(item, resolveInsight("idea-1", cached = { null }, fetch = { item }))
    }
    @Test fun missingItemFallsBackToNullInsteadOfCrashing() = runBlocking {
        assertNull(resolveInsight("gone", cached = { null }, fetch = { error("404") }))
    }
    @Test fun cancellationIsNotSwallowedDuringRestore() {
        assertThrows(CancellationException::class.java) {
            runBlocking { resolveInsight("idea-1", cached = { null }, fetch = { throw CancellationException("stop") }) }
        }
    }
    @Test fun draftKeyIsTiedToTheItem() {
        assertEquals(tutorDraftKey("idea-1"), tutorDraftKey("idea-1"))
        assertNotEquals(tutorDraftKey("idea-1"), tutorDraftKey("idea-2"))
    }
}
