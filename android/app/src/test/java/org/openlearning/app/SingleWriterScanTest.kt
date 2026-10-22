package org.openlearning.app

import org.junit.Assert.*
import org.junit.Test
import java.io.File

/** A text scan of the sources, NOT a runtime guarantee: it catches the usual ways of calling the DAO, not aliasing or reflection. */
fun writesCards(text: String, writerMethods: Set<String>): Boolean = writerMethods.any { method ->
    Regex("""\bdao\s*(?:\(\s*\))?\s*(?:\.\s*$method\s*\(|::\s*$method\b)""").containsMatchIn(text)
}

/** DAO methods that write CachedInsight rows: @Insert/@Update/@Upsert taking one, or an @Query INSERT/UPDATE/REPLACE of its payload. */
fun cardWriterMethods(daoSource: String): Set<String> {
    val declaration = Regex("""(@(?:Insert|Update|Upsert)\b[^\n]*|@Query\("(?:INSERT|REPLACE)[^\n]*)\n?\s*(?:suspend\s+)?fun\s+(\w+)\s*\(([^)]*)\)""")
    return declaration.findAll(daoSource).filter { "CachedInsight" in it.groupValues[1] + it.groupValues[3] }.map { it.groupValues[2] }.toSet()
}

class SingleWriterScanTest {
    private val names = setOf("put", "upsertAll")
    private fun dir() = File(listOf("src/main/java/org/openlearning/app", "app/src/main/java/org/openlearning/app").first { File(it).isDirectory })
    private fun mainSources() = dir().walkTopDown().filter { it.isFile && it.extension == "kt" }.toList()

    @Test fun plainCallIsFound() { assertTrue(writesCards("dao.put(rows)", names)) }
    @Test fun spacedAndChainedCallsAreFound() {
        assertTrue(writesCards("dao . put (rows)", names))
        assertTrue(writesCards("database.dao().put(rows)", names))
        assertTrue(writesCards("container.database.dao() . put(rows)", names))
        assertTrue(writesCards("dao()\n    .put(rows)", names))
        assertTrue(writesCards("dao ( ) .put(rows)", names))
    }
    @Test fun otherWriterMethodsAreFound() {
        assertTrue(writesCards("dao.upsertAll(rows)", names)); assertTrue(writesCards("repo.dao().upsertAll(rows)", names))
        assertTrue(writesCards("val write = dao::put", names))
    }
    @Test fun unrelatedPutsAreNotFlagged() {
        assertFalse(writesCards("map.put(1, 2); cache.put(k, v); daoFake.put(x)", names))
        assertFalse(writesCards("dao.putJob(row); dao.putDraft(d); dao.putForget(f)", names))
    }
    @Test fun writerMethodsAreDiscoveredFromTheDaoAnnotations() {
        val dao = """
            @Insert(onConflict = OnConflictStrategy.REPLACE) suspend fun put(rows: List<CachedInsight>)
            @Insert(onConflict = OnConflictStrategy.REPLACE) suspend fun putJob(job: PendingIngest)
            @Upsert suspend fun upsertAll(rows: List<CachedInsight>)
            @Update
            suspend fun touch(row: CachedInsight): Int
            @Query("INSERT OR REPLACE INTO CachedInsight SELECT * FROM CachedInsight") suspend fun copyAll()
            @Query("UPDATE CachedInsight SET inFeed = 0 WHERE scope = :scope") suspend fun clearFeed(scope: String)
        """.trimIndent()
        assertEquals(setOf("put", "upsertAll", "touch", "copyAll"), cardWriterMethods(dao))
    }

    @Test fun onlyCardWriterWritesCachedInsights() {
        val files = mainSources()
        assertTrue(files.any { it.name == "CardWriter.kt" })
        val methods = cardWriterMethods(File(dir(), "Data.kt").readText())
        assertTrue("put" in methods)
        val offenders = files.filter { it.name != "CardWriter.kt" && it.name != "Data.kt" && writesCards(it.readText(), methods) }
        assertEquals(emptyList<File>(), offenders)
        assertTrue(writesCards(File(dir(), "CardWriter.kt").readText(), methods))
    }
}
