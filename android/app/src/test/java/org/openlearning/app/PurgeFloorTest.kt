package org.openlearning.app

import org.junit.Assert.*
import org.junit.Test

class PurgeFloorTest {
    @Test fun addedIdsAreKnownForTheirScopeOnly() {
        val floor = PurgeFloor().added("A", setOf("d1"))
        assertEquals(setOf("d1"), floor.idsFor("A")); assertEquals(emptySet<String>(), floor.idsFor("B"))
    }
    @Test fun addingForAnotherScopeReplacesTheOldScope() {
        val floor = PurgeFloor().added("A", setOf("d1")).added("B", setOf("d2"))
        assertEquals(emptySet<String>(), floor.idsFor("A")); assertEquals(setOf("d2"), floor.idsFor("B"))
    }
    @Test fun addingIsIdempotentAndMerges() {
        val floor = PurgeFloor().added("A", setOf("d1")).added("A", setOf("d1", "d2"))
        assertEquals(setOf("d1", "d2"), floor.idsFor("A")); assertEquals(floor, floor.added("A", setOf("d1")))
    }
    @Test fun anIdTheRoomSetNowHoldsIsDroppedFromTheFloor() {
        val floor = PurgeFloor().added("A", setOf("d1")).arrived("A", setOf("d1"))
        assertEquals(emptySet<String>(), floor.idsFor("A"))
    }
    @Test fun oneStaleArrivalWithoutTheIdKeepsItButASecondDropsIt() {
        val once = PurgeFloor().added("A", setOf("d1")).arrived("A", emptySet())
        assertEquals(setOf("d1"), once.idsFor("A"))
        assertEquals(emptySet<String>(), once.arrived("A", emptySet()).idsFor("A"))
    }
    @Test fun anArrivalOfAnotherScopeChangesNothing() {
        val floor = PurgeFloor().added("A", setOf("d1"))
        assertEquals(floor, floor.arrived("B", emptySet()))
    }
}
