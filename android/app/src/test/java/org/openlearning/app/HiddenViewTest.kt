package org.openlearning.app

import org.junit.Assert.*
import org.junit.Test

/** The derived view: whatever lands in the mutable state, a hidden document never reaches the screen. */
class HiddenViewTest {
    private fun ins(id: String, doc: String) = Insight(id = id, documentId = doc)
    private fun review(id: String, insight: String, doc: String = "") = Review(id, insight, "q", "d", 1.0, documentId = doc)

    @Test fun sameInstanceWhenNothingMatches() {
        val state = ScreenState(feed = listOf(ins("a", "d2")), reviews = listOf(review("r", "a")), selected = ins("a", "d2"))
        assertSame(state, state.withoutHidden(setOf("d1")))
        assertSame(state, state.withoutHidden(emptySet()))
    }
    @Test fun blankDocumentIdsAreNeverHidden() {
        val state = ScreenState(feed = listOf(ins("a", "")))
        assertSame(state, state.withoutHidden(setOf("")))
    }
    @Test fun dropsFeedLibraryAndResults() {
        val state = ScreenState(feed = listOf(ins("a", "d1"), ins("b", "d2")), library = listOf(ins("c", "d1")), results = listOf(ins("d", "d1"), ins("e", "d3")))
        val next = state.withoutHidden(setOf("d1"))
        assertEquals(listOf("b"), next.feed.map { it.id }); assertTrue(next.library.isEmpty()); assertEquals(listOf("e"), next.results.map { it.id })
    }
    @Test fun hiddenSelectionClosesTheDetailAndTheTutor() {
        val state = ScreenState(selected = ins("a", "d1"), chat = listOf(ChatLine("You", "hi")), chatBusy = true, learning = true)
        val next = state.withoutHidden(setOf("d1"))
        assertNull(next.selected); assertTrue(next.chat.isEmpty()); assertFalse(next.chatBusy); assertFalse(next.learning)
    }
    @Test fun visibleSelectionKeepsItsChat() {
        val state = ScreenState(selected = ins("a", "d2"), chat = listOf(ChatLine("You", "hi")), learning = true)
        assertSame(state, state.withoutHidden(setOf("d1")))
    }
    @Test fun aChatCitingAHiddenDocumentIsClearedEntirely() {
        val reply = ChatLine("Tutor", "answer quoting the note", "q", listOf(Citation(documentId = "d1"), Citation(documentId = "d2")))
        val state = ScreenState(selected = ins("a", "d2"), chat = listOf(ChatLine("You", "q"), reply), chatBusy = true)
        val next = state.withoutHidden(setOf("d1"))
        assertTrue(next.chat.isEmpty()); assertFalse(next.chatBusy); assertEquals("a", next.selected?.id)
    }
    @Test fun reviewsAreDroppedByTheirOwnDocumentId() {
        val state = ScreenState(reviews = listOf(review("r1", "i1", "d1"), review("r2", "i2", "d2")))
        assertEquals(listOf("r2"), state.withoutHidden(setOf("d1")).reviews.map { it.id })
    }
    @Test fun reviewsAreDroppedByRecordedDocument() {
        val state = ScreenState(reviews = listOf(review("r1", "i1"), review("r2", "i2")), reviewDocs = mapOf("i1" to "d1", "i2" to "d2"))
        val next = state.withoutHidden(setOf("d1"))
        assertEquals(listOf("r2"), next.reviews.map { it.id }); assertEquals(mapOf("i2" to "d2"), next.reviewDocs)
    }
    @Test fun reviewSourceAndRevealAreDropped() {
        val state = ScreenState(reviews = listOf(review("r1", "i1"), review("r2", "i2")), reviewSource = ins("i1", "d1"), revealed = true)
        val next = state.withoutHidden(setOf("d1"))
        assertNull(next.reviewSource); assertFalse(next.revealed); assertEquals(listOf("r2"), next.reviews.map { it.id })
    }
    @Test fun revealStaysWhenTheSourceIsVisible() {
        val state = ScreenState(reviews = listOf(review("r1", "i1", "d1")), reviewSource = ins("i2", "d2"), revealed = true)
        val next = state.withoutHidden(setOf("d1"))
        assertTrue(next.revealed); assertNotNull(next.reviewSource)
    }
    @Test fun notesListRowsAreDropped() {
        val state = ScreenState(notes = NotesListState(items = listOf(NoteSummary("d1", "t", null, "", 1), NoteSummary("d2", "t", null, "", 1))))
        assertEquals(listOf("d2"), state.withoutHidden(setOf("d1")).notes.items.map { it.documentId })
    }
    @Test fun unrelatedFieldsSurvive() {
        val state = ScreenState(feed = listOf(ins("a", "d1")), scope = "s", busy = true, error = "e", notice = Notice("n"), forgetWaiting = 3)
        val next = state.withoutHidden(setOf("d1"))
        assertEquals("s", next.scope); assertTrue(next.busy); assertEquals("e", next.error); assertEquals(Notice("n"), next.notice); assertEquals(3, next.forgetWaiting)
    }
}

/** Reveal is judged against what the screen shows, so a hidden review at the head of the raw list cannot block the next one. */
class CanRevealTest {
    private fun review(id: String, insight: String, doc: String = "") = Review(id, insight, "q", "d", 1.0, documentId = doc)

    @Test fun hiddenHeadDoesNotBlockTheVisibleHead() {
        val state = ScreenState(reviews = listOf(review("r1", "i1", "d1"), review("r2", "i2", "d2")))
        assertTrue(state.canReveal(review("r2", "i2", "d2"), setOf("d1")))
    }
    @Test fun aHiddenReviewCannotBeRevealed() {
        val state = ScreenState(reviews = listOf(review("r1", "i1", "d1"), review("r2", "i2", "d2")))
        assertFalse(state.canReveal(review("r1", "i1", "d1"), setOf("d1")))
    }
    @Test fun onlyTheHeadCanBeRevealed() {
        val state = ScreenState(reviews = listOf(review("r1", "i1", "d1"), review("r2", "i2", "d2")))
        assertTrue(state.canReveal(review("r1", "i1", "d1"), emptySet())); assertFalse(state.canReveal(review("r2", "i2", "d2"), emptySet()))
    }
    @Test fun recordedDocumentsHideTheHeadToo() {
        val state = ScreenState(reviews = listOf(review("r1", "i1"), review("r2", "i2")), reviewDocs = mapOf("i1" to "d1", "i2" to "d2"))
        assertTrue(state.canReveal(review("r2", "i2"), setOf("d1")))
    }
}
