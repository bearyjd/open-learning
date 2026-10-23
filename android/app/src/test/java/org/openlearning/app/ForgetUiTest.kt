package org.openlearning.app

import org.junit.Assert.*
import org.junit.Test

class ForgetUiTest {
    @Test fun waitingLineHiddenAtZeroAndCountedOtherwise() {
        assertNull(forgetWaitingLine(0))
        assertEquals("1 forgotten note waiting for the server", forgetWaitingLine(1))
        assertEquals("3 forgotten notes waiting for the server", forgetWaitingLine(3))
    }
    @Test fun failedLineUsesTheFixedErrorAndNeverATitle() {
        assertEquals(FORGET_REJECTED_ERROR, forgetFailedLine(PendingForget("s", "d1", 1, FORGET_FAILED, FORGET_REJECTED_ERROR)))
        assertEquals("Could not delete (the server rejected the request)", forgetFailedLine(PendingForget("s", "d1", 1, FORGET_FAILED, FORGET_REJECTED_ERROR)))
        // An unknown or missing stored error never reaches the screen.
        assertEquals(FORGET_REFUSED_ERROR, forgetFailedLine(PendingForget("s", "d1", 1, FORGET_FAILED, "SECRET-BODY")))
        assertEquals(FORGET_REFUSED_ERROR, forgetFailedLine(PendingForget("s", "d1", 1, FORGET_FAILED, null)))
    }
    @Test fun bannerNeverCountsWaitingRows() {
        val rows = listOf(PendingForget("s", "d1", 1, FORGET_PENDING, null), PendingForget("s", "d2", 2, FORGET_PENDING, null))
        assertEquals(FORGET_QUEUED_MESSAGE, forgetBanner(ForgetResult.Done(emptyList()), rows))
        assertEquals(FORGET_QUEUED_MESSAGE, forgetBanner(ForgetResult.Done(emptyList()), emptyList()))
    }
    @Test fun bannerKeepsFailedForgetGuidance() {
        val rows = listOf(PendingForget("s", "d1", 1, FORGET_PENDING, null), PendingForget("s", "d2", 2, FORGET_FAILED, "SECRET-BODY"))
        val banner = forgetBanner(ForgetResult.Done(emptyList()), rows)
        assertEquals("$FORGET_QUEUED_MESSAGE 1 forgotten note could not be deleted. Retry from Your notes.", banner)
        assertFalse(banner.contains("SECRET-BODY"))
        assertFalse(banner.contains("waiting for the server"))
        assertTrue(forgetBanner(ForgetResult.Done(emptyList()), rows + rows[1].copy(documentId = "d3")).contains("2 forgotten notes could not"))
    }
    @Test fun failedLocalSaveBannerHasNoQueueText() {
        val rows = listOf(PendingForget("s", "d2", 2, FORGET_FAILED, null))
        assertEquals(FORGET_LOCAL_FAILED_MESSAGE, forgetBanner(ForgetResult.Failed(RuntimeException("x")), rows))
    }
    @Test fun copyStatesHiddenNowDeletedLaterNoUndo() {
        assertTrue(FORGET_QUEUED_MESSAGE.contains("hidden on this device"))
        assertTrue(FORGET_QUEUED_MESSAGE.contains("when a connection is available"))
        assertTrue(FORGET_QUEUED_MESSAGE.contains("no undo"))
        assertFalse(FORGET_QUEUED_MESSAGE.contains("was not forgotten"))
        assertFalse(FORGET_LOCAL_FAILED_MESSAGE.contains("was not forgotten"))
    }
}
