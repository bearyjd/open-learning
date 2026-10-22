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
    @Test fun releaseRemovesIdsAtOnceAndOnlyInItsScope() {
        val floor = PurgeFloor().added("A", setOf("d1", "d2"))
        assertEquals(setOf("d2"), floor.released("A", setOf("d1")).idsFor("A"))
        assertEquals(floor, floor.released("B", setOf("d1")))
    }
    @Test fun theCodexSequenceLeavesNothingInTheFloor() {
        // commit -> failed row delivered (holds id) -> purgeNow adds id at age 0 -> stopTrying releases -> one arrival without it.
        val floor = PurgeFloor().added("A", setOf("d1")).released("A", setOf("d1")).arrived("A", emptySet())
        assertEquals(emptySet<String>(), floor.idsFor("A", 0))
    }
    @Test fun withoutTheReleaseOneArrivalLeavesTheIdHiddenIndefinitely() {
        val floor = PurgeFloor().added("A", setOf("d1")).arrived("A", emptySet())
        assertEquals(setOf("d1"), floor.idsFor("A", 0))
    }
    @Test fun theTimeBoundReleasesAfterTheTtlWithAFakeClock() {
        val floor = PurgeFloor().added("A", setOf("d1"), now = 1_000)
        assertEquals(setOf("d1"), floor.idsFor("A", 1_000 + PURGE_FLOOR_TTL_MS - 1))
        assertEquals(emptySet<String>(), floor.idsFor("A", 1_000 + PURGE_FLOOR_TTL_MS))
        assertEquals(emptySet<String>(), floor.arrived("A", emptySet(), 1_000 + PURGE_FLOOR_TTL_MS).ids.keys)
    }
    @Test fun theTtlIsTenMinutes() { assertEquals(600_000L, PURGE_FLOOR_TTL_MS) }
    @Test fun aStillPendingForgetStaysHiddenThroughTheRoomSetAfterTheFloorReleases() {
        val room = setOf("d1")
        val floor = PurgeFloor().added("A", setOf("d1"), now = 0)
        val later = PURGE_FLOOR_TTL_MS + 1
        assertEquals(emptySet<String>(), floor.idsFor("A", later))
        assertEquals(setOf("d1"), room + floor.idsFor("A", later))
    }
}
