package org.openlearning.app

import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.update

/**
 * The single owner of the retained [ScreenState]. Every publication goes through [publishFlow] (Room-flow collectors only),
 * [publishFresh], [publishLocal] (changes that add no document content) or [reset]; a source scan in the tests (a text scan,
 * not a runtime guarantee) fails if another file writes a ScreenState flow directly or misuses these. Best-effort against the
 * interleavings found so far, not a proof. Each publication is purged of the hidden set before it is stored, and a publication
 * for a scope other than the state's is dropped, never applied to the new account's state.
 */
class ScreenPublisher(
    private val hiddenNow: suspend (String) -> Set<String>,
    private val hiddenFlow: (String) -> Flow<Set<String>>,
    private val onHiddenGained: () -> Unit = {},
) {
    private val mutable = MutableStateFlow(ScreenState())
    val flow: StateFlow<ScreenState> = mutable.asStateFlow()
    val value: ScreenState get() = mutable.value
    /** The last hidden set the Room flow delivered, tagged with its account: a snapshot of another account is unknown. */
    private val hiddenSnapshot = MutableStateFlow<Pair<String, Set<String>>>("" to emptySet())

    /** The durable forget set of the account in the state, tagged with that account so a switch in flight never mixes sets. */
    @OptIn(ExperimentalCoroutinesApi::class)
    val hiddenByScope: Flow<Pair<String, Set<String>>> = mutable.map { it.scope }.distinctUntilChanged()
        .flatMapLatest { scope -> (if (scope.isEmpty()) flowOf(emptySet()) else hiddenFlow(scope)).map { scope to it } }

    /** Ids purged by a forget commit before the Room flow reported them (see [purgeNow]). */
    private val floor = MutableStateFlow(PurgeFloor())

    /** What the derived view scrubs with: the Room set together with the latest purge ids, so it never lags a forget commit. */
    val hiddenForView: Flow<Pair<String, Set<String>>> = combine(hiddenByScope, floor) { (scope, ids), known -> scope to (ids + known.idsFor(scope)) }

    /**
     * The forget side of the two-sided protocol. [ForgetQueue] calls this right after its transaction committed and before it
     * returns, so the retained state drops [ids] without waiting for the Room-flow collector. Idempotent and atomic.
     * Ordering argument, with C the forget commit, P this purge (after C), S the atomic store of a publication and R the
     * publication's DAO re-read after S: a store S that precedes P is removed by P; a store that follows P is removed by the
     * floor purge inside the store itself; and if C falls before R the re-read sees the id and purges it. Whatever the order of
     * {the publication's first read, C, S}, the id is gone once P and R have both run. The re-read and P never re-run the transform.
     */
    fun purgeNow(scope: String, ids: Set<String>) {
        if (ids.isEmpty()) return
        floor.update { it.added(scope, ids) }
        mutable.update { it.purgedFor(scope, ids) }
    }

    /**
     * The Room flow delivered a new hidden set: remember it, purge what is retained now and, when it gained an id, clear the
     * tutor chat (the callback first, so an in-flight reply is dropped by its generation before the clear lands).
     */
    fun hiddenArrived(hidden: Pair<String, Set<String>>) {
        val gained = hiddenGained(hiddenSnapshot.value, hidden)
        hiddenSnapshot.value = hidden
        floor.update { it.arrived(hidden.first, hidden.second) }
        if (gained) onHiddenGained()
        mutable.update { current ->
            val purged = current.purgedFor(hidden.first, hidden.second)
            if (gained && current.scope == hidden.first) purged.withChatCleared() else purged
        }
    }

    /** For changes that add no document content (flags, errors, notices, removals): purged with the latest snapshot only. */
    fun publishLocal(transform: (ScreenState) -> ScreenState) { apply(null, emptyList(), transform) }

    /** For the Room-flow collectors: the snapshot, or a Room read when it belongs to another account (unknown). */
    suspend fun publishFlow(transform: (ScreenState) -> ScreenState) {
        val scope = mutable.value.scope
        val extra = if (hiddenSnapshot.value.first == scope) emptyList() else listOf(scope to hiddenNow(scope))
        apply(scope, extra, transform)
    }

    /** For every user, network or restore-origin publication: reads the durable set (Room) right now. False when dropped (another scope). */
    suspend fun publishFresh(scope: String, transform: (ScreenState) -> ScreenState): Boolean = publishFreshWith(scope) { state, _ -> transform(state) }

    /** As [publishFresh], with the hidden set that was read, for a transform that must decide on it. */
    suspend fun publishFreshWith(scope: String, transform: (ScreenState, Set<String>) -> ScreenState): Boolean {
        val hidden = hiddenNow(scope)
        val applied = apply(scope, listOf(scope to hidden)) { transform(it, hidden) }
        // The read side of the protocol: a forget that committed after the read above is seen now; purge only, never the transform.
        if (applied) hiddenNow(scope).let { after -> if (after.isNotEmpty()) mutable.update { it.purgedFor(scope, after) } }
        return applied
    }

    /** A new, empty state for [scope] (a connection change). */
    fun reset(scope: String) { floor.value = PurgeFloor(); mutable.update { ScreenState(scope = scope) } }

    /**
     * Applies [transform] once, atomically, and only if the state still belongs to [scope] (null: any). A snapshot that changes
     * meanwhile is applied afterwards by purging alone, never by running [transform] again (it is not idempotent).
     */
    private fun apply(scope: String?, extra: List<Pair<String, Set<String>>>, transform: (ScreenState) -> ScreenState): Boolean {
        var applied = false
        var used = hiddenSnapshot.value
        mutable.update { current ->
            applied = scope == null || current.scope == scope
            if (!applied) current else { used = hiddenSnapshot.value; current.published(listOf(used, floor.value.scope to floor.value.idsFor(floor.value.scope)) + extra, transform) }
        }
        val latest = hiddenSnapshot.value
        if (applied && latest !== used) mutable.update { it.purgedFor(latest.first, latest.second) }
        return applied
    }
}
