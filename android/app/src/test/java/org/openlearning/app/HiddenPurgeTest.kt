package org.openlearning.app

import org.junit.Assert.*
import org.junit.Test

/** Hiding must remove content from retained state, not only mask it: a pruned tombstone must not reopen anything. */
class HiddenPurgeTest {
    private fun ins(id: String, doc: String) = Insight(id = id, documentId = doc)
    private fun review(id: String, insight: String, doc: String = "") = Review(id, insight, "q", "d", 1.0, documentId = doc)
    private fun job(id: String, doc: String?) = PendingIngest("s", id, "", kind = "note", status = "completed", documentId = doc, title = "T$id")

    private fun full() = ScreenState(
        scope = "s", feed = listOf(ins("a", "d1"), ins("b", "d2")), library = listOf(ins("c", "d1")), results = listOf(ins("d", "d1")),
        selected = ins("a", "d1"), chat = listOf(ChatLine("Tutor", "x", "q", listOf(Citation(documentId = "d1"), Citation(documentId = "d2")))),
        reviews = listOf(review("r1", "i1", "d1"), review("r2", "i2")), reviewDocs = mapOf("i1" to "d1", "i2" to "d2"),
        reviewSource = ins("i1", "d1"), revealed = true,
        notes = NotesListState(items = listOf(NoteSummary("d1", "t", null, "", 1), NoteSummary("d2", "t", null, "", 1))),
        jobs = listOf(job("j1", "d1"), job("j2", "d2"), job("j3", null)),
    )

    @Test fun purgeRemovesEverythingTheMaskRemoves() {
        assertEquals(full().withoutHidden(setOf("d1")), full().purgedOf(setOf("d1")))
    }
    @Test fun nothingReappearsOnceTheTombstoneIsPruned() {
        val purged = full().purgedOf(setOf("d1"))
        val afterPrune = purged.withoutHidden(emptySet())
        assertTrue(afterPrune.feed.none { it.documentId == "d1" }); assertTrue(afterPrune.library.isEmpty()); assertTrue(afterPrune.results.isEmpty())
        assertNull(afterPrune.selected); assertNull(afterPrune.reviewSource); assertFalse(afterPrune.revealed)
        assertEquals(listOf("r2"), afterPrune.reviews.map { it.id }); assertEquals(mapOf("i2" to "d2"), afterPrune.reviewDocs)
        assertEquals(listOf("d2"), afterPrune.notes.items.map { it.documentId })
        assertEquals(listOf("j2", "j3"), afterPrune.jobs.map { it.id })
        assertTrue(afterPrune.chat.isEmpty())
    }
    @Test fun purgeKeepsTheSameInstanceWhenNothingMatches() {
        val state = full()
        assertSame(state, state.purgedOf(setOf("zzz")))
    }
    @Test fun completedImportsOfAForgottenDocumentLeaveRecentImports() {
        val state = ScreenState(jobs = listOf(job("j1", "d1"), job("j2", null), job("j3", "")))
        assertEquals(listOf("j2", "j3"), state.withoutHidden(setOf("d1")).jobs.map { it.id })
    }
    @Test fun aHiddenSetOfAnotherScopeNeverShapesTheView() {
        val state = full()
        val view = state.viewFor("other", setOf("d1"))
        assertEquals("s", view.scope); assertTrue(view.feed.isEmpty()); assertTrue(view.jobs.isEmpty()); assertTrue(view.reviews.isEmpty())
    }
    @Test fun theMatchingScopeIsMaskedNormally() {
        assertEquals(full().withoutHidden(setOf("d1")), full().viewFor("s", setOf("d1")))
    }
    @Test fun aPurgeOfAnotherScopeLeavesTheStateAlone() {
        val state = full()
        assertSame(state, state.purgedFor("other", setOf("d1")))
        assertEquals(state.purgedOf(setOf("d1")), state.purgedFor("s", setOf("d1")))
    }
    @Test fun anImportRowIsHiddenByItsOwnDocumentId() {
        assertTrue(job("j1", "d1").isHiddenBy(setOf("d1")))
        assertFalse(job("j2", "d2").isHiddenBy(setOf("d1")))
        assertFalse(job("j3", null).isHiddenBy(setOf("d1"))); assertFalse(job("j4", "").isHiddenBy(setOf("")))
        assertFalse(job("j1", "d1").isHiddenBy(emptySet()))
    }
}
