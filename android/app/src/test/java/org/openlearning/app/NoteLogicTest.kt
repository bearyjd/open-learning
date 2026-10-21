package org.openlearning.app

import org.junit.Assert.*
import org.junit.Test

class NoteLogicTest {
    private val note = PendingIngest("s", "j1", "", kind = "note", title = "Lecture", text = "body")

    @Test fun tombstoneBlocksOnlyThatScopeAndDocument() {
        val forgotten = ForgottenNotes()
        forgotten.add("s", "d1")
        assertTrue(forgotten.blocks("s", Insight(documentId = "d1")))
        assertFalse(forgotten.blocks("other", Insight(documentId = "d1")))
        assertFalse(forgotten.blocks("s", Insight(documentId = "d2")))
        assertEquals(listOf("b"), forgotten.allowed("s", listOf(Insight(id = "a", documentId = "d1"), Insight(id = "b", documentId = "d2"))).map { it.id })
    }
    @Test fun tombstoneIgnoresBlankDocumentIdsAndCanBeRemoved() {
        val forgotten = ForgottenNotes()
        forgotten.add("s", "")
        assertFalse(forgotten.blocks("s", Insight(documentId = "")))
        forgotten.add("s", "d1"); forgotten.remove("s", "d1")
        assertFalse(forgotten.blocks("s", Insight(documentId = "d1")))
    }
    @Test fun counterCountsCodePointsWithThousandsSeparators() {
        assertEquals("0 / 60,000", noteCounter(""))
        assertEquals("1 / 60,000", noteCounter("😀"))
        assertEquals("1,000 / 60,000", noteCounter("a".repeat(1000)))
    }
    @Test fun jobRowShowsNoteTitleNotUrl() {
        assertEquals("Note: Lecture", note.displayName())
        assertEquals("Note: Untitled", note.copy(title = null).displayName())
        assertEquals("https://a.example", PendingIngest("s", "j", "https://a.example").displayName())
    }
    @Test fun retryHiddenOnCancelledNotesOnly() {
        assertFalse(note.copy(status = "cancelled").canRetry())
        assertTrue(PendingIngest("s", "j", "https://a.example", status = "cancelled").canRetry())
        assertTrue(note.copy(status = "failed").canRetry())
        assertFalse(note.copy(status = "queued").canRetry())
    }
    @Test fun retryPlans() {
        assertEquals(NoteRetry.ServerRetry("srv"), noteRetryPlan(note.copy(jobId = "srv", status = "failed")))
        assertEquals(NoteRetry.Requeue, noteRetryPlan(note.copy(status = "waiting")))
        assertEquals(NoteRetry.Expired, noteRetryPlan(note.copy(text = null, status = "failed")))
    }
    @Test fun conflictResubmitsFromLocalTextWithoutJobId() {
        val next = noteConflictPlan(note.copy(jobId = "srv", status = "failed", error = "x"))!!
        assertNull(next.jobId); assertEquals("queued", next.status); assertNull(next.error); assertEquals("body", next.text)
        assertNull(noteConflictPlan(note.copy(jobId = "srv", text = null)))
    }
    @Test fun forgetDropsDocumentEverywhere() {
        val gone = Insight(id = "a", documentId = "d1"); val keep = Insight(id = "b", documentId = "d2")
        val state = ScreenState(feed = listOf(gone, keep), library = listOf(gone), results = listOf(keep, gone), selected = gone, learning = true,
            reviews = listOf(Review("r1", "a", "q", "t", 1.0), Review("r2", "b", "q", "t", 1.0)), reviewSource = gone, revealed = true)
        val next = state.withoutDocument("d1", listOf("a", "extra"))
        assertEquals(listOf(keep), next.feed); assertEquals(emptyList<Insight>(), next.library); assertEquals(listOf(keep), next.results)
        assertEquals(listOf("r2"), next.reviews.map { it.id })
        assertNull(next.selected); assertNull(next.reviewSource); assertFalse(next.revealed); assertFalse(next.learning)
    }
    @Test fun forgetFailureNeverEchoesErrorText() {
        val offline = forgetFailureMessage(java.io.IOException("SECRET-BODY"))
        assertTrue("not forgotten" in offline && "SECRET" !in offline)
        assertFalse("SECRET" in forgetFailureMessage(IllegalStateException("SECRET-BODY")))
    }
    @Test fun fileReplacesOnlyAfterConfirmWhenTextIsTyped() {
        assertEquals(FilePlan.Apply, filePlan(NoteDraftFields(title = "t")))
        assertEquals(FilePlan.Apply, filePlan(NoteDraftFields(text = "  \n ")))
        assertEquals(FilePlan.ConfirmReplace, filePlan(NoteDraftFields(text = "typed")))
        val next = NoteDraftFields("mine", "l", "old").withFile(NoteFileResult.Loaded("file", "new"))
        assertEquals(NoteDraftFields("mine", "l", "new"), next)
        assertEquals("file", NoteDraftFields().withFile(NoteFileResult.Loaded("file", "new")).title)
    }
    @Test fun draftFieldsEmptiness() {
        assertTrue(NoteDraftFields().isEmpty)
        assertFalse(NoteDraftFields(label = "x").isEmpty)
    }
}
