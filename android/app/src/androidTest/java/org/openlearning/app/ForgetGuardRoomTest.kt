package org.openlearning.app

import android.content.Context
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.test.core.app.ApplicationProvider
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test

/**
 * The durable guard against real SQLite: the seven interleavings of a forget with a stale cache write. Compile-checked in
 * CI; NOT run on a device. The gates stand in for network answers only: nothing waits inside a transaction.
 */
class ForgetGuardRoomTest {
    private val context = ApplicationProvider.getApplicationContext<Context>()
    private lateinit var db: LearningDatabase
    private val dao get() = db.dao()
    private val scheduled = mutableListOf<String>()
    private var now = 1_000L
    private val connection = Connection("https://example.invalid/", "token-guard")
    private val scope = connection.scope
    private lateinit var queue: ForgetQueue
    private lateinit var repo: LearningRepository

    @Before fun setUp() {
        context.deleteDatabase(NAME)
        db = newDb(); queue = queueOn(db); repo = LearningRepository(connection, db)
    }
    @After fun close() { db.close(); context.deleteDatabase(NAME) }

    private fun newDb() = Room.databaseBuilder(context, LearningDatabase::class.java, NAME)
        .setJournalMode(RoomDatabase.JournalMode.WRITE_AHEAD_LOGGING).addCallback(SecureDeleteCallback).build()
    private fun queueOn(database: LearningDatabase) = ForgetQueue(database, { _, id, _ -> scheduled += id }, { _, _ -> }, clock = { now })
    private fun card(id: String, doc: String) = Insight(id = id, documentId = doc)
    private suspend fun ids() = dao.allInsights(scope).map { it.id }

    // 1. Forget, then a response that was in flight lands.
    @Test fun forgetThenStaleWriteLeavesNoCard() = runBlocking {
        val gate = CompletableDeferred<List<Insight>>()
        val refresh = launch { repo.writeCards(gate.await(), CardList.FEED) }
        queue.forgetNoteOffline(scope, "d1")
        gate.complete(listOf(card("a", "d1"), card("b", "d2")))
        refresh.join()
        assertEquals(listOf("b"), ids())
    }

    // 2. Write, then forget: the forget purges what the write left.
    @Test fun writeThenForgetPurges() = runBlocking {
        repo.writeCards(listOf(card("a", "d1"), card("b", "d2")), CardList.FEED)
        assertEquals(listOf("a"), queue.forgetNoteOffline(scope, "d1"))
        assertEquals(listOf("b"), ids())
    }

    // 3. Confirm, then a stale write: the confirmed row keeps refusing the cards.
    @Test fun confirmThenStaleWriteLeavesNoCard() = runBlocking {
        queue.forgetNoteOffline(scope, "d1")
        queue.confirmed(scope, "d1")
        repo.writeCards(listOf(card("a", "d1")), CardList.LIBRARY)
        assertTrue(ids().isEmpty())
        assertEquals(FORGET_CONFIRMED, dao.forget(scope, "d1")?.status)
    }

    // 4. Simulated restart: a new database handle, queue and repository over the same file, then a stale write.
    @Test fun afterARestartAStaleWriteStillLeavesNoCard() = runBlocking {
        queue.forgetNoteOffline(scope, "d1")
        queue.confirmed(scope, "d1")
        db.close()
        db = newDb()
        val freshQueue = queueOn(db); val freshRepo = LearningRepository(connection, db)
        freshRepo.writeCards(listOf(card("a", "d1"), card("b", "d2")), CardList.FEED)
        assertEquals(listOf("b"), ids())
        assertEquals(setOf("d1"), freshQueue.hidden(scope))
    }

    // 5. Reconcile inside the TTL, and on an incomplete page, keeps the confirmed row.
    @Test fun reconcileInsideTheTtlOrOnAnIncompletePageKeeps() = runBlocking {
        queue.forgetNoteOffline(scope, "d1"); queue.confirmed(scope, "d1")
        queue.reconcile(scope, emptySet(), complete = true, requestStartedAt = now + FORGET_CONFIRMED_TTL_MS - 1)
        assertEquals(setOf("d1"), queue.hidden(scope))
        queue.reconcile(scope, emptySet(), complete = false, requestStartedAt = now + FORGET_CONFIRMED_TTL_MS * 2)
        assertEquals(setOf("d1"), queue.hidden(scope))
        queue.reconcile(scope, emptySet(), complete = true, requestStartedAt = now + FORGET_CONFIRMED_TTL_MS)
        assertTrue(queue.hidden(scope).isEmpty())
    }

    // 6. Confirmed and still listed after the TTL: back to pending, and enqueued again.
    @Test fun confirmedAndStillListedAfterTheTtlFlipsToPending() = runBlocking {
        queue.forgetNoteOffline(scope, "d1"); queue.confirmed(scope, "d1"); scheduled.clear()
        now += FORGET_CONFIRMED_TTL_MS
        queue.reconcile(scope, setOf("d1"), complete = true, requestStartedAt = now)
        assertEquals(FORGET_PENDING, dao.forget(scope, "d1")?.status)
        assertEquals(now, dao.forget(scope, "d1")?.createdAt)
        assertEquals(listOf("d1"), scheduled)
    }

    // 7. forgetsOutside (the notice, the purge of other accounts) never reports a confirmed row.
    @Test fun forgetsOutsideExcludesConfirmedRows() = runBlocking {
        dao.putForget(PendingForget("t", "p", 1, FORGET_PENDING)); dao.putForget(PendingForget("t", "f", 1, FORGET_FAILED, FORGET_REJECTED_ERROR))
        dao.confirmForget(PendingForget("t", "c", 1, FORGET_CONFIRMED))
        assertEquals(setOf("p", "f"), dao.forgetsOutside(scope).map { it.documentId }.toSet())
        repo.purgeInactiveScopes()
        assertEquals(3, dao.forgets("t").size)
    }

    @Test fun confirmingARowThatWentMissingStillRecordsIt() = runBlocking {
        queue.confirmed(scope, "d9")
        assertEquals(FORGET_CONFIRMED, dao.forget(scope, "d9")?.status)
    }

    // A delayed import completing for a forgotten note is deleted, not published.
    @Test fun completedImportOfAForgottenNoteIsDeletedNotPublished() = runBlocking {
        val running = PendingIngest(scope, "j1", "", kind = "note", status = "running", title = "Secret title")
        dao.putJob(running)
        queue.forgetNoteOffline(scope, "d1")
        val changed = db.updateJobUnlessHidden(running.copy(status = "completed", documentId = "d1"))
        assertEquals(0, changed); assertNull(dao.job(scope, "j1"))
        val other = PendingIngest(scope, "j2", "", kind = "note", status = "running")
        dao.putJob(other)
        assertEquals(1, db.updateJobUnlessHidden(other.copy(status = "completed", documentId = "d2")))
        assertEquals("completed", dao.job(scope, "j2")?.status)
    }

    // The update's transaction has read the hidden set (empty) and is held open by the gate; a forget starts meanwhile. The
    // forget cannot commit inside that window, so it runs after the update and purges the linked row. Read-then-write without one
    // transaction would let the forget commit in the gap and the late write would keep the row. Compile-checked, NOT run on a
    // device, and it cannot prove Room's lock order, only that the window is held open by the hook.
    @Test fun forgetStartingInsideTheUpdateWindowStillDeletesTheRow() = runBlocking {
        val processing = PendingIngest(scope, "j3", "", kind = "note", status = "processing", title = "Secret title")
        dao.putJob(processing)
        val completed = processing.copy(status = "completed", documentId = "d1")
        val inWindow = CompletableDeferred<Unit>(); val release = CompletableDeferred<Unit>()
        val update = launch(Dispatchers.IO) { db.updateJobUnlessHidden(completed) { inWindow.complete(Unit); release.await() } }
        inWindow.await()
        val forget = launch(Dispatchers.IO) { queue.forgetNoteOffline(scope, "d1") }
        delay(200)
        release.complete(Unit)
        update.join(); forget.join()
        assertNull(dao.job(scope, "j3"))
        assertEquals(setOf("d1"), queue.hidden(scope))
    }

    // The manual retry goes through the guarded path: a row of a forgotten document is deleted, not updated. Compile-checked.
    @Test fun manualRetryOfAForgottenNoteRowIsDeleted() = runBlocking {
        val failed = PendingIngest(scope, "j4", "", kind = "note", status = "failed", documentId = "d1", title = "Secret title")
        dao.putJob(failed)
        queue.forgetNoteOffline(scope, "d1")
        dao.putJob(failed)
        assertEquals(0, db.updateJobUnlessHidden(failed.copy(status = "queued")))
        assertNull(dao.job(scope, "j4"))
    }

    @Test fun cardFlagsProjectsOnlyTheFeedFlag() = runBlocking {
        repo.writeCards(listOf(card("a", "d1")), CardList.FEED); repo.writeCards(listOf(card("b", "d2")), CardList.LIBRARY)
        assertEquals(mapOf("a" to true, "b" to false), dao.cardFlags(scope).associate { it.id to it.inFeed })
    }

    private companion object { const val NAME = "forget-guard-test.db" }
}
