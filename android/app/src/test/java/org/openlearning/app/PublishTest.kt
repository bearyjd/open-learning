package org.openlearning.app

import org.junit.Assert.*
import org.junit.Test

/** Every publication goes through [published]: a late response for a hidden document is never stored, not merely masked. */
class PublishTest {
    private fun ins(id: String, doc: String) = Insight(id = id, documentId = doc)
    private fun review(id: String, insight: String, doc: String) = Review(id, insight, "q", "d", 1.0, documentId = doc)
    private fun job(id: String, doc: String?) = PendingIngest("s", id, "", kind = "note", status = "completed", documentId = doc, title = "T$id")
    private val hidden = listOf("s" to setOf("d1"))
    private val empty = ScreenState(scope = "s")

    @Test fun aLateSearchResultForAHiddenDocumentIsNotStored() {
        val next = empty.published(hidden) { it.copy(results = listOf(ins("a", "d1"), ins("b", "d2")), searched = true) }
        assertEquals(listOf("b"), next.results.map { it.id })
    }
    @Test fun afterTheTombstoneIsPrunedTheRetainedStateStillLacksTheDocument() {
        val next = empty.published(hidden) { it.copy(results = listOf(ins("a", "d1"))) }
        assertTrue(next.withoutHidden(emptySet()).results.isEmpty())
    }
    @Test fun lateReviewsAreNotStored() {
        val next = empty.published(hidden) { it.copy(reviews = listOf(review("r1", "i1", "d1"), review("r2", "i2", "d2")), reviewDocs = mapOf("i1" to "d1", "i2" to "d2")) }
        assertEquals(listOf("r2"), next.reviews.map { it.id }); assertEquals(mapOf("i2" to "d2"), next.reviewDocs)
    }
    @Test fun aLateNotesRefreshIsNotStored() {
        val next = empty.published(hidden) { it.copy(notes = it.notes.copy(items = listOf(NoteSummary("d1", "t", null, "", 1), NoteSummary("d2", "t", null, "", 1)))) }
        assertEquals(listOf("d2"), next.notes.items.map { it.documentId })
    }
    @Test fun lateJobsAreNotStored() {
        val next = empty.published(hidden) { it.copy(jobs = listOf(job("j1", "d1"), job("j2", null))) }
        assertEquals(listOf("j2"), next.jobs.map { it.id })
    }
    @Test fun aLateDetailAndItsChatAreNotStored() {
        val next = empty.published(hidden) { it.copy(selected = ins("a", "d1"), chat = listOf(ChatLine("Tutor", "x", "q", listOf(Citation(documentId = "d1"))))) }
        assertNull(next.selected); assertTrue(next.chat.isEmpty())
    }
    @Test fun aHiddenSetOfAnotherAccountIsNeverApplied() {
        val next = empty.published(listOf("other" to setOf("d1"))) { it.copy(results = listOf(ins("a", "d1"))) }
        assertEquals(listOf("a"), next.results.map { it.id })
    }
    @Test fun everySuppliedSetIsApplied() {
        val next = empty.published(listOf("s" to setOf("d1"), "s" to setOf("d2"))) { it.copy(results = listOf(ins("a", "d1"), ins("b", "d2"), ins("c", "d3"))) }
        assertEquals(listOf("c"), next.results.map { it.id })
    }
}
