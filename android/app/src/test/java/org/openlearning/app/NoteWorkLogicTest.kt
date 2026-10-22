package org.openlearning.app

import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.runBlocking
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.*
import org.junit.Test
import retrofit2.HttpException
import retrofit2.Response

class NoteWorkLogicTest {
    private val fresh = PendingIngest("s", "j1", "", kind = "note", title = "T", label = "L", text = "body")
    private fun noteMessage(code: Int) = HttpException(Response.error<Any>(code, "SENTINEL".toResponseBody("application/json".toMediaType()))).noteMessage()
    private val submitted = fresh.copy(jobId = "srv", status = "queued")

    @Test fun completedClearsTextAndAsksForRefresh() {
        val update = noteAfterResponse(fresh, IngestJob("srv", "completed"))
        assertNull(update.row.text); assertEquals("srv", update.row.jobId); assertEquals(WorkOutcome.SUCCESS, update.outcome); assertTrue(update.refresh)
        assertEquals(WorkOutcome.SUCCESS, noteAfterResponse(fresh, IngestJob("srv", "Succeeded")).outcome)
    }
    @Test fun cancelledClearsTextAndFails() {
        val update = noteAfterResponse(submitted, IngestJob("srv", "cancelled"))
        assertNull(update.row.text); assertEquals(WorkOutcome.FAILURE, update.outcome); assertFalse(update.refresh)
    }
    @Test fun failedKeepsTextForRetry() {
        val update = noteAfterResponse(submitted, IngestJob("srv", "failed", "boom"))
        assertEquals("body", update.row.text); assertEquals("boom", update.row.error); assertEquals(WorkOutcome.FAILURE, update.outcome)
    }
    @Test fun runningKeepsTextAndRetries() {
        val update = noteAfterResponse(fresh, IngestJob("srv", "running"))
        assertEquals("body", update.row.text); assertEquals(WorkOutcome.RETRY, update.outcome); assertFalse(update.refresh)
    }
    @Test fun tooBusyRetriesAsWaiting() {
        val update = noteAfterError(fresh, 429, noteMessage(429))
        assertEquals("waiting", update.row.status); assertEquals(WorkOutcome.RETRY, update.outcome); assertEquals("body", update.row.text)
        assertEquals("The server is busy, retrying automatically.", update.row.error)
    }
    @Test fun tooLargeAndRejectedAreFailed() {
        for (code in listOf(413, 422, 400, 401)) {
            val update = noteAfterError(fresh, code, noteMessage(code))
            assertEquals("failed", update.row.status); assertEquals(WorkOutcome.FAILURE, update.outcome)
        }
        assertEquals("This note is larger than your server accepts.", noteAfterError(fresh, 413, noteMessage(413)).row.error)
    }
    @Test fun networkAndServerErrorsRetry() {
        for (code in listOf<Int?>(null, 500, 503)) assertEquals(WorkOutcome.RETRY, noteAfterError(fresh, code, "x").outcome)
    }
    @Test fun goneJobWithoutTextExpires() {
        for (code in listOf(404, 409)) {
            val update = noteAfterError(submitted.copy(text = null), code, "other")
            assertEquals(NOTE_EXPIRED, update.row.error); assertEquals("failed", update.row.status); assertEquals(WorkOutcome.FAILURE, update.outcome)
        }
    }
    @Test fun missingApiOnFirstPostIsNotExpiry() {
        assertEquals("other", noteAfterError(fresh, 404, "other").row.error)
    }
    @Test fun resubmitOnlyForGoneJobWithLocalText() {
        assertTrue(shouldResubmit(submitted, 409)); assertTrue(shouldResubmit(submitted, 404))
        assertFalse(shouldResubmit(fresh, 404)) // POST itself 404: the API is missing
        assertFalse(shouldResubmit(submitted.copy(text = null), 404))
        assertFalse(shouldResubmit(submitted, 500)); assertFalse(shouldResubmit(submitted, null))
    }
    @Test fun zeroRowsNeverResurrectsAndOnlyDeletesJobsThisRunCreated() {
        assertEquals(SaveFollowUp.CONTINUE, afterSave(1, true))
        assertEquals(SaveFollowUp.DROP_AND_DELETE_SERVER_JOB, afterSave(0, true))
        assertEquals(SaveFollowUp.DROP, afterSave(0, false))
    }
    @Test fun sharedServerJobIsNeverDeleted() {
        assertTrue(mayDeleteServerJob("srv", 0)); assertFalse(mayDeleteServerJob("srv", 1)); assertFalse(mayDeleteServerJob(null, 0))
    }
    @Test fun removeIsHiddenOnCompletedNotesAndUrlRows() {
        assertTrue(fresh.canRemove()); assertTrue(fresh.copy(status = "failed").canRemove()); assertTrue(fresh.copy(status = "cancelled").canRemove())
        assertFalse(fresh.copy(status = "completed", text = null).canRemove()); assertFalse(fresh.copy(status = "Done").canRemove())
        assertFalse(PendingIngest("s", "u", "https://a.example").canRemove())
    }
    @Test fun awaitSettledReturnsWhenSettledAndTimesOutOtherwise() = runBlocking {
        assertTrue(awaitSettled(flowOf(listOf(false), listOf(true)), 1_000) { it.all { done -> done } })
        assertTrue(awaitSettled(flowOf(emptyList<Boolean>()), 1_000) { it.all { done -> done } })
        assertFalse(awaitSettled(flow { emit(false); delay(5_000) }, 50) { it })
    }

    @Test fun completedNoteStoresDocumentId() {
        assertEquals("doc-1", noteAfterResponse(fresh, IngestJob("srv", "completed", documentId = "doc-1")).row.documentId)
        assertEquals("doc-0", noteAfterResponse(fresh.copy(documentId = "doc-0"), IngestJob("srv", "completed")).row.documentId)
        assertNull(noteAfterResponse(fresh, IngestJob("srv", "completed", documentId = " ")).row.documentId)
    }
    @Test fun unfinishedNoteIgnoresDocumentId() {
        assertNull(noteAfterResponse(fresh, IngestJob("srv", "running", documentId = "doc-1")).row.documentId)
    }
    private fun done(id: String, doc: String?) = PendingIngest("s", id, "", kind = "note", status = "completed", documentId = doc)
    @Test fun purgeTargetsOnlyRowsLinkedToTheDocument() {
        val rows = listOf(done("a", "doc-1"), done("b", "doc-2"), done("c", null))
        assertEquals(listOf("a"), noteRowsToPurge(rows, "doc-1").map { it.id })
    }
    private fun legacy(id: String, title: String?) = done(id, null).copy(title = title)
    @Test fun mixedStateNeverDeletesUnlinkedRowsWithoutATitleMatch() {
        val rows = listOf(done("b", "doc-2"), legacy("c", "Other"), legacy("d", "Another"))
        assertTrue(noteRowsToPurge(rows, "doc-1").isEmpty())
        assertTrue(noteRowsToPurge(rows, "doc-1", "Mine").isEmpty())
    }
    @Test fun mixedStateDeletesUnlinkedRowsWithTheSameTitle() {
        val rows = listOf(done("a", "doc-1"), done("b", "doc-2"), legacy("c", "Mine"), legacy("d", " Mine "), legacy("e", "Other"))
        assertEquals(listOf("a", "c", "d"), noteRowsToPurge(rows, "doc-1", "Mine").map { it.id })
    }
    @Test fun blankRowTitleMatchesUntitled() {
        val rows = listOf(done("b", "doc-2"), legacy("c", null), legacy("d", "Other"))
        assertEquals(listOf("c"), noteRowsToPurge(rows, "doc-1", "Untitled").map { it.id })
    }
    @Test fun pureLegacyStateDeletesTitleMatchesElseAllUnlinked() {
        val rows = listOf(legacy("c", "Mine"), legacy("d", "Other"))
        assertEquals(listOf("c"), noteRowsToPurge(rows, "doc-1", "Mine").map { it.id })
        assertEquals(listOf("c", "d"), noteRowsToPurge(rows, "doc-1", "Gone title").map { it.id })
        assertEquals(listOf("c", "d"), noteRowsToPurge(rows, "doc-1").map { it.id })
        assertTrue(noteRowsToPurge(emptyList(), "doc-1", "Mine").isEmpty())
    }
    @Test fun noteBackoffStartsAtServerRetryAfter() {
        assertEquals(30L, NOTE_BACKOFF_SECONDS)
        assertEquals(30L, backoffSeconds("note")); assertEquals(10L, backoffSeconds("url"))
    }
}
