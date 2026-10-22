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
    @Test fun loadStartedBeforeDeleteCannotBringTheNoteBack() {
        val started = NotesListState().refreshed(listOf(a, b)).refreshStarted()
        val generation = started.generation
        val deleted = started.forgetStarted("a").forgetSucceeded("a")
        val late = deleted.refreshed(listOf(a, b), generation)
        assertEquals(listOf(b), late.items)
        // Still hidden on a second stale answer, and only released once the server stops listing it.
        assertEquals(listOf(b), late.refreshed(listOf(a, b)).items)
        val next = late.refreshStarted().let { it.refreshed(listOf(b), it.generation) }
        assertTrue(next.gone.isEmpty()); assertEquals(listOf(b), next.items)
    }
    @Test fun olderResponseNeverOverwritesNewer() {
        val first = NotesListState().refreshStarted()
        val second = first.refreshStarted()
        val newest = second.refreshed(listOf(b), second.generation)
        assertEquals(listOf(b), newest.items)
        assertEquals(newest, newest.refreshed(listOf(a), first.generation))
        assertEquals(newest, newest.refreshFailed("late failure", first.generation))
    }
    @Test fun staleAnswerDoesNotEndTheNewestLoad() {
        val second = NotesListState().refreshStarted().refreshStarted()
        val afterStale = second.refreshed(listOf(a), 1)
        assertTrue(afterStale.loading); assertTrue(afterStale.items.isEmpty())
    }
    @Test fun partialForgetStillRemovesTheRowWithItsOwnMessage() {
        val state = NotesListState().refreshed(listOf(a, b)).forgetStarted("a").forgetSucceeded("a", "partial")
        assertEquals(listOf(b), state.items); assertEquals("partial", state.message); assertTrue("a" in state.gone)
    }
    @Test fun toStringNeverShowsTitlesOrLabels() {
        val secret = NoteSummary("d1", "SECRET-TITLE", "SECRET-LABEL", "2026-01-01", 1)
        assertFalse("SECRET" in secret.toString())
        assertFalse("SECRET" in NoteListItem("d1", "SECRET-TITLE", "SECRET-LABEL").toString())
        val state = ScreenState(notes = NotesListState(items = listOf(secret)))
        assertFalse("SECRET" in state.toString())
        assertFalse("SECRET" in state.notes.toString())
    }
}
