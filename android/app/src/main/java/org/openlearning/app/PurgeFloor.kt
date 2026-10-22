package org.openlearning.app

/**
 * Document ids a forget commit has just purged from the retained state ([ScreenPublisher.purgeNow]), kept so that a publication
 * or the derived view that still holds an older hidden set scrubs them too. Each id carries the number of Room hidden-set
 * arrivals that came without it: one such arrival may have been computed before the commit (stale), a second cannot, so the id
 * then leaves the floor (the row was really removed again, e.g. the person stopped trying). An arrival that holds it ends it.
 */
data class PurgeFloor(val scope: String = "", val ids: Map<String, Int> = emptyMap()) {
    fun added(scope: String, ids: Set<String>): PurgeFloor {
        val base = if (scope == this.scope) this.ids else emptyMap()
        return PurgeFloor(scope, base + ids.filter { it !in base }.associateWith { 0 })
    }

    fun arrived(scope: String, hidden: Set<String>): PurgeFloor =
        if (scope != this.scope) this
        else copy(ids = ids.filterKeys { it !in hidden }.mapValues { it.value + 1 }.filterValues { it < STALE_ARRIVALS })

    fun idsFor(scope: String): Set<String> = if (scope == this.scope) ids.keys else emptySet()

    private companion object { const val STALE_ARRIVALS = 2 }
}
