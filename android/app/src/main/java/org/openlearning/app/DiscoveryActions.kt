// SPDX-License-Identifier: GPL-3.0-only
package org.openlearning.app

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import retrofit2.HttpException

/** Identity changes even for an A → B → A connection switch. Never reconstruct this for each operation. */
class DiscoverySession(val scope: String, val api: DiscoveryService) {
    override fun toString() = "DiscoverySession(<redacted>)"
}

class DiscoveryActions(
    private val scope: CoroutineScope,
    private val session: () -> DiscoverySession?,
    private val onIngested: suspend (DiscoverySession, DiscoveryItem, IngestJob) -> Unit = { _, _, _ -> },
    private val clock: () -> Long = System::currentTimeMillis,
    private val topicsAllowed: suspend (String) -> Boolean = { true },
) {
    private val mutable = MutableStateFlow(DiscoveryState())
    val state = mutable.asStateFlow()
    private var queueLifetime = 0L
    private var feedLifetime = 0L
    private var topicLifetime = 0L
    private data class Ticket(val session: DiscoverySession, val epoch: Long)
    private fun ticket() = session()?.let { Ticket(it, state.value.epoch) }
    private fun current(ticket: Ticket) = session() === ticket.session && state.value.epoch == ticket.epoch
    private fun update(block: (DiscoveryState) -> DiscoveryState) { mutable.update(block) }
    private fun guarded(ticket: Ticket, predicate: (DiscoveryState) -> Boolean = { true }, block: (DiscoveryState) -> DiscoveryState) {
        mutable.update { value -> if (session() === ticket.session && value.epoch == ticket.epoch && predicate(value)) block(value) else value }
    }
    private fun queue(block: (DiscoveryQueueState) -> DiscoveryQueueState) = update { it.copy(queue = block(it.queue)) }
    private fun feeds(block: (DiscoveryFeedsState) -> DiscoveryFeedsState) = update { it.copy(feeds = block(it.feeds)) }
    private fun topics(block: (DiscoveryTopicsState) -> DiscoveryTopicsState) = update { it.copy(topics = block(it.topics)) }

    /** User actions run on the view-model dispatcher; clear can also be called by a forget worker. */
    fun loadQueue() = startQueue()
    private fun startQueue(message: String? = null, captured: Ticket? = ticket(), expectedGeneration: Long? = null) {
        val ticket = captured ?: return
        var generation: Long? = null
        guarded(ticket, { expectedGeneration == null || it.queue.generation == expectedGeneration }) { value ->
            generation = value.queue.generation + 1
            value.copy(queue = value.queue.copy(loading = true, error = null, message = message,
                generation = generation!!, seenCursors = emptySet()))
        }
        generation?.let { fetchQueue(ticket, null, it) }
    }

    fun nextPage() {
        val q = state.value.queue
        val cursor = q.nextCursor ?: return
        if (q.loading || q.items.size >= DISCOVERY_MAX_ITEMS) return
        val ticket = ticket() ?: return
        queue { it.copy(loading = true, error = null) }
        fetchQueue(ticket, cursor, q.generation)
    }

    fun sortQueue(sort: DiscoverySort) {
        if (state.value.queue.sort == sort && state.value.queue.loaded) return
        queue { it.copy(sort = sort, items = emptyList(), loaded = false, nextCursor = null) }
        startQueue()
    }

    private fun fetchQueue(ticket: Ticket, cursor: String?, generation: Long) {
        val sort = state.value.queue.sort
        fun fresh() = current(ticket) && state.value.queue.generation == generation
        fun queue(block: (DiscoveryQueueState) -> DiscoveryQueueState) = guarded(ticket, { it.queue.generation == generation }) { it.copy(queue = block(it.queue)) }
        scope.launch {
            try {
                val allowedBefore = topicsAllowed(ticket.session.scope)
                if (!fresh()) return@launch
                if (!allowedBefore && sort == DiscoverySort.RELEVANCE) {
                    queue { it.copy(items = emptyList(), nextCursor = null, loaded = true, loading = false, error = TOPICS_WAITING) }
                    return@launch
                }
                val response = ticket.session.api.discover(sort.wire, DISCOVERY_PAGE_LIMIT, cursor)
                val allowedAfter = topicsAllowed(ticket.session.scope)
                if (!fresh()) return@launch
                if (!allowedAfter && sort == DiscoverySort.RELEVANCE) {
                    queue { it.copy(items = emptyList(), nextCursor = null, loaded = true, loading = false, error = TOPICS_WAITING) }
                } else {
                    val safe = if (allowedAfter) response else response.copy(items = response.items.orEmpty().filterNotNull().map { it.copy(matchedTopics = emptyList()) })
                    queue { it.withPage(safe, cursor, generation) }
                }
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (error: Exception) {
                if (!fresh()) return@launch
                if (cursor != null && sort == DiscoverySort.RELEVANCE && error is HttpException && error.code() == 409) {
                    startQueue(DISCOVERY_RANKING_CHANGED, ticket, generation)
                } else queue { it.copy(loading = false, error = discoveryError(error)) }
            }
        }
    }

    fun loadFeeds() {
        val ticket = ticket() ?: return
        feeds { it.copy(loading = true, error = null, generation = it.generation + 1) }
        val generation = state.value.feeds.generation
        fun feeds(block: (DiscoveryFeedsState) -> DiscoveryFeedsState) = guarded(ticket, { it.feeds.generation == generation }) { it.copy(feeds = block(it.feeds)) }
        scope.launch {
            try {
                val rows = ticket.session.api.discoveryFeeds().orEmpty().filterNotNull().mapNotNull { it.safe() }.distinctBy { it.id }.take(50)
                if (current(ticket) && state.value.feeds.generation == generation) feeds { it.copy(items = rows, loading = false, loaded = true, error = null) }
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (error: Exception) {
                if (current(ticket) && state.value.feeds.generation == generation) feeds { it.copy(loading = false, error = discoveryError(error)) }
            }
        }
    }

    fun addFeed(input: String) {
        val url = validFeedUrl(input)
        if (url == null) { feeds { it.copy(message = "Enter a complete HTTP or HTTPS feed URL without credentials.") }; return }
        if (state.value.feeds.items.size >= 50) { feeds { it.copy(message = "You can subscribe to up to 50 feeds.") }; return }
        mutateFeed(ADD_FEED) { ticket ->
            val result = ticket.session.api.addDiscoveryFeed(FeedRequest(url)).safe() ?: throw IllegalStateException()
            val transform = { feedState: DiscoveryFeedsState -> feedState.copy(items = (listOf(result) + feedState.items).distinctBy { it.id }.take(50), message = "Feed added.") }
            transform
        }
    }

    fun removeFeed(feed: DiscoveryFeed) {
        if (state.value.feeds.items.none { it.id == feed.id }) return
        mutateFeed(feed.id) { ticket ->
            val response = ticket.session.api.deleteDiscoveryFeed(feed.id)
            if (!response.isGone()) throw HttpException(response)
            guarded(ticket) { value -> value.copy(queue = value.queue.let { q -> q.copy(items = q.items.filterNot { it.feedId == feed.id },
                removedFeeds = (q.removedFeeds + feed.id).toList().takeLast(DISCOVERY_MAX_ITEMS).toSet(), generation = q.generation + 1, loading = false) }) }
            val transform = { feedState: DiscoveryFeedsState -> feedState.copy(items = feedState.items.filterNot { it.id == feed.id },
                retryAtMillis = feedState.retryAtMillis - feed.id, message = "Feed removed.") }
            transform
        }
    }

    fun refreshFeed(feed: DiscoveryFeed) {
        if (state.value.feeds.items.none { it.id == feed.id }) return
        if ((state.value.feeds.retryAtMillis[feed.id] ?: 0) > clock()) {
            feeds { it.copy(message = "The server is busy. Wait before trying again.") }; return
        }
        mutateFeed(feed.id) { ticket ->
            val result = ticket.session.api.refreshDiscoveryFeed(feed.id).safe() ?: throw IllegalStateException()
            val transform = { feedState: DiscoveryFeedsState -> feedState.copy(items = feedState.items.map { if (it.id == feed.id) result else it },
                retryAtMillis = feedState.retryAtMillis - feed.id,
                message = if (result.lastError == null) "Feed refreshed." else feedErrorMessage(result.lastError)) }
            transform
        }
    }

    private fun mutateFeed(id: String, work: suspend (Ticket) -> (DiscoveryFeedsState) -> DiscoveryFeedsState) {
        if (id in state.value.feeds.operating) return
        val ticket = ticket() ?: return
        val lifetime = feedLifetime
        fun fresh() = current(ticket) && lifetime == feedLifetime
        fun feeds(block: (DiscoveryFeedsState) -> DiscoveryFeedsState) = guarded(ticket, { lifetime == feedLifetime }) { it.copy(feeds = block(it.feeds)) }
        feeds { it.copy(operating = it.operating + id, message = null) }
        scope.launch {
            try {
                val result = work(ticket)
                if (fresh()) feeds { result(it).copy(loading = false, loaded = true, generation = it.generation + 1) }
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (error: Exception) {
                if (fresh()) feeds { f ->
                    val retry = if (error is HttpException && error.code() == 429)
                        f.retryAtMillis + (id to (clock() + discoveryRetryDelay(error.response()?.headers()?.get("Retry-After"), clock()))) else f.retryAtMillis
                    f.copy(message = discoveryError(error), retryAtMillis = retry)
                }
            } finally { if (fresh()) feeds { it.copy(operating = it.operating - id) } }
        }
    }

    fun loadTopics() {
        val ticket = ticket() ?: return
        topics { it.copy(loading = true, error = null, generation = it.generation + 1) }
        val generation = state.value.topics.generation
        fun fresh() = current(ticket) && state.value.topics.generation == generation
        fun topics(block: (DiscoveryTopicsState) -> DiscoveryTopicsState) = guarded(ticket, { it.topics.generation == generation }) { it.copy(topics = block(it.topics)) }
        scope.launch {
            try {
                val allowedBefore = topicsAllowed(ticket.session.scope)
                if (!fresh()) return@launch
                if (!allowedBefore) { blockTopics(ticket, generation); return@launch }
                val response = ticket.session.api.discoveryTopics()
                val allowedAfter = topicsAllowed(ticket.session.scope)
                if (!fresh()) return@launch
                if (!allowedAfter) { blockTopics(ticket, generation); return@launch }
                val derived = response.topics.orEmpty().filterNotNull().filter { !it.term.isNullOrBlank() }.take(50)
                val pins = response.pinned.orEmpty().filterNotNull().filter { !it.id.isNullOrBlank() && !it.term.isNullOrBlank() }.distinctBy { it.id }.take(30)
                topics { it.copy(topics = derived, pinned = pins, loading = false, loaded = true, error = null) }
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (error: Exception) { if (fresh()) topics { it.copy(loading = false, error = discoveryError(error)) } }
        }
    }

    private fun blockTopics(ticket: Ticket, generation: Long? = null) = guarded(ticket,
        { generation == null || it.topics.generation == generation }) {
        it.copy(topics = it.topics.copy(topics = emptyList(), pinned = emptyList(), loading = false, loaded = true, error = TOPICS_WAITING),
            queue = it.queue.copy(items = it.queue.items.map { row -> row.copy(matchedTopics = emptyList()) }))
    }

    fun pinTopic(input: String) {
        val term = normalizeTopic(input)
        if (term == null) { topics { it.copy(message = DISCOVERY_TOPIC_INVALID) }; return }
        if (state.value.topics.pinned.size >= 30) { topics { it.copy(message = "You can pin up to 30 topics.") }; return }
        mutateTopic(PIN_TOPIC) { ticket ->
            val pin = ticket.session.api.pinDiscoveryTopic(PinTopicRequest(term))
            if (pin.id.isNullOrBlank() || pin.term.isNullOrBlank()) throw IllegalStateException()
            val transform = { topicState: DiscoveryTopicsState -> topicState.copy(pinned = (topicState.pinned + pin).distinctBy { it.id }.take(30), message = "Topic pinned.") }
            transform
        }
    }

    fun unpinTopic(pin: PinnedTopic) {
        if (state.value.topics.pinned.none { it.id == pin.id }) return
        mutateTopic(pin.id) { ticket ->
            val response = ticket.session.api.unpinDiscoveryTopic(pin.id)
            if (!response.isGone()) throw HttpException(response)
            val transform = { topicState: DiscoveryTopicsState -> topicState.copy(pinned = topicState.pinned.filterNot { it.id == pin.id }, message = "Topic unpinned.") }
            transform
        }
    }

    private fun mutateTopic(id: String, work: suspend (Ticket) -> (DiscoveryTopicsState) -> DiscoveryTopicsState) {
        if (state.value.topics.operating.isNotEmpty()) return
        val ticket = ticket() ?: return
        val lifetime = topicLifetime
        fun fresh() = current(ticket) && lifetime == topicLifetime
        fun topics(block: (DiscoveryTopicsState) -> DiscoveryTopicsState) = guarded(ticket, { lifetime == topicLifetime }) { it.copy(topics = block(it.topics)) }
        topics { it.copy(operating = it.operating + id, message = null) }
        scope.launch {
            try {
                if (!topicsAllowed(ticket.session.scope)) { if (fresh()) blockTopics(ticket); return@launch }
                if (!fresh()) return@launch
                val result = work(ticket)
                if (fresh()) {
                    topics { result(it).copy(topics = emptyList(), loading = false, generation = it.generation + 1) }
                    guarded(ticket) { current -> current.copy(queue = current.queue.let { it.copy(items = if (it.sort == DiscoverySort.RELEVANCE) emptyList() else it.items.map { row -> row.copy(matchedTopics = emptyList()) },
                        nextCursor = if (it.sort == DiscoverySort.RELEVANCE) null else it.nextCursor, generation = it.generation + 1, loading = false) }) }
                    loadTopics()
                }
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (error: Exception) { if (fresh()) topics { it.copy(message = discoveryError(error)) } }
            finally { if (fresh()) topics { it.copy(operating = it.operating - id) } }
        }
    }

    fun ingest(item: DiscoveryItem) = actOnItem(item, ingest = true)
    fun dismiss(item: DiscoveryItem) = actOnItem(item, ingest = false)
    private fun actOnItem(item: DiscoveryItem, ingest: Boolean) {
        val q = state.value.queue
        if (item.id in q.operating || item.id in q.removedIds || q.items.none { it.id == item.id }) return
        val ticket = ticket() ?: return
        val lifetime = queueLifetime
        fun fresh() = current(ticket) && lifetime == queueLifetime
        fun queue(block: (DiscoveryQueueState) -> DiscoveryQueueState) = guarded(ticket) { it.copy(queue = block(it.queue)) }
        queue { it.copy(operating = it.operating + item.id, message = null) }
        scope.launch {
            try {
                val job = if (ingest) ticket.session.api.ingestDiscovered(item.id) else {
                    val response = ticket.session.api.dismissDiscovered(item.id)
                    if (!response.isGone()) throw HttpException(response)
                    null
                }
                if (session() !== ticket.session) return@launch
                // Once accepted, never repeat POST if tracking later fails. A concurrent page cannot bring it back.
                queue { it.copy(items = it.items.filterNot { row -> row.id == item.id },
                    removedIds = (it.removedIds + item.id).toList().takeLast(DISCOVERY_MAX_ITEMS).toSet(), generation = it.generation + 1, loading = false) }
                if (job != null) {
                    if (job.id.isNullOrBlank()) { if (fresh()) queue { it.copy(message = JOB_TRACKING_FAILED) }; return@launch }
                    try {
                        onIngested(ticket.session, item, job)
                        if (fresh()) queue { it.copy(message = "Article added to imports.") }
                    } catch (cancelled: CancellationException) { throw cancelled }
                    catch (error: Exception) { if (fresh()) queue { it.copy(message = JOB_TRACKING_FAILED) } }
                } else if (fresh()) queue { it.copy(message = "Article dismissed.") }
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (error: Exception) { if (fresh()) queue { it.copy(message = discoveryError(error)) } }
            finally { if (current(ticket)) queue { it.copy(operating = it.operating - item.id) } }
        }
    }

    /** Clear increments a monotonic epoch: an old A request is stale even after an A → B → A switch. */
    fun clear() { mutable.update { DiscoveryState(epoch = it.epoch + 1) } }
    fun closeQueue() {
        queueLifetime++
        queue { DiscoveryQueueState(sort = it.sort, generation = it.generation + 1, removedIds = it.removedIds, removedFeeds = it.removedFeeds) }
    }
    fun closeFeeds() { feedLifetime++; feeds { DiscoveryFeedsState(generation = it.generation + 1, retryAtMillis = it.retryAtMillis) } }
    fun closeTopics() { topicLifetime++; topics { DiscoveryTopicsState(generation = it.generation + 1) } }
    fun messageShown() = update { it.copy(queue = it.queue.copy(message = null), feeds = it.feeds.copy(message = null), topics = it.topics.copy(message = null)) }

    companion object {
        private const val ADD_FEED = "add"
        private const val PIN_TOPIC = "pin"
        private const val TOPICS_WAITING = "Topics will be available after forgotten notes are removed from the server."
        private const val JOB_TRACKING_FAILED = "The article was accepted, but its import could not be shown. Check Imports before trying again."
    }
}
