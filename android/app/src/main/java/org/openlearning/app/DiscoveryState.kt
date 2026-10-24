// SPDX-License-Identifier: GPL-3.0-only
package org.openlearning.app

enum class DiscoverySort(val wire: String) { NEWEST("newest"), RELEVANCE("relevance") }

data class DiscoveryQueueState(
    val items: List<DiscoveryItem> = emptyList(), val sort: DiscoverySort = DiscoverySort.NEWEST,
    val nextCursor: String? = null, val loading: Boolean = false, val loaded: Boolean = false,
    val error: String? = null, val message: String? = null, val operating: Set<String> = emptySet(),
    val generation: Long = 0, val seenCursors: Set<String> = emptySet(),
    val removedIds: Set<String> = emptySet(), val removedFeeds: Set<String> = emptySet(),
) {
    val isEmpty get() = loaded && items.isEmpty()
    override fun toString() = "DiscoveryQueueState(items=${items.size}, loading=$loading, sort=$sort)"
}
data class DiscoveryFeedsState(
    val items: List<DiscoveryFeed> = emptyList(), val loading: Boolean = false, val loaded: Boolean = false,
    val error: String? = null, val message: String? = null, val operating: Set<String> = emptySet(),
    val retryAtMillis: Map<String, Long> = emptyMap(), val generation: Long = 0,
) { override fun toString() = "DiscoveryFeedsState(items=${items.size}, loading=$loading)" }
data class DiscoveryTopicsState(
    val topics: List<TopicWeight> = emptyList(), val pinned: List<PinnedTopic> = emptyList(),
    val loading: Boolean = false, val loaded: Boolean = false, val error: String? = null,
    val message: String? = null, val operating: Set<String> = emptySet(), val generation: Long = 0,
) { override fun toString() = "DiscoveryTopicsState(topics=${topics.size}, pinned=${pinned.size}, loading=$loading)" }
data class DiscoveryState(
    val queue: DiscoveryQueueState = DiscoveryQueueState(), val feeds: DiscoveryFeedsState = DiscoveryFeedsState(),
    val topics: DiscoveryTopicsState = DiscoveryTopicsState(), val epoch: Long = 0,
) { override fun toString() = "DiscoveryState(<redacted>)" }

/** Retains server order. Bounded, deduplicated pages cannot loop on a repeated or oversized cursor. */
fun DiscoveryQueueState.withPage(page: DiscoveryPage, cursor: String?, generation: Long): DiscoveryQueueState {
    if (generation != this.generation) return this
    val fetched = page.items.orEmpty().filterNotNull().mapNotNull { it.safe() }
    val combined = ((if (cursor == null) emptyList() else items) + fetched)
        .filterNot { it.id in removedIds || it.feedId in removedFeeds }.distinctBy { it.id }.take(DISCOVERY_MAX_ITEMS)
    val seen = seenCursors + listOfNotNull(cursor)
    val next = page.nextCursor?.takeIf {
        it.isNotBlank() && it.length <= 768 && it !in seen && seen.size < 10 && combined.size < DISCOVERY_MAX_ITEMS && fetched.isNotEmpty()
    }
    return copy(items = combined, nextCursor = next, seenCursors = seen,
        loading = false, loaded = true, error = null)
}
