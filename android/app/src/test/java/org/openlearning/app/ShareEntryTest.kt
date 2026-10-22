package org.openlearning.app

import org.junit.Assert.*
import kotlinx.coroutines.runBlocking
import org.junit.Test

class ShareEntryTest {
    @Test fun freshLaunchHandlesTheShare() {
        assertTrue(shouldHandleShare(isShare = true, isNewIntent = false, recreated = false, fromHistory = false))
    }
    @Test fun recreatedActivityDoesNotReplayTheShare() {
        assertFalse(shouldHandleShare(isShare = true, isNewIntent = false, recreated = true, fromHistory = false))
    }
    @Test fun launchFromRecentsDoesNotReplayTheShare() {
        assertFalse(shouldHandleShare(isShare = true, isNewIntent = false, recreated = false, fromHistory = true))
    }
    @Test fun newIntentHandlesTheShare() {
        assertTrue(shouldHandleShare(isShare = true, isNewIntent = true, recreated = false, fromHistory = false))
    }
    @Test fun consumedOrNonShareIntentIsNeverHandled() {
        assertFalse(shouldHandleShare(isShare = false, isNewIntent = false, recreated = false, fromHistory = false))
        assertFalse(shouldHandleShare(isShare = false, isNewIntent = true, recreated = false, fromHistory = false))
    }
    private val note = routeShare("a note body that is plain text")
    @Test fun noteWaitsForAccountAndDraft() {
        assertTrue(shareDeferred(note, accountReady = false, draftReady = false, limitsLoaded = true))
        assertTrue(shareDeferred(note, accountReady = true, draftReady = false, limitsLoaded = true))
        assertTrue(shareDeferred(note, accountReady = false, draftReady = true, limitsLoaded = true))
        assertFalse(shareDeferred(note, accountReady = true, draftReady = true, limitsLoaded = true))
    }
    @Test fun linksAndIgnoredSharesNeverWait() {
        assertFalse(shareDeferred(ShareRoute.Url("https://a.example"), false, false, false))
        assertFalse(shareDeferred(ShareRoute.Ignore, false, false, false))
    }
    @Test fun rejectedShareOnlyWaitsForTheAccount() {
        val tooLong = ShareRoute.TooLong(10, 5)
        assertTrue(shareDeferred(tooLong, accountReady = false, draftReady = false, limitsLoaded = true))
        assertFalse(shareDeferred(tooLong, accountReady = true, draftReady = false, limitsLoaded = true))
    }
    @Test fun coldStartWithALateDraftAsksBeforeReplacing() {
        // Account is ready but the Room draft has not loaded yet: the share must wait, not see an empty draft.
        var draftReady = false
        var draft = NoteDraftFields()
        assertTrue(shareDeferred(note, accountReady = true, draftReady = draftReady, limitsLoaded = true))
        assertEquals(FilePlan.Apply, filePlan(draft)) // what would have overwritten the saved draft
        draft = NoteDraftFields("t", "", "unsent words"); draftReady = true
        assertFalse(shareDeferred(note, accountReady = true, draftReady = draftReady, limitsLoaded = true))
        assertEquals(FilePlan.ConfirmReplace, filePlan(draft))
    }
    @Test fun repeatedIdenticalShareIsDropped() {
        assertNull(acceptShare("same", "same"))
        assertNull(acceptShare("same", null, "same"))
        assertEquals("new", acceptShare("new", "old", null))
        assertNull(acceptShare(null, "x"))
    }
    @Test fun replaceDialogNamesTheSource() {
        assertTrue(replaceDialogText(true).startsWith("The shared text will replace"))
        assertTrue(replaceDialogText(false).startsWith("The file will replace"))
    }
    @Test fun tooLongMessageNeverEchoesText() {
        val message = shareTooLongMessage(ShareRoute.TooLong(300_000, 70_000))
        assertTrue("300,000" in message && "70,000" in message && "Nothing was added" in message)
    }

    @Test fun shareWaitsForTheLimitsFetchToFinish() {
        assertTrue(shareDeferred(note, accountReady = true, draftReady = true, limitsLoaded = false))
        assertTrue(shareDeferred(ShareRoute.TooLong(10, 5), accountReady = true, draftReady = true, limitsLoaded = false))
        assertFalse(shareDeferred(note, accountReady = true, draftReady = true, limitsLoaded = true))
    }
    @Test fun coldStartWithLateLargerLimitAcceptsAValid100kShare() {
        val shared = "x".repeat(100_000)
        // Before /limits answers the defaults reject it with the wrong cap; that route must never be acted on.
        val early = routeShare(shared, DEFAULT_LIMITS)
        assertTrue(early is ShareRoute.TooLong)
        assertTrue(shareDeferred(early, accountReady = true, draftReady = true, limitsLoaded = false))
        val late = routeShare(shared, effectiveLimits(Limits(maxDocumentChars = 150_000)))
        assertTrue(late is ShareRoute.Note)
        assertFalse(shareDeferred(late, accountReady = true, draftReady = true, limitsLoaded = true))
    }
    @Test fun draftIsMarkedReadyEvenWhenTheLoadThrows() {
        var ready = false
        try { runBlocking { loadDraftThen({ true }, { ready = true }) { throw IllegalStateException("dao") } } } catch (_: IllegalStateException) {}
        assertTrue(ready)
    }
    @Test fun staleDraftLoadNeverMarksReady() {
        var ready = false
        runBlocking { loadDraftThen({ false }, { ready = true }) {} }
        assertFalse(ready)
    }
    @Test fun draftIsMarkedReadyOnlyAfterTheLoadRuns() {
        val order = mutableListOf<String>()
        runBlocking { loadDraftThen({ true }, { order += "ready" }) { order += "load" } }
        assertEquals(listOf("load", "ready"), order)
    }
}
