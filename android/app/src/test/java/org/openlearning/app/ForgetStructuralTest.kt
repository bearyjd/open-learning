package org.openlearning.app

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.runBlocking
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.*
import org.junit.Test
import retrofit2.HttpException
import retrofit2.Response
import java.io.File

/** Structural fix for offline forget: enqueue policy, review resolution, notice, and the single-writer source scan. */
class ForgetStructuralTest {
    private fun row(id: String, status: String = FORGET_PENDING, scope: String = "s") = PendingForget(scope, id, 1, status)
    private fun review(id: String, insight: String, doc: String = "") = Review(id, insight, "q", "2026-01-01", 1.0, documentId = doc)
    private fun http(code: Int) = HttpException(Response.error<Any>(code, "".toResponseBody()))

    // Enqueue policy table.
    @Test fun enqueuePolicyTable() {
        assertEquals(ForgetEnqueue.REPLACE, forgetEnqueuePolicy(ForgetTrigger.NEW_FORGET))
        assertEquals(ForgetEnqueue.REPLACE, forgetEnqueuePolicy(ForgetTrigger.RETRY))
        assertEquals(ForgetEnqueue.KEEP, forgetEnqueuePolicy(ForgetTrigger.RESUME))
        assertEquals(2, ForgetEnqueue.values().size)
    }
    @Test fun onlyBadInputIsAPermanentSchedulingFailure() {
        assertTrue(scheduleFailureIsPermanent(InvalidForgetIdException()))
        assertTrue(scheduleFailureIsPermanent(IllegalArgumentException("Data too large")))
        assertFalse(scheduleFailureIsPermanent(IllegalStateException("WorkManager not initialised")))
        assertFalse(scheduleFailureIsPermanent(ForgetScheduleTransientException(IllegalArgumentException("x"))))
        assertFalse(scheduleFailureIsPermanent(java.io.IOException("x")))
    }
    @Test fun aTransientSchedulingErrorNeverMarksARowFailed() = runBlocking {
        val failed = mutableListOf<String>()
        val permanent = scheduleEach(listOf(row("a"), row("b"), row("c")), ForgetTrigger.RESUME, { _, id, _ ->
            when (id) { "a" -> throw IllegalStateException("busy"); "b" -> throw InvalidForgetIdException(); else -> Unit }
        }, { failed += it.documentId })
        assertEquals(1, permanent); assertEquals(listOf("b"), failed)
    }
    @Test fun suspendingScheduleIsAwaitedAndCancellationPropagates() {
        assertThrows(CancellationException::class.java) {
            runBlocking { scheduleEach(listOf(row("a")), ForgetTrigger.NEW_FORGET, { _, _, _ -> throw CancellationException("x") }, { }) }
        }
    }
    @Test fun afterCommitStepsAllRunAndFailuresAreCounted() = runBlocking {
        val ran = mutableListOf<Int>()
        val failures = runBestEffort({ ran += 1; throw IllegalStateException("x") }, { ran += 2 })
        assertEquals(1, failures); assertEquals(listOf(1, 2), ran)
    }

    // Reviews: the review's own document id first; blank fails closed while anything is hidden.
    @Test fun reviewDocumentIdNeedsNoLookup() = runBlocking {
        val view = resolveReviews(listOf(review("r1", "i1", "d1"), review("r2", "i2", "d2")), setOf("d1"), { error("cache") }, { error("network") })
        assertEquals(listOf("r2"), view.reviews.map { it.id }); assertEquals(mapOf("i2" to "d2"), view.documents)
    }
    @Test fun blankReviewIdResolvesThroughTheInsightLookup() = runBlocking {
        val view = resolveReviews(listOf(review("r1", "i1"), review("r2", "i2")), setOf("d1"), { null }, { if (it.insightId == "i1") "d1" else "d2" })
        assertEquals(listOf("r2"), view.reviews.map { it.id })
    }
    @Test fun anUnresolvableBlankReviewIsDroppedWhileAnythingIsHidden() = runBlocking {
        val view = resolveReviews(listOf(review("r1", "i1")), setOf("d1"), { null }, { throw java.io.IOException() })
        assertTrue(view.reviews.isEmpty())
    }
    @Test fun a404OnLookupDropsThatReviewButIsNotNetworkDown() = runBlocking {
        var calls = 0
        val view = resolveReviews(listOf(review("r1", "i1"), review("r2", "i2")), setOf("d1"), { null }, { calls++; if (it.insightId == "i1") throw http(404) else "d2" })
        assertEquals(2, calls); assertEquals(listOf("r2"), view.reviews.map { it.id })
    }
    @Test fun anotherHttpErrorStopsFurtherLookups() = runBlocking {
        var calls = 0
        val view = resolveReviews(listOf(review("r1", "i1"), review("r2", "i2")), setOf("d1"), { null }, { calls++; throw http(500) })
        assertEquals(1, calls); assertTrue(view.reviews.isEmpty())
    }
    @Test fun nothingHiddenMeansNothingLooked() = runBlocking {
        val reviews = listOf(review("r1", "i1"))
        assertEquals(reviews, resolveReviews(reviews, emptySet(), { error("c") }, { error("n") }).reviews)
    }
    @Test fun reviewDocumentIdSurvivesAnOldServerAnswer() {
        val old = apiGson.fromJson("""{"id":"r","insight_id":"i","question":"q","due_at":"d","interval_days":1.0}""", Review::class.java)
        assertEquals("", old.docId())
        val new = apiGson.fromJson("""{"id":"r","insight_id":"i","question":"q","due_at":"d","interval_days":1.0,"document_id":"d1"}""", Review::class.java)
        assertEquals("d1", new.docId())
    }

    // Notice.
    @Test fun noticeOnlyForPendingOrFailedRowsOfAnotherScope() {
        assertEquals(FORGET_ELSEWHERE_NOTICE, forgetElsewhereNotice(listOf(row("a", scope = "t")), "s"))
        assertEquals(FORGET_ELSEWHERE_NOTICE, forgetElsewhereNotice(listOf(row("a", FORGET_FAILED, scope = "t")), "s"))
        assertNull(forgetElsewhereNotice(listOf(row("a", FORGET_CONFIRMED, scope = "t")), "s"))
        assertNull(forgetElsewhereNotice(listOf(row("a", scope = "s")), "s"))
        assertNull(forgetElsewhereNotice(emptyList(), "s"))
    }
    @Test fun noticeNeverNamesAServer() {
        assertEquals("Some notes you forgot while connected to another server or token are still waiting to be deleted.", FORGET_ELSEWHERE_NOTICE)
        assertFalse(FORGET_ELSEWHERE_NOTICE.contains("http"))
    }
    @Test fun fixedErrorStringsAreFour() { assertEquals(4, FORGET_FIXED_ERRORS.size) }

    // Source scans: the single-writer rule and the wiring that cannot run on the JVM.
    private fun source(name: String): String {
        val dirs = listOf("src/main/java/org/openlearning/app", "app/src/main/java/org/openlearning/app")
        return File(dirs.first { File(it).isDirectory }, name).readText()
    }
    private fun mainSources(): List<File> {
        val dir = File(listOf("src/main/java", "app/src/main/java").first { File(it).isDirectory })
        return dir.walkTopDown().filter { it.isFile && it.extension == "kt" }.toList()
    }
    @Test fun onlyCardWriterWritesCachedInsights() {
        val files = mainSources()
        assertTrue(files.any { it.name == "CardWriter.kt" })
        val offenders = files.filter { it.name != "CardWriter.kt" && Regex("""\bdao\.put\(""").containsMatchIn(it.readText()) }
        assertEquals(emptyList<File>(), offenders)
        assertTrue(source("CardWriter.kt").contains("dao.put("))
    }
    @Test fun noInMemoryMarkerRemains() {
        val offenders = mainSources().filter { Regex("""ForgottenNotes|markedUntilCommit|takePreviousServer""").containsMatchIn(it.readText()) }
        assertEquals(emptyList<File>(), offenders)
    }
    @Test fun confirmationWritesADurableRowInsteadOfDeleting() {
        val text = source("ForgetQueue.kt")
        val body = text.substringAfter("suspend fun confirmed(").substringBefore("suspend fun markFailed")
        assertTrue(body.contains("confirmForget("))
        assertFalse(body.contains("deleteForget("))
    }
    @Test fun loadingNotesReconcilesTheQueueWithTheServerPage() {
        val text = source("NotesActions.kt")
        assertTrue(text.contains("queue.reconcile("))
        assertTrue(text.contains("serverPage("))
    }
}
