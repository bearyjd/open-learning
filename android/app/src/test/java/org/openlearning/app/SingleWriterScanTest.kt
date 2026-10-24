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

    /** Text scan, NOT a runtime guarantee: a direct write to a ScreenState flow is allowed only inside ScreenPublisher.kt. */
    @Test fun onlyThePublisherWritesTheScreenState() {
        val files = mainSources().filter { "ScreenState" in it.readText() || "ScreenPublisher" in it.readText() }
        assertTrue(files.any { it.name == "ScreenPublisher.kt" } && files.any { it.name == "LearningViewModel.kt" } && files.any { it.name == "NotesActions.kt" })
        val names = mainSources().flatMap { screenFlowNames(it.readText()) }.toSet()
        assertTrue("mutable" in names)
        val offenders = files.filter { it.name != "ScreenPublisher.kt" && writesScreenFlow(it.readText(), names) }
        assertEquals(emptyList<File>(), offenders)
        assertTrue(writesScreenFlow(File(dir(), "ScreenPublisher.kt").readText(), names))
    }
    @Test fun screenFlowDeclarationsAreDiscoveredUnderAnyName() {
        assertEquals(setOf("a", "b", "c"), screenFlowNames("private val a = MutableStateFlow(ScreenState())\nval b: MutableStateFlow<ScreenState> = x\nvar c = MutableStateFlow<ScreenState>(ScreenState())"))
        assertEquals(emptySet<String>(), screenFlowNames("val d = MutableStateFlow(0)"))
        assertTrue(writesScreenFlow("screenBox.update { it }", setOf("screenBox")))
        assertFalse(writesScreenFlow("screenBox.value == 1", setOf("screenBox")))
    }

    /** Text scan: only the publisher and the flow collectors in LearningViewModel may use the snapshot paths; `publish(` is gone. */
    @Test fun noOneCallsTheRemovedSnapshotPublish() {
        val offenders = mainSources().filter { it.name != "ScreenPublisher.kt" && Regex("""\.publish\s*[({]""").containsMatchIn(it.readText()) }
        assertEquals(emptyList<File>(), offenders)
        assertTrue(hasBarePublish("publisher.publish { it }")); assertTrue(hasBarePublish("state\n .publish(x)"))
        assertFalse(hasBarePublish("publisher.publishFresh(scope) { it }; publishLocal { it }"))
    }
    @Test fun snapshotFlowPublicationsStayInTheViewModelCollectors() {
        val offenders = mainSources().filter { it.name !in setOf("ScreenPublisher.kt", "LearningViewModel.kt") && "publishFlow" in it.readText() }
        assertEquals(emptyList<File>(), offenders)
    }
    @Test fun localPublicationsCarryNoContentFields() {
        val offenders = mainSources().flatMap { f -> f.readLines().filter { "publishLocal" in it && contentField.containsMatchIn(it) }.map { "${f.name}: $it" } }
        assertEquals(emptyList<String>(), offenders)
        assertTrue(contentField.containsMatchIn("publishLocal { it.copy(selected = item) }"))
        assertTrue(contentField.containsMatchIn("publishLocal { it.copy(results = r) }"))
        assertFalse(contentField.containsMatchIn("publishLocal { it.copy(selected = null, reviews = it.reviews.filterNot { }) }"))
    }

    /** Text scan: a direct DAO write of a job row outside the guarded path (ForgetGuards.kt) and the workers' own rows. */
    @Test fun manualRetryUsesTheGuardedJobUpdate() {
        val vm = File(dir(), "LearningViewModel.kt").readText()
        val retry = vm.substringAfter("private suspend fun retryNote").substringBefore("private suspend fun loadDraft")
        assertTrue("updateJobUnlessHidden" in retry); assertFalse(Regex("""dao\.updateJob\(""").containsMatchIn(retry))
    }
    /** Text scans, NOT runtime proofs: the forget commit is followed by the hook, the hook is the publisher's purge, and only ChatSession bumps. */
    @Test fun forgetCommitIsFollowedByTheImmediatePurgeHook() {
        val queue = File(dir(), "ForgetQueue.kt").readText()
        val forget = queue.substringAfter("suspend fun forgetNoteOffline").substringBefore("suspend fun purgeLocal")
        assertTrue(forget.indexOf("onHidden(") > forget.indexOf("withTransaction"))
        assertTrue(forget.indexOf("onHidden(") < forget.indexOf("return purged"))
        val confirmed = queue.substringAfter("suspend fun confirmed").substringBefore("suspend fun reconcile")
        assertTrue(confirmed.indexOf("onHidden(") > confirmed.indexOf("withTransaction"))
        assertTrue("onHidden = { scope, ids -> publisher.purgeNow(scope, ids); clearDiscovery() }" in File(dir(), "LearningViewModel.kt").readText())
    }
    @Test fun onlyChatSessionOwnsTheChatGeneration() {
        val offenders = mainSources().filter { it.name != "ChatSession.kt" && "chatGeneration" in it.readText() }
        assertEquals(emptyList<File>(), offenders)
        assertTrue("generation++" in File(dir(), "ChatSession.kt").readText())
    }
    @Test fun screenFlowWritesAreFound() {
        assertTrue(writesScreenFlow("mutable.update { it }")); assertTrue(writesScreenFlow("state . update {"))
        assertTrue(writesScreenFlow("mutable.value = ScreenState()")); assertTrue(writesScreenFlow("mutable\n  .value=x"))
        assertFalse(writesScreenFlow("if (mutable.value == x) y; val s = state.value; publisher.publish { it }"))
    }
}

/** Row-content fields a local (snapshot-purged) publication may only reduce: assigning anything but null or the field's own value fails. */
private val contentField = Regex("""\b(selected|results|feed|library|reviews|reviewSource|jobs)\s*=\s*+(?!null\b|it\.\1\b)""")
fun hasBarePublish(text: String) = Regex("""\.\s*publish\s*[({]""").containsMatchIn(text)

/** Names of every `MutableStateFlow<ScreenState>` declared (`val x = MutableStateFlow(ScreenState(...))` or `val x: MutableStateFlow<ScreenState>`). */
fun screenFlowNames(text: String): Set<String> =
    Regex("""(?:val|var)\s+(\w+)\s*(?::\s*MutableStateFlow<ScreenState>|=\s*MutableStateFlow\s*(?:<ScreenState>)?\(\s*ScreenState)""").findAll(text).map { it.groupValues[1] }.toSet()

/** Text scan for writes (`update`, `value =`, `emit`, `tryEmit`) on a discovered ScreenState flow or the usual `mutable`/`state`; reads and `.value ==` are not writes. */
fun writesScreenFlow(text: String, names: Set<String> = emptySet()): Boolean {
    val alternatives = (names + setOf("mutable", "state")).joinToString("|") { Regex.escape(it) }
    return Regex("""\b(?:$alternatives)\s*\.\s*(?:update\b|value\s*=(?!=)|emit\b|tryEmit\b)""").containsMatchIn(text)
}
