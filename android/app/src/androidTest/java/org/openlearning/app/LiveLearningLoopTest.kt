package org.openlearning.app

import android.content.Intent
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.test.core.app.ActivityScenario
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.work.WorkInfo
import androidx.work.WorkManager
import com.google.gson.JsonArray
import com.google.gson.JsonParser
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import okhttp3.OkHttpClient
import okhttp3.Request
import org.junit.After
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.time.Duration
import java.time.Instant
import java.util.concurrent.TimeUnit

/** Explicitly opt-in: no HTTP fixtures, model substitutions, or API mutation helpers.
 * Run with scripts/android_live.py against a fresh, disposable backend.
 */
@RunWith(AndroidJUnit4::class)
class LiveLearningLoopTest {
    @get:Rule val compose = createEmptyComposeRule()
    private val app: LearningApplication get() = ApplicationProvider.getApplicationContext()
    private val container get() = app.container
    private val work get() = WorkManager.getInstance(app)
    private var scenario: ActivityScenario<MainActivity>? = null
    private var optedIn = false
    private lateinit var source: String
    private lateinit var repository: LearningRepository
    private val http = OkHttpClient.Builder().followRedirects(false).followSslRedirects(false)
        .callTimeout(30, TimeUnit.SECONDS).build()

    @Before fun prepare() {
        assumeTrue("Run the explicit live acceptance runner to opt in",
            InstrumentationRegistry.getArguments().getString("liveLearningLoop") == "true")
        check(app.packageName == "org.openlearning.app.acceptance") { "Acceptance package required" }
        optedIn = true
        val file = File(app.filesDir, "live-learning-loop.json")
        val config = try { JsonParser.parseString(file.readText()).asJsonObject }
        finally { file.delete() }
        check(config.get("isolated_backend").asBoolean) { "A fresh isolated backend is required" }
        source = config.get("source_url").asString
        val server = normalizeServer(config.get("server_url").asString, true)
        val token = config.get("token").asString
        // Verify the remote state BEFORE clearing local data or sending UI mutations.
        repository = container.repository(Connection(server, token))
        io {
            assertTrue("Backend feed must be empty", repository.api.feed().isEmpty())
            assertTrue("Backend library must be empty", repository.api.library().isEmpty())
            assertTrue("Backend due reviews must be empty", repository.api.reviews().isEmpty())
        }
        assertEquals("Backend tutor history must be empty", 0, history().size())
        work.cancelAllWork().result.get(10, TimeUnit.SECONDS)
        io { container.database.clearAllTables() }
        container.credentials.save(server, token, true)
    }

    @After fun cleanUp() {
        if (!optedIn) return
        check(app.packageName == "org.openlearning.app.acceptance")
        scenario?.close()
        work.cancelAllWork().result.get(10, TimeUnit.SECONDS)
        File(app.filesDir, "live-learning-loop.json").delete()
    }

    @Test fun articleBecomesSavedLearningAndRememberedReviewAcrossRelaunch() {
        launch()
        waitText("Article URL")
        compose.onNodeWithText("Article URL").performTextInput(source)
        compose.onNodeWithText("Add to learning feed").performClick()
        waitUntil { io { repository.jobs.first().isNotEmpty() } }
        val row = io { repository.jobs.first().single() }
        waitUntil(600_000) {
            val current = io { container.database.dao().job(row.scope, row.id) }!!
            check(current.status !in setOf("failed", "error", "cancelled", "paused")) {
                "Live ingestion failed; inspect the isolated backend (not retried by this test)"
            }
            current.status == "completed" && work.getWorkInfosForUniqueWork("ingest-${row.scope}-${row.id}")
                .get(10, TimeUnit.SECONDS).any { it.state == WorkInfo.State.SUCCEEDED }
        }
        val job = io { container.database.dao().job(row.scope, row.id) }!!
        assertEquals("completed", io { repository.api.job(checkNotNull(job.jobId)).status })
        val insight = io { repository.api.feed() }.firstOrNull { it.sourceUrl.trimEnd('/') == source.trimEnd('/') }
            ?: error("Imported source did not appear in the real feed")
        assertTrue("Generated insight must include source/model metadata",
            listOf(insight.id, insight.title, insight.idea, insight.explanation, insight.documentId,
                insight.sourceTitle, insight.evidence, insight.coverage, insight.model).all { it.isNotBlank() })
        scrollTo(insight.title).performClick()
        scrollTo(insight.sourceTitle).assertIsDisplayed()
        scrollTo("Source: ${insight.coverage}").assertIsDisplayed()
        scrollTo(insight.evidence).assertIsDisplayed()
        scrollTo("Save").performClick()
        waitUntil { io { repository.api.library().any { it.id == insight.id && it.saved } } }
        waitUntil { io { container.database.dao().get(row.scope, insight.id)?.saved == true } }

        // Starts the tutor automatically through the shipped Learn UI.
        navigate("Learn")
        scrollTo(insight.title).performClick()
        waitUntil(180_000) { assistantHistory(insight.id).size == 1 }
        val firstAnswer = assistantHistory(insight.id).single()
        val firstReview = io { repository.api.reviews() }.single { it.insightId == insight.id }
        assertTrue(firstAnswer.isNotBlank() && firstReview.question.isNotBlank())
        scrollTo(firstAnswer).assertIsDisplayed()
        scrollTo(firstReview.question).assertIsDisplayed()
        scrollTo("Check source").assertIsDisplayed()
        waitUntil { compose.onNodeWithText("Explain it in your own words").isEnabled() }
        val learnerReply = "In my own words: ${insight.idea}"
        compose.onNodeWithText("Explain it in your own words").performTextInput(learnerReply)
        compose.onNodeWithText("Send", substring = false).performClick()
        waitUntil(180_000) { assistantHistory(insight.id).size == 2 }
        val messages = history().filter { it.asJsonObject.get("insight_id").asString == insight.id }
        assertEquals(1, messages.count { it.asJsonObject.get("role").asString == "user" &&
            it.asJsonObject.get("message").asString == learnerReply })
        val secondAnswer = assistantHistory(insight.id).first()
        assertTrue(secondAnswer.isNotBlank())
        // Scroll through the learner turn before the newest answer, even if wording repeats.
        scrollTo(learnerReply).assertIsDisplayed()
        scrollTo(secondAnswer).assertIsDisplayed()
        val recall = io { repository.api.reviews() }.single { it.insightId == insight.id }
        scrollTo(recall.question).assertIsDisplayed()
        compose.onNode(hasScrollToIndexAction()).performScrollToIndex(4)
        scrollTo("Check source").assertIsDisplayed()

        navigate("Review")
        scrollTo(recall.question).assertIsDisplayed()
        compose.onNodeWithText("Remembered", substring = false).assertDoesNotExist()
        scrollTo("Show explanation").performClick()
        scrollTo(insight.explanation).assertIsDisplayed()
        scrollTo(insight.evidence).assertIsDisplayed()
        val ratedAt = Instant.now()
        scrollTo("Remembered").performClick()
        waitText("You're up to date")
        waitUntil { compose.onAllNodesWithText("Next review: ", substring = true).fetchSemanticsNodes().isNotEmpty() }
        val notice = compose.onNodeWithText("Next review: ", substring = true).fetchSemanticsNode()
            .config[SemanticsProperties.Text].joinToString("") { it.text }
        val nextDue = Instant.parse(notice.substringAfter("Next review: "))
        assertTrue("Remembered must schedule the review three days ahead",
            Duration.between(ratedAt.plus(Duration.ofDays(3)), nextDue).abs() < Duration.ofMinutes(2))
        assertTrue(io { repository.api.reviews().none { it.id == recall.id } })

        // New Activity AND ViewModel; the backend retains the future review and Room the saved item.
        scenario!!.close()
        scenario = null
        assertEquals(repository.config.scope, checkNotNull(Credentials(app).connection.value).scope)
        launch()
        navigate("Library")
        scrollTo(insight.title).performClick()
        scrollTo(insight.explanation).assertIsDisplayed()
        scrollTo(insight.evidence).assertIsDisplayed()
        assertTrue(io { repository.api.library().any { it.id == insight.id && it.saved } })
        navigate("Review")
        waitText("You're up to date")
        assertTrue(io { repository.api.reviews().none { it.id == recall.id } })
        assertEquals(2, assistantHistory(insight.id).size)
    }

    private fun history(): JsonArray {
        val request = Request.Builder().url(repository.config.server + "api/v1/history")
            .header("Authorization", "Bearer ${repository.config.token}").build()
        return http.newCall(request).execute().use { response ->
            check(response.isSuccessful) { "Live history GET failed" }
            JsonParser.parseString(checkNotNull(response.body).string()).asJsonArray
        }
    }
    private fun assistantHistory(id: String): List<String> = history().filter {
        it.asJsonObject.get("insight_id").asString == id && it.asJsonObject.get("role").asString == "assistant"
    }.map { it.asJsonObject.get("message").asString }
    private fun launch() { scenario = ActivityScenario.launch(Intent(app, MainActivity::class.java)) }
    private fun navigate(title: String) { compose.onNodeWithText(title, substring = false).performClick() }
    private fun waitText(text: String) = waitUntil { compose.onAllNodesWithText(text).fetchSemanticsNodes().isNotEmpty() }
    private fun scrollTo(text: String): SemanticsNodeInteraction {
        compose.onNode(hasScrollToIndexAction()).performScrollToNode(hasText(text))
        return compose.onAllNodesWithText(text, substring = false).onLast()
    }
    private fun SemanticsNodeInteraction.isEnabled(): Boolean = runCatching { assertIsEnabled(); true }.getOrDefault(false)
    private fun waitUntil(timeout: Long = 20_000, condition: () -> Boolean) = compose.waitUntil(timeout, condition)
    private fun <T> io(block: suspend () -> T): T = runBlocking(Dispatchers.IO) { block() }
}
