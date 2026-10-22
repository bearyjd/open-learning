package org.openlearning.app

import org.junit.Assert.*
import org.junit.Test

class SecureRefCountTest {
    @Test fun onlyTheFirstUserSetsAndOnlyTheLastClears() {
        val counter = SecureRefCount()
        assertTrue(counter.acquire()); assertFalse(counter.acquire())
        assertFalse(counter.release()); assertEquals(1, counter.count)
        assertTrue(counter.release()); assertEquals(0, counter.count)
    }
    @Test fun unmatchedReleaseNeverGoesNegativeOrClears() {
        val counter = SecureRefCount()
        assertFalse(counter.release()); assertEquals(0, counter.count)
        assertTrue(counter.acquire())
    }
    @Test fun nestedUserKeepsTheFlagWhenTheOuterOneLeavesFirst() {
        val counter = SecureRefCount()
        counter.acquire(); counter.acquire()
        assertFalse(counter.release())
        assertTrue(counter.release())
    }
}
