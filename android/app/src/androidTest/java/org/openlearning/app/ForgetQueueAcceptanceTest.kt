package org.openlearning.app

import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.test.core.app.ActivityScenario
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.work.WorkManager
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import java.util.concurrent.TimeUnit

/**
 * E2b-3 offline forget queue against a device-local fixture. Compiled in CI; not run on a device.
 * Run on the .acceptance app only. A failed first attempt backs off 30 s, so waits for a replay allow 90 s.
 */
@RunWith(AndroidJUnit4::class)
class ForgetQueueAcceptanceTest {
    @get:Rule val compose = createEmptyComposeRule()
    private val app: LearningApplication get() = ApplicationProvider.getApplicationContext()
    private val container get() = app.container
    private val work get() = WorkManager.getInstance(app)
    private lateinit var fixture: ListFixture
    private var scenario: ActivityScenario<MainActivity>? = null

    @Before fun prepare() {
        check(app.packageName == "org.openlearning.app.acceptance") { "Refusing to reset a non-acceptance app" }
        work.cancelAllWork().result.get(10, TimeUnit.SECONDS)
        io { container.database.clearAllTables() }
        fixture = ListFixture().also { it.start() }
        container.credentials.save(fixture.server.url("/").toString(), "alpha", true)
    }

    @After fun cleanUp() {
        check(app.packageName == "org.openlearning.app.acceptance") { "Refusing to reset a non-acceptance app" }
        scenario?.closeAfterFinish()
        work.cancelAllWork().result.get(10, TimeUnit.SECONDS)
        fixture.server.shutdown()
    }

    @Test fun offlineForgetSurvivesARestartAndSendsExactlyOneDeleteOnReconnect() {
        openList(); waitText("Alpha note")
        fixture.down.set(true)
        forgetFirst()
        waitText("1 forgotten note waiting for the server")
        assertEquals(0, fixture.count("DELETE", "/api/v1/notes/${TestIds.DOC_A}"))
        // Process death stand-in: finish the activity (new ViewModel on relaunch) and drop the scheduled work.
        scenario!!.closeAfterFinish(); scenario = null
        work.cancelAllWork().result.get(10, TimeUnit.SECONDS)
        assertEquals(1, forgets().size)
        fixture.down.set(false)
        openList()
        waitUntil(90_000) { fixture.count("DELETE", "/api/v1/notes/${TestIds.DOC_A}") == 1 }
        waitUntil { forgets().map { it.status } == listOf(FORGET_CONFIRMED) }
        waitUntil { compose.onAllNodesWithText("1 forgotten note waiting for the server").fetchSemanticsNodes().isEmpty() }
        Thread.sleep(2_000)
        assertEquals(1, fixture.count("DELETE", "/api/v1/notes/${TestIds.DOC_A}"))
    }

    @Test fun a404CountsAsSuccess() {
        fixture.deleteStatus.set(404)
        openList(); waitText("Alpha note")
        forgetFirst()
        waitUntil(90_000) { forgets().map { it.status } == listOf(FORGET_CONFIRMED) }
        compose.onNodeWithText("Alpha note").assertDoesNotExist()
        assertTrue(compose.onAllNodesWithText("Could not delete", substring = true).fetchSemanticsNodes().isEmpty())
    }

    @Test fun a401LeavesAFailedRowWithRetryAndTheNoteStaysHidden() {
        fixture.deleteStatus.set(401)
        openList(); waitText("Alpha note")
        forgetFirst()
        waitText("Could not delete (the server rejected the request)", substring = true)
        waitText("Retry")
        assertEquals(listOf(FORGET_FAILED), forgets().map { it.status })
        compose.onNodeWithText("Refresh").performClick()
        Thread.sleep(2_000)
        compose.onNodeWithText("Alpha note").assertDoesNotExist()
        // Retry once the server accepts: the row goes pending, the worker deletes it.
        fixture.deleteStatus.set(204)
        compose.onNodeWithText("Retry").performClick()
        waitUntil(90_000) { forgets().map { it.status } == listOf(FORGET_CONFIRMED) }
    }

    @Test fun aServerErrorKeepsTheRowPendingAndIsRetried() {
        fixture.deleteStatus.set(503)
        openList(); waitText("Alpha note")
        forgetFirst()
        waitUntil { fixture.count("DELETE", "/api/v1/notes/${TestIds.DOC_A}") >= 1 }
        assertEquals(listOf(FORGET_PENDING), forgets().map { it.status })
        fixture.deleteStatus.set(204)
        waitUntil(90_000) { fixture.count("DELETE", "/api/v1/notes/${TestIds.DOC_A}") >= 2 }
        waitUntil(90_000) { forgets().map { it.status } == listOf(FORGET_CONFIRMED) }
    }

    @Test fun anotherAccountsPendingRowSurvivesReconnect() {
        val otherScope = Connection("https://other.invalid/", "beta").scope
        io { container.database.dao().putForget(PendingForget(otherScope, TestIds.DOC_OTHER, 1, FORGET_PENDING)) }
        openList(); waitText("Alpha note")
        Thread.sleep(3_000)
        assertEquals(0, fixture.count("DELETE", "/api/v1/notes/${TestIds.DOC_OTHER}"))
        assertEquals(1, io { container.database.dao().forgets(otherScope).size })
    }

    private fun forgetFirst() {
        compose.onAllNodesWithText("Forget")[0].performClick()
        waitText("Forget this note?")
        compose.onNode(hasText("Forget") and hasAnyAncestor(isDialog())).performClick()
    }
    private fun openList() {
        scenario = ActivityScenario.launch(MainActivity::class.java)
        waitText("Your notes")
        compose.onNode(hasScrollToIndexAction()).performScrollToNode(hasText("Your notes"))
        compose.onNodeWithText("Your notes").performClick()
    }
    private fun scope() = checkNotNull(container.credentials.connection.value).scope
    private fun forgets(): List<PendingForget> = io { container.database.dao().forgets(scope()) }
    private fun waitText(text: String, substring: Boolean = false) = waitUntil { compose.onAllNodesWithText(text, substring = substring).fetchSemanticsNodes().isNotEmpty() }
    private fun waitUntil(timeout: Long = 15_000, condition: () -> Boolean) = compose.waitUntil(timeout, condition)
    private fun <T> io(block: suspend () -> T): T = runBlocking(Dispatchers.IO) { block() }
}
