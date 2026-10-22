package org.openlearning.app

import org.junit.Assert.*
import org.junit.Test

class NotesListFormatTest {
    private fun note(label: String? = "Lecture", chars: Int = 1234, created: String = "2026-10-22T09:30:00Z") =
        NoteSummary("d1", "Title", label, created, chars)

    @Test fun rowMetaShowsLabelCountAndDate() = assertEquals("Lecture · 1,234 characters · 2026-10-22", note().rowMeta())
    @Test fun rowMetaOmitsMissingLabel() = assertEquals("1,234 characters · 2026-10-22", note(label = null).rowMeta())
    @Test fun rowMetaSingular() = assertEquals("1 character · 2026-10-22", note(label = null, chars = 1).rowMeta())
    @Test fun rowMetaBlankDate() = assertEquals("0 characters · date unknown", note(label = null, chars = 0, created = "").rowMeta())

    @Test fun bodyLoadingOnlyBeforeFirstLoad() {
        assertEquals(NotesBody.Loading, notesBody(NotesListState(loading = true)))
        assertEquals(NotesBody.Empty, notesBody(NotesListState(loading = true, loaded = true)))
    }
    @Test fun bodyErrorOnlyWhenNothingToShow() {
        assertEquals(NotesBody.Error, notesBody(NotesListState(error = "x")))
        assertEquals(NotesBody.List, notesBody(NotesListState(items = listOf(note()), loaded = true, error = "x")))
    }
    @Test fun bodyEmptyAndList() {
        assertEquals(NotesBody.Empty, notesBody(NotesListState(loaded = true)))
        assertEquals(NotesBody.List, notesBody(NotesListState(items = listOf(note()), loaded = true)))
    }

    @Test fun forgetCopyDoesNotClaimEveryCompletedImportIsCleared() {
        assertFalse(FORGET_NOTE_MESSAGE.contains("every completed"))
        assertTrue(FORGET_NOTE_MESSAGE.contains("before this update"))
        assertTrue(FORGET_NOTE_MESSAGE.contains("needs a connection"))
    }

    @Test fun sharedNoteWaitsForTheAccountButLinkDoesNot() {
        val text = ShareRoute.Note("t", "body")
        assertTrue(shareDeferred(text, false, true)); assertFalse(shareDeferred(text, true, true))
        assertFalse(shareDeferred(ShareRoute.Url("https://a.example"), false, false))
        assertFalse(shareDeferred(ShareRoute.Ignore, false, false))
    }
    @Test fun onlyLinksFillTheImportField() {
        assertEquals("https://a.example", importTextFor(ShareRoute.Url("https://a.example")))
        assertNull(importTextFor(ShareRoute.Note("t", "body")))
        assertNull(importTextFor(ShareRoute.Ignore))
    }
}
