package org.openlearning.app

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Test

/** Review fixes for the offline forget queue (items 1-13): pure decisions only, no Room or WorkManager. */
class ForgetHardeningTest {
    private fun row(id: String, status: String = FORGET_PENDING) = PendingForget("s", id, 1, status)

    // 3. Document ids are checked before commit; one bad row never stops the others.
    @Test fun documentIdValidation() {
        assertTrue(validForgetDocumentId("123e4567-e89b-12d3-a456-426614174000"))
        assertTrue(validForgetDocumentId("123e4567e89b12d3a456426614174000"))
        assertFalse(validForgetDocumentId(""))
        assertFalse(validForgetDocumentId("a".repeat(65)))
        assertFalse(validForgetDocumentId("a".repeat(20_000)))
        assertFalse(validForgetDocumentId("../etc"))
        assertFalse(validForgetDocumentId("abc def"))
        assertFalse(validForgetDocumentId("xyz"))
    }
    @Test fun invalidIdFailureHasItsOwnFixedMessage() {
        assertEquals(FORGET_INVALID_ID_MESSAGE, ForgetResult.Failed(InvalidForgetIdException()).message())
        assertTrue(FORGET_INVALID_ID_MESSAGE.contains("nothing was queued"))
        assertEquals(FORGET_LOCAL_FAILED_MESSAGE, ForgetResult.Failed(RuntimeException("x")).message())
    }
    @Test fun oneThrowingRowIsMarkedFailedAndTheRestAreScheduled() = runBlocking {
        val scheduled = mutableListOf<String>(); val failed = mutableListOf<String>()
        val count = scheduleEach(listOf(row("a"), row("bad"), row("c")), ForgetTrigger.RESUME,
            { _, id, _ -> if (id == "bad") throw IllegalArgumentException("too big") else scheduled += id }, { failed += it.documentId })
        assertEquals(1, count)
        assertEquals(listOf("a", "c"), scheduled)
        assertEquals(listOf("bad"), failed)
    }
    @Test fun cancellationIsNotSwallowedWhileScheduling() {
        assertThrows(CancellationException::class.java) {
            runBlocking { scheduleEach(listOf(row("a")), ForgetTrigger.RESUME, { _, _, _ -> throw CancellationException("x") }, { }) }
        }
    }

    // 4. The confirmation purge targets only rows linked to the document, never the legacy fallback.
    @Test fun confirmationPurgeLeavesAnUnrelatedLegacyRow() {
        fun r(id: String, doc: String?, title: String) = PendingIngest("s", id, "", kind = "note", status = "completed", documentId = doc, title = title)
        val a = r("A", "d1", "A"); val b = r("B", null, "B")
        assertEquals(listOf("A"), linkedRowsToPurge(listOf(a, b), "d1").map { it.id })
        // The first pass (with the title) may use the fallback; the second must not repeat it.
        assertEquals(listOf("B"), noteRowsToPurge(listOf(b), "d1", null).map { it.id })
        assertEquals(emptyList<PendingIngest>(), linkedRowsToPurge(listOf(b), "d1"))
    }

    // 5. Reviews: hidden documents never show, lookups are bounded, ids are recorded.
    private fun review(id: String, insight: String) = Review(id, insight, "q", "2026-01-01", 1.0)
    @Test fun reviewsOfHiddenDocumentsAreDroppedAndDocumentsRecorded() = runBlocking {
        val view = resolveReviews(listOf(review("r1", "i1"), review("r2", "i2"), review("r3", "i3")), setOf("d1"),
            { if (it.insightId == "i1") "d1" else null }, { if (it.insightId == "i2") "d2" else throw java.io.IOException() })
        assertEquals(listOf("r2"), view.reviews.map { it.id })
        assertEquals(mapOf("i2" to "d2"), view.documents)
    }
    @Test fun noLookupsWhenTheReviewNamesItsDocument() = runBlocking {
        val reviews = listOf(Review("r1", "i1", "q", "2026-01-01", 1.0, documentId = "d1"))
        val view = resolveReviews(reviews, emptySet(), { error("cache") }, { error("network") })
        assertEquals(reviews, view.reviews)
    }
    @Test fun networkLookupsAreCappedAndStopAfterTheFirstFailure() = runBlocking {
        var calls = 0
        val many = (1..10).map { review("r$it", "i$it") }
        val capped = resolveReviews(many, setOf("dx"), { null }, { calls++; "d" }, cap = 3)
        assertEquals(3, calls); assertEquals(3, capped.reviews.size)
        calls = 0
        val offline = resolveReviews(many, setOf("dx"), { null }, { calls++; throw java.io.IOException() }, cap = 5)
        assertEquals(1, calls); assertTrue(offline.reviews.isEmpty())
    }
    @Test fun revealIsRefusedForAHiddenDocument() {
        assertTrue(Insight(id = "i", documentId = "d1").isHiddenBy(setOf("d1")))
        assertFalse(Insight(id = "i", documentId = "d2").isHiddenBy(setOf("d1")))
        assertFalse(Insight(id = "i", documentId = "").isHiddenBy(setOf("")))
    }
    // 6. A saved tutor selection of a hidden document is cleared, not restored.
    @Test fun restoreOfAHiddenDocumentClears() {
        val saved = SavedTutor("s", "i1", false)
        val found = ResolvedInsight.Found(Insight(id = "i1", documentId = "d1"))
        assertEquals(RestoreDecision.Clear, decideRestore(saved, "s", true, null, saved, found, setOf("d1")))
        assertEquals(RestoreDecision.Restore(found.insight), decideRestore(saved, "s", true, null, saved, found, setOf("d2")))
    }

    // 8. After the commit nothing may undo the forget: every step runs, failures are counted.
    @Test fun afterCommitStepsAllRunAndFailuresAreCounted() = runBlocking {
        val ran = mutableListOf<Int>()
        val failures = runBestEffort({ ran += 1; throw IllegalStateException("x") }, { ran += 2 })
        assertEquals(1, failures); assertEquals(listOf(1, 2), ran)
        assertThrows(CancellationException::class.java) { runBlocking { runBestEffort({ throw CancellationException("x") }) } }
        Unit
    }

    // 10. A rejected forget can be abandoned; failed rows say what failed in fixed words.
    @Test fun stopTryingOnlyForFailedRowsAndUnhidesInTheList() {
        assertTrue(canStopTrying(row("a", FORGET_FAILED)))
        assertFalse(canStopTrying(row("a", FORGET_PENDING)))
        val state = NotesListState().refreshed(listOf(NoteSummary("a", "T", null, "d", 1))).forgetSucceeded("a")
        assertEquals(listOf("a"), state.refreshed(listOf(NoteSummary("a", "T", null, "d", 1))).items.map { it.documentId })
        assertTrue(FORGET_STOP_MESSAGE.contains("stays on the server"))
        assertTrue(FORGET_STOP_MESSAGE.contains("reappear"))
    }
    @Test fun everyFailedErrorIsFixedAndTitleFree() {
        for (error in listOf(FORGET_REJECTED_ERROR, FORGET_REFUSED_ERROR, FORGET_GAVE_UP_ERROR, FORGET_SCHEDULE_ERROR)) {
            assertEquals(error, forgetFailedLine(PendingForget("s", "d", 1, FORGET_FAILED, error)))
        }
        assertEquals(FORGET_REFUSED_ERROR, forgetFailedLine(PendingForget("s", "d", 1, FORGET_FAILED, "SENTINEL")))
        assertNotEquals(FORGET_GAVE_UP_ERROR, FORGET_REJECTED_ERROR)
    }

    // 11. Replay table.
    @Test fun replayTreatsGoneAsDoneAndTimeoutsAsRetry() {
        assertEquals(ForgetReplay.Done, forgetReplay(410))
        assertEquals(ForgetReplay.Retry, forgetReplay(408))
        assertEquals(ForgetReplay.Retry, forgetReplay(425))
        assertEquals(ForgetReplay.Failed(FORGET_REFUSED_ERROR), forgetReplay(302))
        assertEquals(ForgetReplay.Failed(FORGET_REFUSED_ERROR), forgetReplay(409))
    }
    @Test fun retriesGiveUpAfterTheAttemptCap() {
        assertEquals(ForgetReplay.Retry, forgetReplay(500, FORGET_MAX_ATTEMPTS - 2))
        assertEquals(ForgetReplay.Failed(FORGET_GAVE_UP_ERROR), forgetReplay(500, FORGET_MAX_ATTEMPTS - 1))
        assertEquals(ForgetReplay.Failed(FORGET_GAVE_UP_ERROR), forgetReplay(null, 500))
        assertEquals(ForgetReplay.Done, forgetReplay(204, 500))
        assertEquals(ForgetReplay.Failed(FORGET_REJECTED_ERROR), forgetReplay(403, 500))
        assertEquals(20, FORGET_MAX_ATTEMPTS)
    }

    // 12. A malformed cached payload must not roll the forget back.
    @Test fun malformedPayloadYieldsNoDocumentId() {
        assertNull(documentIdOfPayload("{not json"))
        assertNull(documentIdOfPayload(""))
        assertNull(documentIdOfPayload("null"))
        assertEquals("d1", documentIdOfPayload("""{"id":"i","document_id":"d1"}"""))
    }

    // 13. The residual is stated in the confirm dialog.
    @Test fun dialogWarnsAboutNotesStillBeingImported() {
        assertTrue(FORGET_NOTE_MESSAGE.contains("A note that is still being imported may reappear."))
    }
}
