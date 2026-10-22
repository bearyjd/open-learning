package org.openlearning.app

import org.junit.Assert.*
import org.junit.Test

class NotesListStateTest {
    private fun note(id: String) = NoteSummary(id, "T$id", null, "2026-01-01T00:00:00Z", 10)
    private val a = note("a"); private val b = note("b")

    @Test fun refreshLifecycle() {
        val loading = NotesListState().refreshStarted()
        assertTrue(loading.loading); assertFalse(loading.loaded)
        val done = loading.refreshed(listOf(a, b))
        assertEquals(listOf(a, b), done.items); assertFalse(done.loading); assertTrue(done.loaded); assertFalse(done.isEmpty)
        assertTrue(NotesListState().refreshed(emptyList()).isEmpty)
        assertFalse(NotesListState().isEmpty)
    }
    @Test fun failedRefreshKeepsRowsAndShowsError() {
        val state = NotesListState().refreshed(listOf(a)).refreshStarted().refreshFailed("offline")
        assertEquals(listOf(a), state.items); assertEquals("offline", state.error); assertFalse(state.loading)
        assertNull(state.refreshStarted().error)
    }
    @Test fun refreshDuringForgetDoesNotBringTheNoteBack() {
        val state = NotesListState().refreshed(listOf(a, b)).forgetStarted("a").refreshed(listOf(a, b))
        assertEquals(listOf(b), state.items)
    }
    @Test fun forgetSuccessRemovesOnlyThatNote() {
        val state = NotesListState().refreshed(listOf(a, b)).forgetStarted("a").forgetSucceeded("a")
        assertEquals(listOf(b), state.items); assertTrue(state.forgetting.isEmpty()); assertEquals("Note forgotten.", state.message)
    }
    @Test fun forgetFailureKeepsTheRowAndReportsIt() {
        val state = NotesListState().refreshed(listOf(a, b)).forgetStarted("a").forgetFailed("a", "no network")
        assertEquals(listOf(a, b), state.items); assertTrue(state.forgetting.isEmpty()); assertEquals("no network", state.message)
        assertNull(state.messageShown().message)
    }
}
