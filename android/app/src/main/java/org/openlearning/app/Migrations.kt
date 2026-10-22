package org.openlearning.app

import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase

// Never fall back to destructive migration: queued notes live only in this database.
val MIGRATION_1_2 = object : Migration(1, 2) {
    override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL("ALTER TABLE `PendingIngest` ADD COLUMN `kind` TEXT NOT NULL DEFAULT 'url'")
        db.execSQL("ALTER TABLE `PendingIngest` ADD COLUMN `title` TEXT")
        db.execSQL("ALTER TABLE `PendingIngest` ADD COLUMN `label` TEXT")
        db.execSQL("ALTER TABLE `PendingIngest` ADD COLUMN `text` TEXT")
        db.execSQL("CREATE TABLE IF NOT EXISTS `NoteDraft` (`scope` TEXT NOT NULL, `title` TEXT NOT NULL, `label` TEXT NOT NULL, `text` TEXT NOT NULL, PRIMARY KEY(`scope`))")
    }
}

val MIGRATION_2_3 = object : Migration(2, 3) {
    override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL("ALTER TABLE `PendingIngest` ADD COLUMN `documentId` TEXT")
        db.execSQL("CREATE TABLE IF NOT EXISTS `PendingForget` (`scope` TEXT NOT NULL, `documentId` TEXT NOT NULL, `createdAt` INTEGER NOT NULL, `status` TEXT NOT NULL, `error` TEXT, PRIMARY KEY(`scope`, `documentId`))")
    }
}

// Legacy PendingForget ids may be mixed case (the server issues lower-case ids and compares case-sensitively). Rows are lowercased
// in place; rows that collide afterwards are merged by mergeForgetRowsByLowerId, so no hidden note is dropped.
val MIGRATION_3_4 = object : Migration(3, 4) {
    override fun migrate(db: SupportSQLiteDatabase) {
        val rows = db.query("SELECT scope, documentId, createdAt, status, error FROM PendingForget").use { cursor ->
            val found = ArrayList<PendingForget>()
            while (cursor.moveToNext()) found += PendingForget(cursor.getString(0), cursor.getString(1), cursor.getLong(2), cursor.getString(3), if (cursor.isNull(4)) null else cursor.getString(4))
            found
        }
        if (rows.all { it.documentId == it.documentId.lowercase() }) return
        db.execSQL("DELETE FROM PendingForget")
        mergeForgetRowsByLowerId(rows).forEach {
            db.execSQL("INSERT INTO PendingForget (scope, documentId, createdAt, status, error) VALUES (?, ?, ?, ?, ?)", arrayOf<Any?>(it.scope, it.documentId, it.createdAt, it.status, it.error))
        }
    }
}
