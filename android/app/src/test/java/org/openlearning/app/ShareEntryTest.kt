package org.openlearning.app

import org.junit.Assert.*
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
        assertTrue(shareDeferred(note, accountReady = false, draftReady = false))
        assertTrue(shareDeferred(note, accountReady = true, draftReady = false))
        assertTrue(shareDeferred(note, accountReady = false, draftReady = true))
        assertFalse(shareDeferred(note, accountReady = true, draftReady = true))
    }
    @Test fun linksAndIgnoredSharesNeverWait() {
        assertFalse(shareDeferred(ShareRoute.Url("https://a.example"), false, false))
        assertFalse(shareDeferred(ShareRoute.Ignore, false, false))
    }
    @Test fun rejectedShareOnlyWaitsForTheAccount() {
        val tooLong = ShareRoute.TooLong(10, 5)
        assertTrue(shareDeferred(tooLong, accountReady = false, draftReady = false))
        assertFalse(shareDeferred(tooLong, accountReady = true, draftReady = false))
    }
    @Test fun coldStartWithALateDraftAsksBeforeReplacing() {
        // Account is ready but the Room draft has not loaded yet: the share must wait, not see an empty draft.
        var draftReady = false
        var draft = NoteDraftFields()
        assertTrue(shareDeferred(note, accountReady = true, draftReady = draftReady))
        assertEquals(FilePlan.Apply, filePlan(draft)) // what would have overwritten the saved draft
        draft = NoteDraftFields("t", "", "unsent words"); draftReady = true
        assertFalse(shareDeferred(note, accountReady = true, draftReady = draftReady))
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
}
