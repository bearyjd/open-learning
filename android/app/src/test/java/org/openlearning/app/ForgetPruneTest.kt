package org.openlearning.app

import org.junit.Assert.*
import org.junit.Test

/** TTL pruning of confirmed forget rows: pure decisions only. */
class ForgetPruneTest {
    private val ttl = FORGET_CONFIRMED_TTL_MS
    private fun confirmed(id: String, createdAt: Long, scope: String = "s") = PendingForget(scope, id, createdAt, FORGET_CONFIRMED)

    @Test fun ttlIsSevenDays() { assertEquals(7L * 24 * 60 * 60 * 1000, ttl) }
    @Test fun statusIsTheLiteralConfirmed() { assertEquals("confirmed", FORGET_CONFIRMED) }

    @Test fun deletedWhenPastTtlCompleteAndNotListed() {
        val d = confirmedDecisions("s", listOf(confirmed("a", 0)), emptySet(), true, ttl, ttl)
        assertEquals(listOf("a"), d.delete); assertTrue(d.revive.isEmpty())
    }
    @Test fun exactlyAtTtlIsPast() {
        assertEquals(listOf("a"), confirmedDecisions("s", listOf(confirmed("a", 100)), emptySet(), true, 100 + ttl, ttl).delete)
    }
    @Test fun oneMillisecondBeforeTtlIsKept() {
        val d = confirmedDecisions("s", listOf(confirmed("a", 100)), emptySet(), true, 100 + ttl - 1, ttl)
        assertTrue(d.delete.isEmpty() && d.revive.isEmpty())
    }
    @Test fun incompletePageNeverDeletes() {
        val d = confirmedDecisions("s", listOf(confirmed("a", 0)), emptySet(), false, ttl * 2, ttl)
        assertTrue(d.delete.isEmpty() && d.revive.isEmpty())
    }
    @Test fun stillListedAfterTtlFlipsBackToPending() {
        val d = confirmedDecisions("s", listOf(confirmed("a", 0)), setOf("a"), true, ttl, ttl)
        assertEquals(listOf("a"), d.revive); assertTrue(d.delete.isEmpty())
    }
    @Test fun stillListedAfterTtlOnAnIncompletePageAlsoFlips() {
        assertEquals(listOf("a"), confirmedDecisions("s", listOf(confirmed("a", 0)), setOf("a"), false, ttl, ttl).revive)
    }
    @Test fun stillListedInsideTtlIsKept() {
        val d = confirmedDecisions("s", listOf(confirmed("a", 0)), setOf("a"), true, ttl - 1, ttl)
        assertTrue(d.delete.isEmpty() && d.revive.isEmpty())
    }
    @Test fun rowConfirmedAfterTheRequestStartedIsKept() {
        // Delivery of the answer is later than its start: the row was confirmed while the request was in flight.
        val d = confirmedDecisions("s", listOf(confirmed("a", 5_000)), emptySet(), true, 1_000, ttl)
        assertTrue(d.delete.isEmpty() && d.revive.isEmpty())
    }
    @Test fun ageIsMeasuredFromRequestStartNotDelivery() {
        val started = ttl - 1
        assertTrue(confirmedDecisions("s", listOf(confirmed("a", 0)), emptySet(), true, started, ttl).delete.isEmpty())
    }
    @Test fun otherScopesAndOtherStatusesAreKept() {
        val rows = listOf(confirmed("a", 0, scope = "t"), PendingForget("s", "p", 0, FORGET_PENDING), PendingForget("s", "f", 0, FORGET_FAILED))
        val d = confirmedDecisions("s", rows, emptySet(), true, ttl * 2, ttl)
        assertTrue(d.delete.isEmpty() && d.revive.isEmpty())
    }

    @Test fun serverPageIsCompleteOnlyBelowTheLimit() {
        val full = (1..NOTES_PAGE_LIMIT).map { NoteListItem(documentId = "d$it") }
        assertFalse(serverPage(full).complete)
        assertTrue(serverPage(full.dropLast(1)).complete)
        assertTrue(serverPage(emptyList()).complete)
    }
    @Test fun serverPageIdsSkipRowsWithoutAnId() {
        val page = serverPage(listOf(NoteListItem(documentId = "a"), NoteListItem(documentId = null), NoteListItem(documentId = "")))
        assertEquals(setOf("a"), page.ids)
    }
    @Test fun sweepReconcilesOnlyWhenAConfirmedRowIsPastTtl() {
        assertFalse(sweepNeedsReconcile(listOf(confirmed("a", 0)), ttl - 1, ttl))
        assertTrue(sweepNeedsReconcile(listOf(confirmed("a", 0)), ttl, ttl))
        assertFalse(sweepNeedsReconcile(listOf(PendingForget("s", "p", 0, FORGET_PENDING)), ttl * 3, ttl))
        assertFalse(sweepNeedsReconcile(emptyList(), ttl * 3, ttl))
    }
}
