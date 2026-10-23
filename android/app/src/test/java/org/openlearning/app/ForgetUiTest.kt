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
    @Test fun bannerCarriesNoQueueCount() {
        // A count in a banner goes stale once the queue drains; the live line on the list is the only count shown.
        listOf(ForgetResult.Done(emptyList()), ForgetResult.Failed(RuntimeException("x"))).forEach {
            assertFalse(it.message().contains("waiting for the server"))
        }
        assertEquals(FORGET_QUEUED_MESSAGE, ForgetResult.Done(emptyList()).message())
    }
    @Test fun copyStatesHiddenNowDeletedLaterNoUndo() {
        assertTrue(FORGET_QUEUED_MESSAGE.contains("hidden on this device"))
        assertTrue(FORGET_QUEUED_MESSAGE.contains("when a connection is available"))
        assertTrue(FORGET_QUEUED_MESSAGE.contains("no undo"))
        assertFalse(FORGET_QUEUED_MESSAGE.contains("was not forgotten"))
        assertFalse(FORGET_LOCAL_FAILED_MESSAGE.contains("was not forgotten"))
    }
}
