package org.openlearning.app

import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.update

/**
 * The single owner of the retained [ScreenState]. Every publication (search results, detail, chat, reviews, notes, jobs,
 * resets) goes through [publish], [publishFresh] or [reset], the only code that writes the underlying flow; a source scan in
 * the tests (a text scan, not a runtime guarantee) fails if another file writes a ScreenState flow directly. Each publication
 * is purged of the latest hidden set (kept in [hiddenSnapshot] by [hiddenArrived]) before it is stored.
 */
class ScreenPublisher(private val queue: ForgetQueue) {
    private val mutable = MutableStateFlow(ScreenState())
    val flow: StateFlow<ScreenState> = mutable.asStateFlow()
    val value: ScreenState get() = mutable.value
    private val hiddenSnapshot = MutableStateFlow<Pair<String, Set<String>>>("" to emptySet())

    /** The durable forget set of the account in the state, tagged with that account so a switch in flight never mixes sets. */
    @OptIn(ExperimentalCoroutinesApi::class)
    val hiddenByScope: Flow<Pair<String, Set<String>>> = mutable.map { it.scope }.distinctUntilChanged()
        .flatMapLatest { scope -> (if (scope.isEmpty()) flowOf(emptySet()) else queue.hiddenIds(scope)).map { scope to it } }

    /** The Room flow delivered a new hidden set: remember it for later publications, then purge what is retained now. */
    fun hiddenArrived(hidden: Pair<String, Set<String>>) {
        hiddenSnapshot.value = hidden
        mutable.update { it.published(listOf(hidden)) { state -> state } }
    }

    /** Purges with the latest hidden set. Re-checks after the write, so a set that arrives mid-publication is applied too. */
    fun publish(transform: (ScreenState) -> ScreenState) = publishWith(emptyList(), transform)

    /** For a network-origin publication: also reads the durable set (Room) right now and purges with it. */
    suspend fun publishFresh(scope: String, transform: (ScreenState) -> ScreenState) {
        publishWith(listOf(scope to queue.hidden(scope)), transform)
    }

    /** A new, empty state for [scope] (a connection change). */
    fun reset(scope: String) = publish { ScreenState(scope = scope) }

    private fun publishWith(extra: List<Pair<String, Set<String>>>, transform: (ScreenState) -> ScreenState) {
        while (true) {
            var used: Pair<String, Set<String>>? = null
            mutable.update { current ->
                val snapshot = hiddenSnapshot.value
                used = snapshot
                current.published(listOf(snapshot) + extra, transform)
            }
            if (hiddenSnapshot.value === used) return
        }
    }
}
