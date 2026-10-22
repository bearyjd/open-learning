package org.openlearning.app

import android.content.Intent
import android.view.WindowManager
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.test.core.app.ActivityScenario
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.work.WorkManager
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

/**
 * E2b-2 notes list, targeted forget and share-as-note against a device-local fixture.
 * Compiled in CI; not run on a device. Run on the .acceptance app only.
 */
@RunWith(AndroidJUnit4::class)
class NotesListAcceptanceTest {
    @get:Rule val compose = createEmptyComposeRule()
    private val app: LearningApplication get() = ApplicationProvider.getApplicationContext()
    private val container get() = app.container
    private lateinit var fixture: ListFixture
    private var scenario: ActivityScenario<MainActivity>? = null

    @Before fun prepare() {
        check(app.packageName == "org.openlearning.app.acceptance") { "Refusing to reset a non-acceptance app" }
        WorkManager.getInstance(app).cancelAllWork().result.get(10, TimeUnit.SECONDS)
        io { container.database.clearAllTables() }
        fixture = ListFixture().also { it.start() }
        container.credentials.save(fixture.server.url("/").toString(), "alpha", true)
        io {
            val scope = scope()
            container.database.dao().putJob(PendingIngest(scope, "row-a", "", kind = "note", status = "completed", title = "Alpha note", documentId = "doc-a"))
            container.database.dao().putJob(PendingIngest(scope, "row-b", "", kind = "note", status = "completed", title = "Beta note", documentId = "doc-b"))
        }
    }

    @After fun cleanUp() {
        check(app.packageName == "org.openlearning.app.acceptance") { "Refusing to reset a non-acceptance app" }
        scenario?.close()
        WorkManager.getInstance(app).cancelAllWork().result.get(10, TimeUnit.SECONDS)
        fixture.server.shutdown()
    }

    @Test fun listShowsTitleLabelCountAndDate() {
        openList()
        waitText("Alpha note"); waitText("Beta note")
        compose.onNodeWithText("Lecture · 1,234 characters · 2026-10-22").assertExists()
    }

    @Test fun forgetCancelSendsNoDelete() {
        openList(); waitText("Alpha note")
        compose.onAllNodesWithText("Forget")[0].performClick()
        waitText("Forget this note?")
        compose.onNodeWithText("Cancel").performClick()
        assertEquals(0, fixture.count("DELETE", "/api/v1/notes/doc-a"))
        assertEquals(2, rows().size)
    }

    @Test fun forgetConfirmDeletesOneNoteAndKeepsTheOthersImportRow() {
        openList(); waitText("Alpha note")
        compose.onAllNodesWithText("Forget")[0].performClick()
        waitText("Forget this note?")
        confirm()
        waitUntil { fixture.count("DELETE", "/api/v1/notes/doc-a") == 1 }
        waitUntil { rows().map { it.documentId } == listOf("doc-b") }
        waitUntil { compose.onAllNodesWithText("Alpha note").fetchSemanticsNodes().isEmpty() }
        compose.onNodeWithText("Beta note").assertExists()
        assertEquals(0, fixture.count("DELETE", "/api/v1/notes/doc-b"))
    }

    @Test fun failedDeleteKeepsRowsAndTheListEntry() {
        fixture.deleteStatus.set(500)
        openList(); waitText("Alpha note")
        compose.onAllNodesWithText("Forget")[0].performClick()
        waitText("Forget this note?")
        confirm()
        waitUntil { compose.onAllNodesWithText("was not forgotten", substring = true).fetchSemanticsNodes().isNotEmpty() }
        assertEquals(2, rows().size)
        compose.onNodeWithText("Alpha note").assertExists()
    }

    @Test fun offlineDeleteKeepsRows() {
        openList(); waitText("Alpha note")
        fixture.down.set(true)
        compose.onAllNodesWithText("Forget")[0].performClick()
        waitText("Forget this note?")
        confirm()
        waitUntil { compose.onAllNodesWithText("was not forgotten", substring = true).fetchSemanticsNodes().isNotEmpty() }
        assertEquals(2, rows().size)
    }

    // NOT device-verified. FLAG_SECURE is set while the list is shown and cleared again after it is closed.
    @Test fun listIsFlagSecureOnlyWhileShown() {
        openList(); waitText("Alpha note")
        assertTrue(secure())
        compose.onNodeWithText("Close").performClick()
        waitUntil { !secure() }
    }

    @Test fun sharedTextOpensTheEditorPrefilledAndSubmitsNothing() {
        val text = "Spaced practice revisits an idea after a delay. ".repeat(4)
        scenario = ActivityScenario.launch(Intent(app, MainActivity::class.java).setAction(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, text))
        waitText("Write a note.")
        compose.onNodeWithText("Note text").assertTextContains(text.trim(), substring = true)
        Thread.sleep(1_500)
        assertEquals(0, fixture.count("POST", "/api/v1/ingest/text"))
    }

    private fun openList() {
        scenario = ActivityScenario.launch(MainActivity::class.java)
        waitText("Your notes")
        compose.onNode(hasScrollToIndexAction()).performScrollToNode(hasText("Your notes"))
        compose.onNodeWithText("Your notes").performClick()
    }
    private fun confirm() = compose.onNode(hasText("Forget") and hasAnyAncestor(isDialog())).performClick()
    private fun secure(): Boolean {
        var flagged = false
        scenario!!.onActivity { flagged = it.window.attributes.flags and WindowManager.LayoutParams.FLAG_SECURE != 0 }
        return flagged
    }
    private fun scope() = checkNotNull(container.credentials.connection.value).scope
    private fun rows(): List<PendingIngest> = io { container.database.dao().jobs(scope()).first() }.sortedBy { it.id }
    private fun waitText(text: String) = waitUntil { compose.onAllNodesWithText(text).fetchSemanticsNodes().isNotEmpty() }
    private fun waitUntil(timeout: Long = 15_000, condition: () -> Boolean) = compose.waitUntil(timeout, condition)
    private fun <T> io(block: suspend () -> T): T = runBlocking(Dispatchers.IO) { block() }
}

/** GET /notes, GET /limits and DELETE /notes/{id}. Records only method and path, never bodies. */
private class ListFixture {
    val server = MockWebServer()
    val requests = CopyOnWriteArrayList<RecordedRequest>()
    val down = AtomicBoolean(false)
    val deleteStatus = AtomicInteger(204)
    private val deleted = CopyOnWriteArrayList<String>()
    private val notes = mapOf(
        "doc-a" to """{"document_id":"doc-a","title":"Alpha note","label":"Lecture","created_at":"2026-10-22T09:30:00Z","char_count":1234}""",
        "doc-b" to """{"document_id":"doc-b","title":"Beta note","label":null,"created_at":"2026-10-21T08:00:00Z","char_count":456}""",
    )

    fun start() {
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                if (down.get()) return MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AT_START)
                requests += request
                if (request.getHeader("Authorization") != "Bearer alpha") return MockResponse().setResponseCode(401)
                val path = request.path.orEmpty().substringBefore('?')
                return when {
                    request.method == "GET" && path == "/api/v1/notes" -> json("[" + notes.filterKeys { it !in deleted }.values.joinToString(",") + "]")
                    request.method == "GET" && path == "/api/v1/limits" -> json("""{"max_document_chars":60000,"min_text_chars":100,"max_title_chars":100,"max_label_chars":100}""")
                    request.method == "DELETE" && path.startsWith("/api/v1/notes/") -> {
                        if (deleteStatus.get() in 200..299) deleted += path.substringAfterLast('/')
                        MockResponse().setResponseCode(deleteStatus.get())
                    }
                    path == "/api/v1/feed/today" || path == "/api/v1/library" -> json("[]")
                    else -> MockResponse().setResponseCode(404)
                }
            }
        }
        server.start()
    }
    private fun json(body: String) = MockResponse().addHeader("Content-Type", "application/json").setBody(body)
    fun count(method: String, path: String) = requests.count { it.method == method && it.path?.substringBefore('?') == path }
}
