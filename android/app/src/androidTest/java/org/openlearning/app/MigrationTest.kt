package org.openlearning.app

import androidx.room.testing.MigrationTestHelper
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test

class MigrationTest {
    @get:Rule val helper = MigrationTestHelper(InstrumentationRegistry.getInstrumentation(), LearningDatabase::class.java)

    @Test fun migrate1To2KeepsRowsAndAddsNoteColumns() {
        helper.createDatabase(DB, 1).apply {
            execSQL("INSERT INTO CachedInsight VALUES('s','i1','{}',1,0)")
            execSQL("INSERT INTO PendingIngest VALUES('s','p1','https://a.example','job-1','completed',NULL)")
            close()
        }
        val db = helper.runMigrationsAndValidate(DB, 2, true, MIGRATION_1_2)
        db.query("SELECT url, jobId, status, kind, title, label, text FROM PendingIngest").use {
            assertTrue(it.moveToFirst())
            assertEquals("https://a.example", it.getString(0))
            assertEquals("job-1", it.getString(1))
            assertEquals("completed", it.getString(2))
            assertEquals("url", it.getString(3))
            assertTrue(it.isNull(4) && it.isNull(5) && it.isNull(6))
        }
        db.query("SELECT COUNT(*) FROM CachedInsight").use { it.moveToFirst(); assertEquals(1, it.getInt(0)) }
        db.execSQL("INSERT INTO NoteDraft VALUES('s','t','l','body')")
        db.query("SELECT COUNT(*) FROM NoteDraft").use { it.moveToFirst(); assertEquals(1, it.getInt(0)) }
    }
    @Test fun migrate2To3AddsDocumentIdAndKeepsUnsentNotes() {
        helper.createDatabase(DB, 2).apply {
            execSQL("INSERT INTO PendingIngest VALUES('s','n1','','job-1','queued',NULL,'note','T','L','UNSENT-BODY')")
            execSQL("INSERT INTO PendingIngest VALUES('s','n2','','job-2','completed',NULL,'note','Done',NULL,NULL)")
            execSQL("INSERT INTO NoteDraft VALUES('s','t','l','draft body')")
            close()
        }
        val db = helper.runMigrationsAndValidate(DB, 3, true, MIGRATION_2_3)
        db.query("SELECT id, text, documentId FROM PendingIngest ORDER BY id").use {
            assertTrue(it.moveToFirst()); assertEquals("UNSENT-BODY", it.getString(1)); assertTrue(it.isNull(2))
            assertTrue(it.moveToNext()); assertTrue(it.isNull(1) && it.isNull(2))
        }
        db.query("SELECT text FROM NoteDraft").use { assertTrue(it.moveToFirst()); assertEquals("draft body", it.getString(0)) }
        db.execSQL("INSERT INTO PendingForget VALUES('s','doc-1',1,'pending',NULL)")
        db.query("SELECT COUNT(*) FROM PendingForget").use { it.moveToFirst(); assertEquals(1, it.getInt(0)) }
    }
    @Test fun migrate1To3RunsBothMigrations() {
        helper.createDatabase(DB, 1).apply {
            execSQL("INSERT INTO PendingIngest VALUES('s','p1','https://a.example','job-1','completed',NULL)")
            close()
        }
        val db = helper.runMigrationsAndValidate(DB, 3, true, MIGRATION_1_2, MIGRATION_2_3)
        db.query("SELECT kind, documentId FROM PendingIngest").use {
            assertTrue(it.moveToFirst()); assertEquals("url", it.getString(0)); assertTrue(it.isNull(1))
        }
        db.execSQL("INSERT INTO NoteDraft VALUES('s','t','l','body')")
        db.execSQL("INSERT INTO PendingForget VALUES('s','doc-1',1,'pending',NULL)")
    }
    private companion object { const val DB = "migration-test.db" }
}
