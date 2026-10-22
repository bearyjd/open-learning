package org.openlearning.app

import android.content.Intent
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
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/** Runs the shipped UI and persistence/network stack against a device-local HTTP fixture.
 * Only the .acceptance app sandbox is reset. No device connectivity/settings are changed.
 */
@RunWith(AndroidJUnit4::class)
class DeviceAcceptanceTest {
    @get:Rule val compose = createEmptyComposeRule()
    private val app: LearningApplication get() = ApplicationProvider.getApplicationContext()
    private val container get() = app.container
    private val work get() = WorkManager.getInstance(app)
    private lateinit var fixture: LearningFixture
    private var scenario: ActivityScenario<MainActivity>? = null

    @Before fun prepare() {
        check(app.packageName == "org.openlearning.app.acceptance") { "Refusing to reset a non-acceptance app" }
        val display = app.resources.configuration
        android.util.Log.i("OpenLearningAcceptance", "Display ${display.screenWidthDp}x${display.screenHeightDp} dp; orientation=${display.orientation}")
        work.cancelAllWork().result.get(10, TimeUnit.SECONDS)
        io { container.database.clearAllTables() }
        fixture = LearningFixture()
        fixture.start()
        configure("alpha")
    }

    @After fun cleanUp() {
        check(app.packageName == "org.openlearning.app.acceptance") { "Refusing to reset a non-acceptance app" }
        scenario?.closeAfterFinish()
        work.cancelAllWork().result.get(10, TimeUnit.SECONDS)
        if (::fixture.isInitialized) fixture.close()
    }

    @Test fun shareIntentPrefillsArticleWithoutSubmittingIt() {
        launch(Intent(app, MainActivity::class.java).apply {
            action = Intent.ACTION_SEND
            type = "text/plain"
            putExtra(Intent.EXTRA_TEXT, "A useful read: https://example.org/article?topic=memory.")
        })
        waitText("Article URL")
        compose.onNode(hasSetTextAction() and hasText("https://example.org/article?topic=memory"))
            .assertIsDisplayed()
        assertEquals(0, fixture.count("POST", "/api/v1/ingest/url"))
    }

    @Test fun onlineSaveSurvivesOfflineRelaunchAndAccountSwitch() {
        launch()
        scrollTo("Save").performClick()
        waitUntil { io { container.database.dao().get(connection().scope, "idea-1")?.saved == true } }
        navigate("Library")
        waitText(LearningFixture.TITLE)
        assertTrue(fixture.count("POST", "/api/v1/insights/idea-1/save") == 1)
        assertTrue(fixture.requests.all { it.getHeader("Authorization") == "Bearer alpha" })

        // New Activity AND ViewModel, with the fixture socket closed: no in-memory UI cache.
        scenario!!.close()
        fixture.close()
        assertEquals("alpha", Credentials(app).connection.value?.token)
        assertNotEquals("alpha", app.getSharedPreferences("connection", 0).getString("token", null))
        launch()
        navigate("Library")
        waitText(LearningFixture.TITLE)
        compose.onNodeWithText(LearningFixture.TITLE).performClick()
        scrollTo(LearningFixture.EXPLANATION).assertIsDisplayed()
        scenario!!.recreate()
        waitText(LearningFixture.TITLE)

        // Unsent text of alpha must outlive the scope-change purge (cards and completed imports do not).
        val alphaScope = connection().scope
        io { container.database.dao().putDraft(NoteDraft(alphaScope, "Fixture title", "", "fixture unsent text")) }

        // Drive the real connection dialog so account changes exercise the UI and Keystore.
        switchAccount("beta")
        navigate("Library")
        waitText("No saved ideas yet")
        compose.onNodeWithText(LearningFixture.TITLE).assertDoesNotExist()
        // Current policy: connecting another account purges alpha's cached cards, saved ones included.
        waitUntil { io { container.database.dao().allInsights(alphaScope).isEmpty() } }
        assertEquals("fixture unsent text", io { container.database.dao().draft(alphaScope)?.text })

        // Return trip: alpha's saved state lives on the server, so reconnect alpha to a fresh fixture.
        fixture = LearningFixture().also { it.saved.set(true); it.start() }
        configure("alpha")
        navigate("Library")
        waitText(LearningFixture.TITLE)
        assertEquals("fixture unsent text", io { container.database.dao().draft(alphaScope)?.text })
    }

    @Test fun persistedQueuedImportResumesThroughRealWorkManagerWithoutDuplicateSubmission() {
        val config = connection()
        launch(Intent(app, MainActivity::class.java).apply {
            action = Intent.ACTION_SEND
            type = "text/plain"
            putExtra(Intent.EXTRA_TEXT, "https://example.org/queued")
        })
        waitText("Article URL")
        compose.onNodeWithText("Add to learning feed").performClick()
        waitUntil { io { container.database.dao().jobs(config.scope).first().isNotEmpty() } }
        val row = io { container.database.dao().jobs(config.scope).first().single() }
        waitUntil {
            val saved = io { container.database.dao().job(row.scope, row.id) }
            saved?.jobId == "remote-1" && workInfos(row).any { it.state == WorkInfo.State.ENQUEUED && it.runAttemptCount > 0 }
        }
        val originalWork = workInfos(row).single().id
        scenario!!.closeAfterFinish()
        launch() // ViewModel recovers persisted pending rows and schedules the same unique work.
        waitText("Recent imports")
        IngestWorker.schedule(app, row.scope, row.id)
        assertEquals(originalWork, workInfos(row).single().id)
        scenario!!.recreate()
        fixture.completeJob.set(true)
        waitUntil(45_000) { io { container.database.dao().job(row.scope, row.id)?.status == "completed" } }
        waitUntil { workInfos(row).any { it.state == WorkInfo.State.SUCCEEDED } }
        assertEquals(1, fixture.count("POST", "/api/v1/ingest/url"))
        assertTrue(fixture.count("GET", "/api/v1/jobs/remote-1") >= 1)
        assertTrue(fixture.count("GET", "/api/v1/feed/today") >= 1)
    }

    @Test fun lateResponseFromPreviousAccountNeverAppearsInNewAccount() {
        val original = connection()
        fixture.holdAlphaFeed.set(true)
        launch()
        waitUntil { fixture.count("GET", "/api/v1/feed/today") == 1 }
        configure("beta")
        waitText("A different account")
        fixture.releaseAlphaFeed.countDown()
        // Prove A's late response was received and persisted before checking B's UI.
        waitUntil { io { container.database.dao().get(original.scope, "idea-1") != null } }
        compose.onNodeWithText("A different account").assertExists()
        compose.onNodeWithText(LearningFixture.TITLE).assertDoesNotExist()
    }

    @Test fun accountMismatchPausesImportAndOriginalAccountCanRetryWithoutResubmitting() {
        val row = PendingIngest(connection().scope, "paused-local", "https://example.org/paused", "remote-1", "processing")
        io { container.database.dao().putJob(row) }
        configure("beta")
        IngestWorker.schedule(app, row.scope, row.id)
        waitUntil { io { container.database.dao().job(row.scope, row.id)?.status == "paused" } }
        waitUntil { workInfos(row).any { it.state == WorkInfo.State.FAILED } }
        assertEquals(0, fixture.requests.size)
        configure("alpha")
        fixture.completeJob.set(true)
        launch()
        scrollTo("Retry import").performClick()
        waitUntil { io { container.database.dao().job(row.scope, row.id)?.status == "completed" } }
        assertEquals(0, fixture.count("POST", "/api/v1/ingest/url"))
        assertEquals(1, fixture.count("GET", "/api/v1/jobs/remote-1"))
    }

    @Test fun tutorAndReviewUseSourceGroundedServerResponses() {
        fixture.saved.set(true)
        launch()
        navigate("Learn")
        waitText(LearningFixture.TITLE)
        compose.onNodeWithText(LearningFixture.TITLE).performClick()
        waitText("Spacing strengthens recall.")
        scrollTo("Which practice helps recall?").assertIsDisplayed()
        scrollTo("Source evidence for spaced practice.").assertIsDisplayed()
        compose.onNodeWithText("Explain it in your own words").performTextInput("Practice after a delay")
        compose.onNodeWithText("Send").performClick()
        waitUntil { fixture.count("POST", "/api/v1/tutor/chat") == 2 }
        val chat = fixture.requests.last { it.path == "/api/v1/tutor/chat" }.body.clone().readUtf8()
        assertTrue(chat.contains("Practice after a delay"))
        navigate("Review")
        waitText("What makes practice stick?")
        compose.onNodeWithText("Remembered").assertDoesNotExist()
        scrollTo("Show explanation").performClick()
        scrollTo(LearningFixture.EXPLANATION).assertIsDisplayed()
        scrollTo("Remembered").performClick()
        waitText("You're up to date")
        waitText("Next review: 2026-11-01T00:00:00Z")
        assertEquals(1, fixture.count("POST", "/api/v1/reviews/review-1/answer"))
        assertTrue(fixture.requests.last { it.path?.endsWith("/answer") == true }.body.clone().readUtf8().contains("remembered"))
    }

    private fun configure(token: String) = container.credentials.save(fixture.server.url("/").toString(), token, true)
    private fun connection() = checkNotNull(container.credentials.connection.value)
    private fun launch(intent: Intent = Intent(app, MainActivity::class.java)) {
        scenario = ActivityScenario.launch(intent)
    }
    private fun navigate(title: String) {
        compose.onNodeWithText(title, substring = false).performClick()
    }
    private fun switchAccount(token: String) {
        compose.onNodeWithContentDescription("Connection settings").performClick()
        compose.onNodeWithText("Access token").performTextReplacement(token)
        compose.onNodeWithText("Connect", substring = false).performClick()
    }
    private fun waitText(text: String) {
        waitUntil { compose.onAllNodesWithText(text).fetchSemanticsNodes().isNotEmpty() }
    }
    private fun scrollTo(text: String): SemanticsNodeInteraction {
        compose.onNode(hasScrollToIndexAction()).performScrollToNode(hasText(text))
        return compose.onNodeWithText(text)
    }
    private fun waitUntil(timeout: Long = 15_000, condition: () -> Boolean) = compose.waitUntil(timeout, condition)
    private fun workInfos(row: PendingIngest) = work.getWorkInfosForUniqueWork("ingest-${row.scope}-${row.id}").get(10, TimeUnit.SECONDS)
    private fun <T> io(block: suspend () -> T): T = runBlocking(Dispatchers.IO) { block() }
}

/** Strict endpoint fixture: unknown calls fail, requests and submissions remain inspectable. */
private class LearningFixture : AutoCloseable {
    val server = MockWebServer()
    val requests = CopyOnWriteArrayList<RecordedRequest>()
    val saved = AtomicBoolean(false)
    val completeJob = AtomicBoolean(false)
    val holdAlphaFeed = AtomicBoolean(false)
    val releaseAlphaFeed = CountDownLatch(1)
    private val closed = AtomicBoolean(false)
    private val gson = GsonBuilder().setFieldNamingPolicy(FieldNamingPolicy.LOWER_CASE_WITH_UNDERSCORES).create()
    private val insight get() = Insight("idea-1", TITLE, "Recall improves with spaced practice.", EXPLANATION,
        "Useful ideas remain available.", "A controlled source", "https://example.org/source", "document-1",
        "Source evidence for spaced practice.", listOf("memory"), saved.get(), "full_source")
    private val review = Review("review-1", "idea-1", "What makes practice stick?", "2026-10-18T00:00:00Z", 1.0, TITLE)

    fun start() {
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                requests += request
                if (request.getHeader("Authorization") !in setOf("Bearer alpha", "Bearer beta")) return MockResponse().setResponseCode(401)
                val path = request.path
                val isBeta = request.getHeader("Authorization") == "Bearer beta"
                if (path == "/api/v1/feed/today" && !isBeta && holdAlphaFeed.get()) {
                    check(releaseAlphaFeed.await(15, TimeUnit.SECONDS)) { "Timed out waiting to release account A" }
                }
                val body: Any = when (request.method to path) {
                    "GET" to "/api/v1/feed/today" -> listOf(if (isBeta) insight.copy(id = "beta-idea", title = "A different account") else insight)
                    "GET" to "/api/v1/library" -> if (saved.get()) listOf(insight) else emptyList<Insight>()
                    "GET" to "/api/v1/insights/idea-1" -> insight
                    "POST" to "/api/v1/insights/idea-1/save" -> { saved.set(true); insight }
                    "POST" to "/api/v1/ingest/url" -> IngestJob("remote-1", "processing")
                    "GET" to "/api/v1/jobs/remote-1" -> IngestJob("remote-1", if (completeJob.get()) "completed" else "processing")
                    "POST" to "/api/v1/tutor/chat" -> ChatReply("Spacing strengthens recall.", "Which practice helps recall?",
                        listOf(Citation("document-1", "https://example.org/source", "Source evidence for spaced practice.")), "review-1")
                    "GET" to "/api/v1/reviews/due" -> listOf(review)
                    "POST" to "/api/v1/reviews/review-1/answer" -> review.copy(dueAt = "2026-11-01T00:00:00Z")
                    else -> return MockResponse().setResponseCode(404).setBody("Unexpected fixture endpoint")
                }
                return MockResponse().addHeader("Content-Type", "application/json").setBody(gson.toJson(body))
            }
        }
        server.start()
    }
    fun count(method: String, path: String) = requests.count { it.method == method && it.path == path }
    override fun close() {
        releaseAlphaFeed.countDown()
        if (closed.compareAndSet(false, true)) server.shutdown()
    }
    companion object {
        const val TITLE = "Remember useful ideas"
        const val EXPLANATION = "Spaced practice revisits an idea after a delay to strengthen recall."
    }
}
