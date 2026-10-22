package org.openlearning.app

import android.content.Context
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.test.core.app.ApplicationProvider
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test

/**
 * PendingForget DAO and ForgetQueue against real SQLite. Compile-checked in CI; not run on a device.
 * No network: replay (DELETE) behaviour is covered by ForgetQueueAcceptanceTest.
 */
class ForgetQueueRoomTest {
    private val context = ApplicationProvider.getApplicationContext<Context>()
    private lateinit var db: LearningDatabase
    private val dao get() = db.dao()
    private val scheduled = mutableListOf<Pair<String, String>>()
    private val cancelled = mutableListOf<Pair<String, String>>()
    private lateinit var queue: ForgetQueue

    @Before fun open() {
        context.deleteDatabase(NAME)
        db = Room.databaseBuilder(context, LearningDatabase::class.java, NAME)
            .setJournalMode(RoomDatabase.JournalMode.WRITE_AHEAD_LOGGING).addCallback(SecureDeleteCallback).build()
        queue = ForgetQueue(db, { scope, id, _ -> scheduled += scope to id }, { scope, id -> cancelled += scope to id }, clock = { 42L })
    }
    @After fun close() { db.close(); context.deleteDatabase(NAME) }

    private fun connection(name: String) = Connection("https://example.invalid/", "token-$name")
    private fun cached(scope: String, id: String, documentId: String, inFeed: Boolean = true, saved: Boolean = false) =
        CachedInsight(scope, id, apiGson.toJson(Insight(id = id, documentId = documentId)), saved, inFeed)

    @Test fun insertIsIdempotentOnThePrimaryKeyAndKeepsTheFirstRow() = runBlocking {
        dao.putForget(PendingForget("s", "d1", 1, FORGET_PENDING))
        dao.setForgetStatus("s", "d1", FORGET_FAILED, FORGET_REJECTED_ERROR)
        dao.putForget(PendingForget("s", "d1", 2, FORGET_PENDING))
        assertEquals(listOf(PendingForget("s", "d1", 1, FORGET_FAILED, FORGET_REJECTED_ERROR)), dao.forgets("s"))
    }

    @Test fun hiddenIdsAreScopedAndDeleteRemovesOnlyThatRow() = runBlocking {
        dao.putForget(PendingForget("s", "d1", 1, FORGET_PENDING)); dao.putForget(PendingForget("s", "d2", 1, FORGET_FAILED))
        dao.putForget(PendingForget("t", "d1", 1, FORGET_PENDING))
        assertEquals(setOf("d1", "d2"), dao.hiddenIds("s").toSet())
        assertEquals(1, dao.pendingForgetCount("s").first())
        assertEquals(listOf("d2"), dao.failedForgets("s").first().map { it.documentId })
        dao.deleteForget("s", "d1")
        assertEquals(setOf("d2"), dao.hiddenIds("s").toSet()); assertEquals(setOf("d1"), dao.hiddenIds("t").toSet())
    }

    @Test fun forgetQueuesPurgesAndSchedulesInOneStep() = runBlocking {
        dao.put(listOf(cached("s", "c1", "d1"), cached("s", "c2", "d2")))
        assertEquals(listOf("c1"), queue.forgetNoteOffline("s", "d1", "Lecture"))
        assertNull(dao.get("s", "c1")); assertNotNull(dao.get("s", "c2"))
        assertEquals(listOf(PendingForget("s", "d1", 42, FORGET_PENDING)), dao.forgets("s"))
        assertEquals(listOf("s" to "d1"), scheduled)
        queue.forgetNoteOffline("s", "d1")
        assertEquals(1, dao.forgets("s").size)
    }

    @Test fun forgettingCancelsAQueuedIngestOfTheSameNoteAndDropsItsText() = runBlocking {
        dao.putJob(PendingIngest("s", "q", "", kind = "note", status = "queued", text = "SENTINEL", documentId = "d1"))
        dao.putJob(PendingIngest("s", "other", "", kind = "note", status = "queued", text = "SENTINEL", documentId = "d2"))
        queue.forgetNoteOffline("s", "d1")
        assertNull(dao.job("s", "q")); assertNotNull(dao.job("s", "other"))
        assertEquals(listOf("s" to "q"), cancelled)
    }

    @Test fun purgeInactiveScopesKeepsPendingForgetRowsOfEveryScope() = runBlocking {
        val other = connection("old").scope; val mine = connection("s")
        dao.putForget(PendingForget(other, "d1", 1, FORGET_PENDING)); dao.putForget(PendingForget(mine.scope, "d2", 1, FORGET_FAILED))
        dao.put(listOf(cached(other, "c", "d1")))
        LearningRepository(mine, db).purgeInactiveScopes()
        assertNull(dao.get(other, "c"))
        assertEquals(1, dao.forgets(other).size); assertEquals(1, dao.forgets(mine.scope).size)
    }

    @Test fun confirmedKeepsADurableRowAndOtherAccountsRowsSurvive() = runBlocking {
        dao.putForget(PendingForget("s", "d1", 1, FORGET_PENDING)); dao.putForget(PendingForget("t", "d1", 1, FORGET_PENDING))
        queue.confirmed("s", "d1")
        assertEquals(listOf(PendingForget("s", "d1", 42, FORGET_CONFIRMED)), dao.forgets("s")); assertEquals(FORGET_PENDING, dao.forget("t", "d1")?.status)
    }

    @Test fun retryReschedulesOnlyFailedRows() = runBlocking {
        dao.putForget(PendingForget("s", "p", 1, FORGET_PENDING)); dao.putForget(PendingForget("s", "f", 1, FORGET_FAILED, FORGET_REJECTED_ERROR))
        queue.retryForget("s", "p"); assertTrue(scheduled.isEmpty())
        queue.retryForget("s", "f")
        assertEquals(listOf("s" to "f"), scheduled)
        assertEquals(FORGET_PENDING, dao.forget("s", "f")?.status); assertNull(dao.forget("s", "f")?.error)
    }

    @Test fun resumePendingSchedulesOnlyThePendingRowsOfThatAccount() = runBlocking {
        dao.putForget(PendingForget("s", "p", 1, FORGET_PENDING)); dao.putForget(PendingForget("s", "f", 1, FORGET_FAILED)); dao.putForget(PendingForget("t", "x", 1, FORGET_PENDING))
        queue.resumePending("s")
        assertEquals(listOf("s" to "p"), scheduled)
    }

    @Test fun hiddenNotesAreFilteredAtReadTimeFromFeedLibrarySearchAndNotesList() = runBlocking {
        val repo = LearningRepository(connection("s"), db); val scope = repo.config.scope
        dao.put(listOf(cached(scope, "feed-hidden", "d1"), cached(scope, "feed-kept", "d2"), cached(scope, "lib-hidden", "d1", inFeed = false, saved = true)))
        // A fresh queue and repository read only Room, as after process death.
        dao.putForget(PendingForget(scope, "d1", 1, FORGET_PENDING))
        assertEquals(listOf("feed-kept"), repo.cached.first().map { it.id })
        val fresh = ForgetQueue(db, { _, _, _ -> }, { _, _ -> })
        val hidden = fresh.hidden(scope)
        assertEquals(setOf("d1"), hidden)
        assertEquals(listOf("b"), listOf(Insight(id = "a", documentId = "d1"), Insight(id = "b", documentId = "d2")).hideForgotten(hidden) { it.documentId }.map { it.id })
        assertEquals(emptySet<String>(), fresh.hiddenIds("other").first())
    }

    @Test fun hiddenNotesAreFilteredFromReviewsUsingTheCachedCard() = runBlocking {
        val repo = LearningRepository(connection("s"), db); val scope = repo.config.scope
        dao.put(listOf(cached(scope, "c-hidden", "d1"), cached(scope, "c-kept", "d2")))
        dao.putForget(PendingForget(scope, "d1", 1, FORGET_PENDING))
        val reviews = listOf(Review("r1", "c-hidden", "q", "", 1.0), Review("r2", "c-kept", "q", "", 1.0))
        assertEquals(listOf("r2"), queue.visibleReviews(repo, reviews).reviews.map { it.id })
    }

    @Test fun anOversizedIdIsRejectedBeforeAnythingIsQueued() = runBlocking {
        try { queue.forgetNoteOffline("s", "a".repeat(20_000)); fail("expected InvalidForgetIdException") } catch (expected: InvalidForgetIdException) { }
        assertTrue(dao.forgets("s").isEmpty()); assertTrue(scheduled.isEmpty())
    }

    @Test fun resumeMarksAThrowingRowFailedAndStillSchedulesTheRest() = runBlocking {
        val throwing = ForgetQueue(db, { scope, id, _ -> if (id == "bad") throw IllegalStateException("too big") else scheduled += scope to id }, { _, _ -> })
        dao.putForget(PendingForget("s", "bad", 1, FORGET_PENDING)); dao.putForget(PendingForget("s", "ok", 1, FORGET_PENDING))
        throwing.resumePending("s")
        assertEquals(listOf("s" to "ok"), scheduled)
        assertEquals(FORGET_SCHEDULE_ERROR, dao.forget("s", "bad")?.error); assertEquals(FORGET_FAILED, dao.forget("s", "bad")?.status)
    }

    @Test fun aSchedulingFailureAfterTheCommitStillCountsAsForgotten() = runBlocking {
        val throwing = ForgetQueue(db, { _, _, _ -> throw IllegalStateException("x") }, { _, _ -> })
        dao.put(listOf(cached("s", "c1", "d1")))
        assertEquals(listOf("c1"), throwing.forgetNoteOffline("s", "d1"))
        assertEquals(FORGET_PENDING, dao.forget("s", "d1")?.status)
    }

    @Test fun confirmationNeverDeletesAnUnrelatedLegacyRow() = runBlocking {
        dao.putJob(PendingIngest("s", "A", "", kind = "note", status = "completed", documentId = "d1", title = "A"))
        dao.putJob(PendingIngest("s", "B", "", kind = "note", status = "completed", documentId = null, title = "B"))
        dao.putForget(PendingForget("s", "d1", 1, FORGET_PENDING))
        queue.confirmed("s", "d1")
        assertEquals(listOf("B"), dao.completedNoteRows("s", COMPLETED_STATES).map { it.id })
    }

    @Test fun stopTryingDeletesOnlyAFailedRow() = runBlocking {
        dao.putForget(PendingForget("s", "p", 1, FORGET_PENDING)); dao.putForget(PendingForget("s", "f", 1, FORGET_FAILED, FORGET_REJECTED_ERROR))
        assertFalse(queue.stopTrying("s", "p")); assertTrue(queue.stopTrying("s", "f"))
        assertEquals(setOf("p"), dao.hiddenIds("s").toSet())
    }

    @Test fun otherScopeRowsAreReportedForTheTokenRotationNotice() = runBlocking {
        dao.putForget(PendingForget("old", "d1", 1, FORGET_PENDING)); dao.putForget(PendingForget("s", "d2", 1, FORGET_PENDING))
        assertEquals(listOf("d1"), queue.otherScopeRows("s").map { it.documentId })
    }

    private companion object { const val NAME = "forget-queue-test.db" }
}
