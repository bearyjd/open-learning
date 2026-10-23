package org.openlearning.app

import org.junit.Assert.*
import org.junit.Test

class JobWriteDecisionTest {
    private fun row(doc: String?) = PendingIngest("s", "j", "", kind = "note", status = "completed", documentId = doc, title = "T")
    @Test fun aRowOfAHiddenDocumentIsDeleted() { assertEquals(JobWrite.DELETE, jobWriteDecision(row("d1"), setOf("d1"))) }
    @Test fun otherRowsAreUpdated() {
        assertEquals(JobWrite.UPDATE, jobWriteDecision(row("d2"), setOf("d1")))
        assertEquals(JobWrite.UPDATE, jobWriteDecision(row(null), setOf("d1")))
        assertEquals(JobWrite.UPDATE, jobWriteDecision(row(""), setOf("")))
        assertEquals(JobWrite.UPDATE, jobWriteDecision(row("d1"), emptySet()))
    }
}
