package org.openlearning.app

/** An id leaves the purge floor after this long even if no release or Room arrival ended it (backstop, see [PurgeFloor]). */
const val PURGE_FLOOR_TTL_MS = 10 * 60 * 1000L

/** A floor entry: Room hidden-set arrivals that came without the id, and when the id was added. */
data class FloorEntry(val arrivals: Int = 0, val addedAt: Long = 0)

/**
 * Document ids a forget commit has just purged from the retained state ([ScreenPublisher.purgeNow]), kept so that a publication
 * or the derived view that still holds an older hidden set scrubs them too. An id leaves the floor on the FIRST of:
 * 1. an arrival that holds it (the Room set now hides it);
 * 2. an explicit [released] call, made whenever a PendingForget row is deleted (stop trying, TTL prune), so a note the person
 *    wanted back is visible on the next refresh (without it, a stale arrival lacking the id only aged it to 1);
 * 3. two Room arrivals lacking it (one may predate the commit, a second cannot);
 * 4. [PURGE_FLOOR_TTL_MS] (10 minutes) since it was added: a backstop so a missed release can never hide a note forever.
 * The time bound is safe because a still-pending or confirmed forget is also in the Room set, which hides it independently.
 * Time is passed in ([now]) so the class stays pure.
 */
data class PurgeFloor(val scope: String = "", val ids: Map<String, FloorEntry> = emptyMap()) {
    fun added(scope: String, ids: Set<String>, now: Long = 0): PurgeFloor {
        val base = if (scope == this.scope) this.ids else emptyMap()
        return PurgeFloor(scope, base + ids.filter { it !in base }.associateWith { FloorEntry(0, now) })
    }

    fun arrived(scope: String, hidden: Set<String>, now: Long = 0): PurgeFloor =
        if (scope != this.scope) this
        else copy(ids = ids.filter { (id, e) -> id !in hidden && !expired(e, now) }
            .mapValues { it.value.copy(arrivals = it.value.arrivals + 1) }.filterValues { it.arrivals < STALE_ARRIVALS })

    fun released(scope: String, ids: Set<String>): PurgeFloor =
        if (scope != this.scope) this else copy(ids = this.ids.filterKeys { it !in ids })

    fun idsFor(scope: String, now: Long = 0): Set<String> =
        if (scope == this.scope) ids.filterValues { !expired(it, now) }.keys else emptySet()

    private fun expired(e: FloorEntry, now: Long) = now - e.addedAt >= PURGE_FLOOR_TTL_MS

    private companion object { const val STALE_ARRIVALS = 2 }
}
