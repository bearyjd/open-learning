package org.openlearning.app

import org.junit.Assert.*
import org.junit.Test

class ForgetLogicTest {
    @Test fun replayDecisionTable() {
        assertEquals(ForgetReplay.Done, forgetReplay(204))
        assertEquals(ForgetReplay.Done, forgetReplay(200))
        assertEquals(ForgetReplay.Done, forgetReplay(404))
        assertEquals(ForgetReplay.Failed(FORGET_REJECTED_ERROR), forgetReplay(401))
        assertEquals(ForgetReplay.Failed(FORGET_REJECTED_ERROR), forgetReplay(403))
        assertEquals(ForgetReplay.Retry, forgetReplay(500))
        assertEquals(ForgetReplay.Retry, forgetReplay(503))
        assertEquals(ForgetReplay.Retry, forgetReplay(429))
        assertEquals(ForgetReplay.Retry, forgetReplay(null))
        assertEquals(ForgetReplay.Failed(FORGET_REFUSED_ERROR), forgetReplay(400))
    }
    @Test fun errorStringsAreFixed() {
        assertEquals("Could not delete (sign-in rejected)", FORGET_REJECTED_ERROR)
        assertFalse(FORGET_REFUSED_ERROR.contains("SENTINEL"))
    }
    @Test fun schedulingConstants() {
        assertEquals("forget-s-d1", forgetWorkName("s", "d1"))
        assertEquals(30L, FORGET_BACKOFF_SECONDS)
        assertEquals(NOTE_BACKOFF_SECONDS, FORGET_BACKOFF_SECONDS)
    }
    @Test fun hiddenFilterAppliesToAnyList() {
        val hidden = setOf("d1")
        val insights = listOf(Insight(id = "a", documentId = "d1"), Insight(id = "b", documentId = "d2"), Insight(id = "c", documentId = ""))
        assertEquals(listOf("b", "c"), insights.hideForgotten(hidden) { it.documentId }.map { it.id })
        val notes = listOf(NoteSummary("d1", "t", null, "2026-01-01", 1), NoteSummary("d2", "t", null, "2026-01-01", 1))
        assertEquals(listOf("d2"), notes.hideForgotten(hidden) { it.documentId }.map { it.documentId })
        assertSame(insights, insights.hideForgotten(emptySet()) { it.documentId })
    }
    @Test fun scopeIsolationOfPendingRows() {
        val rows = listOf(PendingForget("a", "d1", 1, "pending"), PendingForget("b", "d1", 2, "pending"), PendingForget("a", "d2", 3, "failed"))
        assertEquals(setOf("d1", "d2"), hiddenIds(rows, "a"))
        assertEquals(setOf("d1"), hiddenIds(rows, "b"))
        assertEquals(emptySet<String>(), hiddenIds(rows, "c"))
    }
    @Test fun activeNoteJobsLinkedToTheDocumentAreCancelled() {
        fun row(id: String, status: String, doc: String?, text: String? = null) = PendingIngest("s", id, "", kind = "note", status = status, documentId = doc, text = text)
        val rows = listOf(row("a", "queued", "d1", "x"), row("b", "failed", "d1", "x"), row("c", "queued", "d2", "x"), row("d", "queued", null, "x"), row("e", "completed", "d1"))
        assertEquals(listOf("a", "b"), activeRowsToCancel(rows, "d1").map { it.id })
    }
    @Test fun workerRunsOnlyForTheActiveScope() {
        assertEquals(ForgetStart.RUN, forgetStart("a", "a"))
        assertEquals(ForgetStart.LEAVE_PENDING, forgetStart("b", "a"))
        assertEquals(ForgetStart.LEAVE_PENDING, forgetStart(null, "a"))
    }
}
