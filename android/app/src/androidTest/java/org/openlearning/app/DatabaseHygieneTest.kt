package org.openlearning.app

import android.content.Context
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.test.core.app.ApplicationProvider
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test

/** Written to run on a device (WAL, secure_delete and SQL behaviour need real SQLite); compile-checked in CI. */
class DatabaseHygieneTest {
    private val context = ApplicationProvider.getApplicationContext<Context>()
    private lateinit var db: LearningDatabase
    private val dao get() = db.dao()

    @Before fun open() {
        context.deleteDatabase(NAME)
        // Force WAL: a low-RAM device would otherwise use TRUNCATE journaling and the WAL checks would prove nothing.
        db = Room.databaseBuilder(context, LearningDatabase::class.java, NAME)
            .setJournalMode(RoomDatabase.JournalMode.WRITE_AHEAD_LOGGING).addCallback(SecureDeleteCallback).build()
    }
    @After fun close() { db.close(); context.deleteDatabase(NAME) }

    private fun note(id: String, status: String, text: String? = null, scope: String = "s", jobId: String? = null) =
        PendingIngest(scope, id, "", jobId, status, kind = "note", title = "Title $id", text = text)

    @Test fun secureDeleteIsOnAfterOpen() = runBlocking {
        dao.putDraft(NoteDraft("s", "t", "l", "x"))
        db.openHelper.writableDatabase.query("PRAGMA secure_delete").use { assertTrue(it.moveToFirst()); assertEquals(1, it.getInt(0)) }
    }

    @Test fun checkpointTruncatesTheWalToZeroBytes() = runBlocking {
        val wal = context.getDatabasePath("$NAME-wal")
        repeat(20) { dao.putDraft(NoteDraft("s", "t", "l", "SENTINEL-NOTE-TEXT $it")) }
        dao.deleteDraft("s")
        assertTrue("WAL should hold frames before the checkpoint", wal.exists() && wal.length() > 0)
        db.checkpoint() // throws when the checkpoint is busy, so reaching the assert means it ran
        assertEquals(0L, wal.length())
    }

    @Test fun completedNoteRowsGoButActiveAndUrlRowsStay() = runBlocking {
        dao.putJob(note("done", "completed")); dao.putJob(note("done2", "Done")); dao.putJob(note("other", "completed", scope = "t"))
        dao.putJob(note("queued", "queued", text = "body")); dao.putJob(note("failed", "failed", text = "body"))
        dao.putJob(PendingIngest("s", "url", "https://a.example", status = "completed"))
        assertEquals(2, dao.deleteCompletedNotes("s", COMPLETED_STATES))
        val left = listOf("done", "done2", "other", "queued", "failed", "url").filter { dao.job(if (it == "other") "t" else "s", it) != null }
        assertEquals(listOf("other", "queued", "failed", "url"), left)
    }

    @Test fun pruneKeepsOnlySavedOrFeedCards() = runBlocking {
        dao.put(listOf(CachedInsight("s", "feed", "{}", false, true), CachedInsight("s", "saved", "{}", true, false),
            CachedInsight("s", "stale", "{}", false, false), CachedInsight("t", "stale", "{}", false, false)))
        assertEquals(1, dao.pruneCards("s"))
        assertNull(dao.get("s", "stale")); assertNotNull(dao.get("s", "feed")); assertNotNull(dao.get("s", "saved")); assertNotNull(dao.get("t", "stale"))
    }

    private fun connection(name: String) = Connection("https://example.invalid/", "token-$name")
    private fun repositoryFor(name: String) = LearningRepository(connection(name), db)
    private fun scopeOf(name: String) = connection(name).scope

    @Test fun purgeKeepsTextHoldingRowsAndDraftsOfOtherScopes() = runBlocking {
        val s = scopeOf("s"); val old = scopeOf("old")
        dao.put(listOf(CachedInsight(s, "a", "{}", true, false), CachedInsight(old, "b", "{}", true, false)))
        dao.putDraft(NoteDraft(s, "t", "l", "keep")); dao.putDraft(NoteDraft(old, "t", "l", "UNSENT"))
        dao.putJob(note("mine", "queued", "body", scope = s))
        dao.putJob(note("q", "queued", "UNSENT", scope = old)); dao.putJob(note("f", "failed", "UNSENT", scope = old))
        dao.putJob(note("done", "completed", scope = old)); dao.putJob(PendingIngest(old, "url", "https://a.example", status = "Completed"))
        assertEquals(setOf("done", "url"), repositoryFor("s").purgeInactiveScopes().map { it.id }.toSet())
        assertNull(dao.get(old, "b")); assertNotNull(dao.get(s, "a"))
        assertNotNull(dao.draft(old)); assertNotNull(dao.draft(s))
        assertNotNull(dao.job(old, "q")); assertNotNull(dao.job(old, "f")); assertNotNull(dao.job(s, "mine"))
        assertNull(dao.job(old, "done")); assertNull(dao.job(old, "url"))
    }

    @Test fun mistypedTokenAToBToAKeepsUnsentNoteText() = runBlocking {
        val a = scopeOf("a")
        dao.putDraft(NoteDraft(a, "t", "l", "DRAFT")); dao.putJob(note("n", "waiting", "UNSENT", scope = a))
        repositoryFor("b").purgeInactiveScopes()
        assertEquals("UNSENT", dao.job(a, "n")?.text); assertEquals("DRAFT", dao.draft(a)?.text)
        repositoryFor("a").purgeInactiveScopes()
        assertEquals("UNSENT", dao.job(a, "n")?.text); assertEquals("DRAFT", dao.draft(a)?.text)
    }

    @Test fun aPurgeThatIsNoLongerCurrentChangesNothing() = runBlocking {
        val a = scopeOf("a")
        dao.put(listOf(CachedInsight(a, "x", "{}", true, false)))
        assertEquals(emptyList<PendingIngest>(), repositoryFor("b").purgeInactiveScopes { false })
        assertNotNull(dao.get(a, "x"))
    }

    @Test fun otherHoldersCountsRowsSharingAServerJob() = runBlocking {
        dao.putJob(note("a", "queued", "x", jobId = "srv")); dao.putJob(note("b", "queued", "y", jobId = "srv")); dao.putJob(note("c", "queued", "z", jobId = "other"))
        assertEquals(1, dao.otherHolders("srv", "s", "a"))
        assertEquals(0, dao.otherHolders("other", "s", "c"))
    }

    @Test fun updateJobDoesNotResurrectARemovedRow() = runBlocking {
        val row = note("gone", "queued", "body")
        assertEquals(0, dao.updateJob(row))
        assertNull(dao.job("s", "gone"))
    }

    private companion object { const val NAME = "hygiene-test.db" }
}
