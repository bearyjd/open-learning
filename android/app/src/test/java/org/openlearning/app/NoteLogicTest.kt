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
    @Test fun scopeChangePurgesOnlyCompletedRowsWithoutText() {
        assertTrue(note.copy(text = null, status = "completed").purgeableAfterScopeChange())
        assertTrue(note.copy(text = null, status = "Done").purgeableAfterScopeChange())
        assertTrue(PendingIngest("s", "u", "https://a.example", status = "completed").purgeableAfterScopeChange())
        for (status in listOf("queued", "running", "waiting", "paused", "failed", "error", "cancelled")) {
            assertFalse(status, note.copy(status = status).purgeableAfterScopeChange())
        }
        // Text always wins, even on a row that claims to be completed.
        assertFalse(note.copy(status = "completed").purgeableAfterScopeChange())
    }
    @Test fun aStalePurgeNeverRunsAfterReconnectingAnotherScope() {
        assertTrue(shouldRunPurge("b", "b"))
        assertFalse("A to B to A: the purge started for B must not run while A is active", shouldRunPurge("a", "b"))
        assertFalse(shouldRunPurge(null, "b"))
    }
    @Test fun noteBodiesStayOutOfToString() {
        val secret = "SECRET-BODY"
        assertFalse(secret in NoteDraftFields("t", "l", secret).toString())
        assertFalse(secret in PendingIngest("s", "i", "", kind = "note", text = secret).toString())
        assertFalse(secret in NoteDraft("s", "t", "l", secret).toString())
        assertFalse(secret in NoteDraftFields("t", "l", secret).copy(title = "x").toString())
    }

    private val queued = Notice("Note queued.", jobId = "j1")
    private fun job(id: String, status: String, kind: String = "note") = PendingIngest("s", id, "", kind = kind, status = status)

    @Test fun queuedNoticeClearsWhenItsJobReachesATerminalState() {
        for (status in listOf("completed", "Complete", "succeeded", "done", "failed", "error", "cancelled"))
            assertNull(status, noticeAfterJobUpdate(queued, listOf(job("j1", status))))
    }
    @Test fun queuedNoticeClearsWhenItsJobRowIsRemoved() {
        assertNull(noticeAfterJobUpdate(queued, emptyList()))
        assertNull(noticeAfterJobUpdate(queued, listOf(job("other", "queued"))))
    }
    @Test fun queuedNoticeStaysWhileTheJobIsStillInFlight() {
        for (status in listOf("queued", "running", "waiting", "paused", "pending"))
            assertSame(status, queued, noticeAfterJobUpdate(queued, listOf(job("j1", status))))
    }
    @Test fun otherNoticesAreNeverClearedByJobUpdates() {
        val forgotten = Notice("Note forgotten.")
        assertSame(forgotten, noticeAfterJobUpdate(forgotten, emptyList()))
        assertSame(forgotten, noticeAfterJobUpdate(forgotten, listOf(job("j1", "completed"))))
        assertNull(noticeAfterJobUpdate(null, listOf(job("j1", "completed"))))
    }
    @Test fun aSecondNotesNoticeIsIndependentOfTheFirstNotesCompletion() {
        val second = Notice("Note queued.", jobId = "j2")
        val jobs = listOf(job("j1", "completed"), job("j2", "waiting"))
        assertSame(second, noticeAfterJobUpdate(second, jobs))
        assertNull(noticeAfterJobUpdate(queued, jobs))
    }
    @Test fun urlImportsDoNotAffectNotices() {
        assertSame(queued, noticeAfterJobUpdate(queued, listOf(job("j1", "queued"), job("u1", "completed", kind = "url"))))
        assertEquals("Note queued.", queued.text)
    }
    @Test fun forgetOutcomeMapsToMessages() {
        assertNull(ForgetResult.Done(listOf("x")).message())
        assertEquals(FORGET_PARTIAL_MESSAGE, ForgetResult.PurgeFailed.message())
        assertTrue(FORGET_PARTIAL_MESSAGE.startsWith("Deleted on the server") && "hidden" in FORGET_PARTIAL_MESSAGE)
        val failed = ForgetResult.Failed(java.io.IOException("SECRET-BODY")).message()!!
        assertEquals(forgetFailureMessage(java.io.IOException("x")), failed)
        assertFalse("SECRET" in failed)
    }

    @Test fun failedPurgeIsRetriedForThatScopeUntilItSucceeds() {
        val forgotten = ForgottenNotes()
        forgotten.purgeFailed("s", "d1", "Lecture"); forgotten.purgeFailed("t", "d2", null)
        assertEquals(listOf(ForgottenNotes.PendingPurge("d1", "Lecture")), forgotten.pendingPurges("s"))
        assertEquals(listOf(ForgottenNotes.PendingPurge("d2", null)), forgotten.pendingPurges("t"))
        forgotten.purgeDone("s", "d1")
        assertTrue(forgotten.pendingPurges("s").isEmpty())
        assertEquals(1, forgotten.pendingPurges("t").size)
    }
    @Test fun purgeTitleIsTheDocumentTitleNotTheInsightHeadline() {
        val item = Insight(title = "A model-written headline", sourceTitle = "My lecture note", sourceKind = "note")
        assertEquals("My lecture note", purgeTitle(item))
    }
}
