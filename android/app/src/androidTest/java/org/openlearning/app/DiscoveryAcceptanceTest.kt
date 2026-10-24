// SPDX-License-Identifier: GPL-3.0-only
package org.openlearning.app

import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.*
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.test.ext.junit.runners.AndroidJUnit4
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.first
import org.junit.After
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/** Compile-checked only. No device execution is claimed for these discovery screens. */
@RunWith(AndroidJUnit4::class)
class DiscoveryAcceptanceTest {
    @get:Rule val compose = createComposeRule()
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private val actions = DiscoveryActions(scope, { null }, onIngested = { _, _, _ -> })
    @After fun cleanUp() { scope.cancel() }

    @Test fun feedsHaveAccessibleInputAndFixedActions() {
        compose.setContent { MaterialTheme { FeedsScreen(DiscoveryFeedsState(), actions, onClose = {}) } }
        compose.onNodeWithTag("feed-url").assertExists().performTextInput("https://example.org/feed")
        compose.onNodeWithText("Add feed").assertIsEnabled()
        compose.onNodeWithText("Close").assertExists()
    }

    @Test fun topicsInputIsClearedAfterForgetWithoutRemountingTheScreen() {
        var epoch by mutableStateOf(0L)
        compose.setContent { MaterialTheme { TopicsScreen(DiscoveryTopicsState(), actions, onClose = {}, privacyEpoch = epoch) } }
        compose.onNodeWithTag("topic-term").performTextInput("private forgotten term")
        compose.runOnIdle { epoch++ }
        compose.onNodeWithText("private forgotten term").assertDoesNotExist()
        compose.onNodeWithText("Pin topic").assertIsNotEnabled()
    }

    @Test fun topicsInputIsClearedWhenScopeCompositionChanges() {
        var account by mutableStateOf("first")
        compose.setContent { MaterialTheme { key(account) { TopicsScreen(DiscoveryTopicsState(), actions, onClose = {}) } } }
        compose.onNodeWithTag("topic-term").performTextInput("private topic")
        compose.runOnIdle { account = "second" }
        compose.onNodeWithTag("topic-term").assertTextContains("")
        compose.onNodeWithText("private topic").assertDoesNotExist()
        compose.onNodeWithText("Pin topic").assertIsNotEnabled()
    }
}

/** Full navigation fixture. Authored and compile-checked only; never run by the implementation agent. */
@RunWith(AndroidJUnit4::class)
class DiscoveryNavigationAcceptanceTest {
    @get:Rule val compose = androidx.compose.ui.test.junit4.createEmptyComposeRule()
    private val app: LearningApplication get() = androidx.test.core.app.ApplicationProvider.getApplicationContext()
    private val server = okhttp3.mockwebserver.MockWebServer()
    private val requests = java.util.concurrent.CopyOnWriteArrayList<String>()
    private var scenario: androidx.test.core.app.ActivityScenario<MainActivity>? = null
    private val ingested = java.util.concurrent.atomic.AtomicBoolean(false)
    private val conflicted = java.util.concurrent.atomic.AtomicBoolean(false)

    @org.junit.Before fun prepare() {
        check(app.packageName == "org.openlearning.app.acceptance") { "Refusing to reset a non-acceptance app" }
        androidx.work.WorkManager.getInstance(app).cancelAllWork().result.get(10, java.util.concurrent.TimeUnit.SECONDS)
        io { app.container.database.clearAllTables() }
        server.dispatcher = object : okhttp3.mockwebserver.Dispatcher() {
            override fun dispatch(request: okhttp3.mockwebserver.RecordedRequest): okhttp3.mockwebserver.MockResponse {
                val path = request.path.orEmpty()
                requests += "${request.method} $path"
                if (request.getHeader("Authorization") != "Bearer discovery-fixture") return okhttp3.mockwebserver.MockResponse().setResponseCode(401)
                if (path.substringBefore('?') == "/api/v1/discover") {
                    if (path.contains("cursor=") && conflicted.compareAndSet(false, true)) return json("{}", 409)
                    val rows = if (ingested.get()) "[]" else """[{"id":"fixture-item","feed_id":"fixture-feed","url":"https://example.org/article","title":"Fixture article","snippet":"Fixture summary","published_at":"2026-10-22T09:00:00Z","matched_topics":["fixture topic"]}]"""
                    return json("""{"items":$rows,"next_cursor":"fixture-cursor"}""")
                }
                if (path == "/api/v1/discover/fixture-item/ingest") {
                    ingested.set(true)
                    return json("""{"id":"fixture-job","status":"queued"}""", 202)
                }
                if (path == "/api/v1/jobs/fixture-job") return json("""{"id":"fixture-job","status":"completed"}""")
                if (path == "/api/v1/limits") return json("{}")
                if (path.substringBefore('?') in setOf("/api/v1/feed/today", "/api/v1/library", "/api/v1/notes")) return json("[]")
                return json("{}", 404)
            }
        }
        server.start()
        app.container.credentials.save(server.url("/").toString(), "discovery-fixture", true)
    }

    @After fun cleanUp() {
        check(app.packageName == "org.openlearning.app.acceptance")
        scenario?.closeAfterFinish()
        androidx.work.WorkManager.getInstance(app).cancelAllWork().result.get(10, java.util.concurrent.TimeUnit.SECONDS)
        server.shutdown()
    }

    @Test fun queueEntryShowsPrivateRowsAndRefetchesAfterRecreation() {
        openQueue()
        compose.onNodeWithText("Fixture summary").assertExists()
        org.junit.Assert.assertTrue(secure())
        val before = requests.count { it.startsWith("GET /api/v1/discover?") }
        scenario!!.recreate()
        waitText("Fixture article")
        compose.waitUntil(15_000) { requests.count { it.startsWith("GET /api/v1/discover?") } > before }
        compose.onNodeWithText("Close").performClick()
        compose.waitUntil(15_000) { !secure() }
    }

    @Test fun relevanceConflictRestartsAndIngestTracksOnlyTheJobIdentifier() {
        openQueue()
        compose.onNodeWithText("Relevance").performClick()
        waitText("fixture topic")
        compose.onNodeWithTag("reading-queue").performScrollToNode(hasText("Load more"))
        compose.onNodeWithText("Load more").performClick()
        compose.waitUntil(15_000) {
            runCatching { compose.onNodeWithTag("reading-queue").performScrollToNode(hasText(DISCOVERY_RANKING_CHANGED)); true }.getOrDefault(false)
        }
        waitText(DISCOVERY_RANKING_CHANGED)
        compose.onNodeWithTag("reading-queue").performScrollToNode(hasText("Ingest"))
        compose.onNodeWithText("Ingest").performClick()
        compose.waitUntil(15_000) { io { app.container.database.dao().jobs(checkNotNull(app.container.credentials.connection.value).scope).first() }.any { it.jobId == "fixture-job" } }
        val row = io { app.container.database.dao().jobs(checkNotNull(app.container.credentials.connection.value).scope).first() }.first { it.jobId == "fixture-job" }
        org.junit.Assert.assertEquals("", row.url)
        org.junit.Assert.assertNull(row.title)
        org.junit.Assert.assertEquals("discovery", row.kind)
    }

    private fun openQueue() {
        scenario = androidx.test.core.app.ActivityScenario.launch(MainActivity::class.java)
        waitText("Reading queue")
        compose.onNode(hasScrollToIndexAction()).performScrollToNode(hasText("Reading queue"))
        compose.onNodeWithText("Reading queue").performClick()
        waitText("Fixture article")
    }
    private fun waitText(text: String) = compose.waitUntil(15_000) { compose.onAllNodesWithText(text).fetchSemanticsNodes().isNotEmpty() }
    private fun secure(): Boolean {
        var result = false
        scenario!!.onActivity { result = it.window.attributes.flags and android.view.WindowManager.LayoutParams.FLAG_SECURE != 0 }
        return result
    }
    private fun json(body: String, code: Int = 200) = okhttp3.mockwebserver.MockResponse().setResponseCode(code).addHeader("Content-Type", "application/json").setBody(body)
    private fun <T> io(block: suspend () -> T): T = kotlinx.coroutines.runBlocking(Dispatchers.IO) { block() }
}
