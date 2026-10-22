package org.openlearning.app

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Test

/** Review fixes for the offline forget queue (items 1-13): pure decisions only, no Room or WorkManager. */
class ForgetHardeningTest {
    private fun row(id: String, status: String = FORGET_PENDING) = PendingForget("s", id, 1, status)

    // 1. A late response after the server confirmed must stay hidden until a fresh answer lacks the note.
    @Test fun confirmedMarkerStillBlocksLateResponses() {
        val forgotten = ForgottenNotes()
        forgotten.add("s", "d1"); forgotten.confirmed("s", "d1")
        assertTrue(forgotten.blocks("s", Insight(documentId = "d1")))
    }
    @Test fun markerClearsOnlyWhenAFreshAnswerLacksTheNote() {
        val forgotten = ForgottenNotes()
        forgotten.confirmed("s", "d1")
        forgotten.settle("s", setOf("d1", "d2"))
        assertTrue(forgotten.blocks("s", Insight(documentId = "d1")))
        forgotten.settle("s", setOf("d2"))
        assertFalse(forgotten.blocks("s", Insight(documentId = "d1")))
    }
    @Test fun settleNeverClearsAnUnconfirmedMarkerOrAnotherScope() {
        val forgotten = ForgottenNotes()
        forgotten.add("s", "d1"); forgotten.confirmed("other", "d1")
        forgotten.settle("s", emptySet())
        assertTrue(forgotten.blocks("s", Insight(documentId = "d1")))
        assertTrue(forgotten.blocks("other", Insight(documentId = "d1")))
    }

    // 2. Enqueue policy: an enqueue during a finishing worker must not be lost; the idempotent resume never piles up.
    @Test fun enqueuePolicyByTrigger() {
        assertEquals(ForgetEnqueue.APPEND_OR_REPLACE, forgetEnqueuePolicy(ForgetTrigger.NEW_FORGET))
        assertEquals(ForgetEnqueue.APPEND_OR_REPLACE, forgetEnqueuePolicy(ForgetTrigger.RETRY))
        assertEquals(ForgetEnqueue.KEEP, forgetEnqueuePolicy(ForgetTrigger.RESUME))
    }

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
            { _, id, _ -> if (id == "bad") throw IllegalStateException("too big") else scheduled += id }, { failed += it.documentId })
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
    @Test fun noLookupsWhenNothingIsHidden() = runBlocking {
        val reviews = listOf(review("r1", "i1"))
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
    @Test fun forgettingDropsReviewsByRecordedDocumentAndReviewSource() {
        val state = ScreenState(reviews = listOf(review("r1", "i1"), review("r2", "i2"), review("r3", "i3")),
            reviewDocs = mapOf("i1" to "d1", "i2" to "d2"), reviewSource = Insight(id = "i3", documentId = "d1"), revealed = true)
        val next = state.withoutDocument("d1", emptyList())
        assertEquals(listOf("r2"), next.reviews.map { it.id })
        assertNull(next.reviewSource); assertFalse(next.revealed)
    }

    // 6. A saved tutor selection of a hidden document is cleared, not restored.
    @Test fun restoreOfAHiddenDocumentClears() {
        val saved = SavedTutor("s", "i1", false)
        val found = ResolvedInsight.Found(Insight(id = "i1", documentId = "d1"))
        assertEquals(RestoreDecision.Clear, decideRestore(saved, "s", true, null, saved, found, setOf("d1")))
        assertEquals(RestoreDecision.Restore(found.insight), decideRestore(saved, "s", true, null, saved, found, setOf("d2")))
    }

    // 7. The marker exists only while the commit can still succeed.
    @Test fun markerIsRemovedWhenTheCommitFailsOrIsCancelled() = runBlocking {
        val events = mutableListOf<String>()
        markedUntilCommit({ events += "mark" }, { events += "unmark" }) { events += "commit" }
        assertEquals(listOf("mark", "commit"), events)
        events.clear()
        runCatching { markedUntilCommit({ events += "mark" }, { events += "unmark" }) { throw CancellationException("x") } }
        assertEquals(listOf("mark", "unmark"), events)
        events.clear()
        runCatching { markedUntilCommit({ events += "mark" }, { events += "unmark" }) { throw IllegalStateException("x") } }
        assertEquals(listOf("mark", "unmark"), events)
    }

    // 8. After the commit nothing may undo the forget: every step runs, failures are counted.
    @Test fun afterCommitStepsAllRunAndFailuresAreCounted() {
        val ran = mutableListOf<Int>()
        val failures = runBestEffort({ ran += 1; throw IllegalStateException("x") }, { ran += 2 })
        assertEquals(1, failures); assertEquals(listOf(1, 2), ran)
        assertThrows(CancellationException::class.java) { runBestEffort({ throw CancellationException("x") }) }
    }

    // 9. Token rotation.
    @Test fun rotationNoticeOnlyForTheSameServerWithOtherScopesWaiting() {
        val others = listOf(PendingForget("old", "d1", 1, FORGET_PENDING))
        assertEquals(FORGET_TOKEN_ROTATION_NOTICE, tokenRotationNotice("https://a/", "https://a/", others))
        assertNull(tokenRotationNotice("https://a/", "https://b/", others))
        assertNull(tokenRotationNotice(null, "https://a/", others))
        assertNull(tokenRotationNotice("https://a/", "https://a/", emptyList()))
        assertFalse(FORGET_TOKEN_ROTATION_NOTICE.contains("http"))
        assertEquals("Notes you forgot while signed in with the previous token are still waiting to be deleted from the server. " +
            "Reconnect with that token to finish.", FORGET_TOKEN_ROTATION_NOTICE)
    }

    // 10. A rejected forget can be abandoned; failed rows say what failed in fixed words.
    @Test fun stopTryingOnlyForFailedRowsAndUnhidesInTheList() {
        assertTrue(canStopTrying(row("a", FORGET_FAILED)))
        assertFalse(canStopTrying(row("a", FORGET_PENDING)))
        val state = NotesListState().refreshed(listOf(NoteSummary("a", "T", null, "d", 1))).forgetSucceeded("a").stoppedTrying("a")
        assertFalse("a" in state.gone)
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
