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
