package org.openlearning.app

import android.view.View
import androidx.compose.ui.InternalComposeUiApi
import androidx.compose.ui.platform.ViewRootForTest
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.test.core.app.ActivityScenario
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.work.WorkInfo
import androidx.work.WorkManager
import com.google.gson.FieldNamingPolicy
import com.google.gson.GsonBuilder
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import okhttp3.mockwebserver.SocketPolicy
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger

/** E2a note ingestion against a device-local fixture. Run on the .acceptance app only; compiled in CI, run on a device later. */
@RunWith(AndroidJUnit4::class)
class NoteAcceptanceTest {
    @get:Rule val compose = createEmptyComposeRule()
    private val app: LearningApplication get() = ApplicationProvider.getApplicationContext()
    private val container get() = app.container
    private val work get() = WorkManager.getInstance(app)
    private lateinit var fixture: NoteFixture
    private var scenario: ActivityScenario<MainActivity>? = null
    private val body = "Spaced practice revisits an idea after a delay so that recall gets stronger over time. ".repeat(3)

    @Before fun prepare() {
        check(app.packageName == "org.openlearning.app.acceptance") { "Refusing to reset a non-acceptance app" }
        work.cancelAllWork().result.get(10, TimeUnit.SECONDS)
        io { container.database.clearAllTables() }
        fixture = NoteFixture().also { it.start() }
        container.credentials.save(fixture.server.url("/").toString(), "alpha", true)
    }

    @After fun cleanUp() {
        check(app.packageName == "org.openlearning.app.acceptance") { "Refusing to reset a non-acceptance app" }
        scenario?.close()
        work.cancelAllWork().result.get(10, TimeUnit.SECONDS)
        fixture.server.shutdown()
    }

    @Test fun offlineQueuedNotePostsExactlyOnceAndInputCarriesOnlyScopeAndId() {
        fixture.down.set(true)
        launchEditor(); submit("Lecture 3", body)
        val row = awaitRow { it.status == "waiting" }
        assertEquals(1, workInfo(row).size)
        assertEquals(setOf("scope", "id"), IngestWorker.input(row.scope, row.id).keyValueMap.keys)
        assertEquals(0, fixture.count("POST", "/api/v1/ingest/text"))
        fixture.down.set(false)
        awaitRow(45_000) { it.status == "completed" }
        assertEquals(1, fixture.count("POST", "/api/v1/ingest/text"))
    }

    // NOT device-verified. Asserts the note editor's compose view sits under an ancestor flagged NO_EXCLUDE_DESCENDANTS,
    // so View.isImportantForAutofill() is false for it; the compose view's own flag is not what is checked.
    @OptIn(InternalComposeUiApi::class)
    @Test fun noteEditorAncestorIsExcludedFromAutofill() {
        launchEditor(); waitText("Add note to learning feed")
        val host = (compose.onNodeWithText("Note text").fetchSemanticsNode().root as ViewRootForTest).view
        val flagged = generateSequence(host.parent) { it.parent }.filterIsInstance<View>()
            .any { it.importantForAutofill == View.IMPORTANT_FOR_AUTOFILL_NO_EXCLUDE_DESCENDANTS }
        assertTrue("an ancestor of the editor's compose view must be NO_EXCLUDE_DESCENDANTS", flagged)
        assertFalse("compose view must not be important for autofill", host.isImportantForAutofill)
    }

    @Test fun textIsClearedFromRoomAfterCompletion() {
        launchEditor(); submit("Lecture 3", body)
        awaitRow { it.status == "completed" }
        assertNull(io { container.database.dao().jobs(scope()).first().single().text })
    }

    @Test fun serverErrorsMapToFixedMessagesWithoutEchoingTheBody() {
        for ((code, message, status) in listOf(
            Triple(413, "This note is larger than your server accepts.", "failed"),
            Triple(422, "The server rejected it: 100–60,000 chars, no control characters.", "failed"),
            Triple(429, "The server is busy, retrying automatically.", "waiting"),
        )) {
            io { container.database.clearAllTables() }
            fixture.textStatus.set(code)
            scenario?.close()
            launchEditor()
            submit("Title $code", body)
            val row = awaitRow { it.status == status }
            assertEquals(message, row.error)
            compose.onNodeWithText(message).assertExists()
            assertTrue(compose.onAllNodesWithText("SENTINEL-BODY", substring = true).fetchSemanticsNodes().isEmpty())
            work.cancelAllWork().result.get(10, TimeUnit.SECONDS)
        }
    }

    @Test fun completedNoteShowsNoSourceLinksAndForgetCancelDoesNotDelete() {
        completeOneNote()
        compose.onNodeWithText("Your note: Lecture 3").performClick()
        waitText("Forget this note")
        assertTrue(compose.onAllNodesWithText("Open original source", substring = true).fetchSemanticsNodes().isEmpty())
        assertTrue(compose.onAllNodesWithText("Check source", substring = true).fetchSemanticsNodes().isEmpty())
        assertTrue(compose.onAllNodesWithText("Open source article", substring = true).fetchSemanticsNodes().isEmpty())
        compose.onNodeWithText("Forget this note").performClick()
        compose.onNodeWithText("Cancel").performClick()
        assertEquals(0, fixture.count("DELETE", "/api/v1/notes/note-doc"))
        assertEquals(1, io { container.database.dao().allInsights(scope()).size })
    }

    @Test fun forgetConfirmDeletesOnceAndPurgesRoom() {
        completeOneNote()
        compose.onNodeWithText("Your note: Lecture 3").performClick()
        waitText("Forget this note")
        compose.onNodeWithText("Forget this note").performClick()
        compose.onNodeWithText("Forget", substring = false).performClick()
        waitUntil { fixture.count("DELETE", "/api/v1/notes/note-doc") == 1 }
        waitUntil { io { container.database.dao().allInsights(scope()).isEmpty() } }
        waitText("Note forgotten.")
    }

    @Test fun offlineForgetKeepsEverything() {
        completeOneNote()
        compose.onNodeWithText("Your note: Lecture 3").performClick()
        waitText("Forget this note")
        fixture.down.set(true)
        compose.onNodeWithText("Forget this note").performClick()
        compose.onNodeWithText("Forget", substring = false).performClick()
        waitUntil { compose.onAllNodesWithText("was not forgotten", substring = true).fetchSemanticsNodes().isNotEmpty() }
        assertEquals(0, fixture.count("DELETE", "/api/v1/notes/note-doc"))
        assertEquals(1, io { container.database.dao().allInsights(scope()).size })
    }

    @Test fun removingAQueuedNoteBeforeSubmitSendsNothing() {
        fixture.down.set(true)
        launchEditor(); submit("Lecture 3", body)
        awaitRow { it.status == "waiting" }
        compose.onNodeWithText("Remove note").performClick()
        waitUntil { io { container.database.dao().jobs(scope()).first().isEmpty() } }
        fixture.down.set(false)
        Thread.sleep(2_000)
        assertEquals(0, fixture.count("POST", "/api/v1/ingest/text"))
    }

    @Test fun draftSurvivesActivityRecreate() {
        launchEditor()
        compose.onNodeWithText("Title").performTextInput("Draft title")
        compose.onNodeWithText("Note text").performTextInput(body)
        waitUntil { io { container.database.dao().draft(scope())?.text == body } }
        scenario!!.recreate()
        waitText("Draft title")
        compose.onNodeWithText("Note text").assertTextContains(body, substring = true)
    }

    private fun completeOneNote() {
        launchEditor(); submit("Lecture 3", body, label = "Lecture 3")
        awaitRow { it.status == "completed" }
        waitUntil { compose.onAllNodesWithText("Your note: Lecture 3").fetchSemanticsNodes().isNotEmpty() }
    }
    private fun launchEditor() { scenario = ActivityScenario.launch(MainActivity::class.java); openEditor() }
    private fun openEditor() { waitText("Write or import a note"); compose.onNodeWithText("Write or import a note").performClick() }
    private fun submit(title: String, text: String, label: String = "") {
        waitText("Add note to learning feed")
        compose.onNodeWithText("Title").performTextInput(title)
        if (label.isNotEmpty()) compose.onNodeWithText("Label (optional)").performTextInput(label)
        compose.onNodeWithText("Note text").performTextInput(text)
        compose.onNodeWithText("Add note to learning feed").performClick()
    }
    private fun scope() = checkNotNull(container.credentials.connection.value).scope
    private fun awaitRow(timeout: Long = 20_000, predicate: (PendingIngest) -> Boolean): PendingIngest {
        waitUntil(timeout) { io { container.database.dao().jobs(scope()).first().any(predicate) } }
        return io { container.database.dao().jobs(scope()).first().first(predicate) }
    }
    private fun workInfo(row: PendingIngest): List<WorkInfo> = work.getWorkInfosForUniqueWork("ingest-${row.scope}-${row.id}").get(10, TimeUnit.SECONDS)
    private fun waitText(text: String) = waitUntil { compose.onAllNodesWithText(text).fetchSemanticsNodes().isNotEmpty() }
    private fun waitUntil(timeout: Long = 15_000, condition: () -> Boolean) = compose.waitUntil(timeout, condition)
    private fun <T> io(block: suspend () -> T): T = runBlocking(Dispatchers.IO) { block() }
}

private class NoteFixture {
    val server = MockWebServer()
    val requests = CopyOnWriteArrayList<RecordedRequest>()
    val down = AtomicBoolean(false)
    val textStatus = AtomicInteger(200)
    private val gson = GsonBuilder().setFieldNamingPolicy(FieldNamingPolicy.LOWER_CASE_WITH_UNDERSCORES).create()
    private val note = Insight("note-1", "Spacing", "Recall improves with spacing.", "Revisit an idea after a delay.", "", "Lecture 3",
        "https://evil.example/not-a-link", "note-doc", "Spaced practice revisits an idea.", listOf("memory"), false, "full_source", false, "", "note", "Lecture 3")
    private val feedReady = AtomicBoolean(false)

    fun start() {
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                if (down.get()) return MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AT_START)
                requests += request
                if (request.getHeader("Authorization") != "Bearer alpha") return MockResponse().setResponseCode(401)
                val result: Any = when (request.method to request.path) {
                    "POST" to "/api/v1/ingest/text" -> {
                        if (textStatus.get() != 200) return MockResponse().setResponseCode(textStatus.get()).setBody("SENTINEL-BODY")
                        feedReady.set(true); IngestJob("note-job-1", "completed")
                    }
                    "GET" to "/api/v1/feed/today" -> if (feedReady.get()) listOf(note) else emptyList<Insight>()
                    "GET" to "/api/v1/library" -> emptyList<Insight>()
                    "GET" to "/api/v1/insights/note-1" -> note
                    "DELETE" to "/api/v1/notes/note-doc", "DELETE" to "/api/v1/jobs/note-job-1" -> return MockResponse().setResponseCode(204)
                    else -> return MockResponse().setResponseCode(404)
                }
                return MockResponse().addHeader("Content-Type", "application/json").setBody(gson.toJson(result))
            }
        }
        server.start()
    }
    fun count(method: String, path: String) = requests.count { it.method == method && it.path == path }
}
