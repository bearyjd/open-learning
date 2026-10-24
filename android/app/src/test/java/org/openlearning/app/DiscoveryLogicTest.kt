// SPDX-License-Identifier: GPL-3.0-only
package org.openlearning.app

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.runBlocking
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.*
import org.junit.Test
import retrofit2.HttpException
import retrofit2.Response
import java.io.IOException

class DiscoveryLogicTest {
    private fun http(code: Int, detail: String) = HttpException(Response.error<Unit>(code,
        "{\"detail\":\"$detail\"}".toResponseBody("application/json".toMediaType())))

    @Test fun topicValidationMatchesUnicodeBackendRules() {
        assertEquals("café physics", normalizeTopic("  ＣＡＦÉ...Physics  "))
        assertEquals("i\u0307stanbul", normalizeTopic("İSTANBUL"))
        assertEquals("a b c d e", normalizeTopic("a-b-c-d-e"))
        assertEquals("𐐨𐐨𐐨", normalizeTopic("𐐀𐐀𐐀"))
        assertNotNull(normalizeTopic("𐐀".repeat(80)))
        listOf("ab", "a b c d e f", "secret\u200Dtopic", "topic\n", "x".repeat(81), "!!!", "\u0301\u0302").forEach {
            assertNull("Rejected backend input", normalizeTopic(it))
        }
    }

    @Test fun feedValidationAllowsHttpHttpsAndRejectsCredentialsOrOtherSchemes() {
        assertEquals("https://example.org/feed?q=1", validFeedUrl(" https://example.org/feed?q=1 "))
        assertNotNull(validFeedUrl("http://localhost/feed"))
        listOf("ftp://example.org", "https://user:password@example.org", "file:///feed", "https://", "https://example.org/\nfeed").forEach {
            assertNull(validFeedUrl(it))
        }
    }

    @Test fun errorsOnlyExposeFixedMessagesForAllowlistedDetails() {
        assertEquals("This feed is already subscribed.", discoveryError(http(409, "Feed already subscribed")))
        assertEquals("You can subscribe to up to 50 feeds.", discoveryError(http(409, "Feed subscription limit reached")))
        assertEquals("You can pin up to 30 topics.", discoveryError(http(409, "Pinned topic limit reached")))
        assertEquals("That feed URL is not allowed.", discoveryError(http(422, "feed_invalid_url")))
        assertFalse(discoveryError(http(422, "private secret URL")).contains("private"))
        assertEquals(DISCOVERY_CONNECTION_ERROR, discoveryError(IOException("private secret URL")))
        assertFalse(feedErrorMessage("private secret URL").contains("private"))
    }

    @Test fun queueDeduplicatesBoundsAndStopsRepeatedCursors() {
        val state = DiscoveryQueueState(loading = true, generation = 2)
        val page = DiscoveryPage(items = listOf(item("a"), item("a")), nextCursor = "next")
        val first = state.withPage(page, null, 2)
        assertEquals(listOf("a"), first.items.map { it.id })
        val next = first.copy(loading = true).withPage(DiscoveryPage(listOf(item("a"), item("b")), "next"), "next", 2)
        assertEquals(listOf("a", "b"), next.items.map { it.id }); assertNull(next.nextCursor)
        assertSame(first, first.withPage(page, null, 1))
        val full = DiscoveryQueueState(loading = true).withPage(DiscoveryPage((1..700).map { item("$it") }, "more"), null, 0)
        assertEquals(DISCOVERY_MAX_ITEMS, full.items.size); assertNull(full.nextCursor)
    }

    @Test fun gsonMissingNullFieldsAndPrivateToStringsAreSafe() {
        val raw = apiGson.fromJson("""{"items":[{"id":"x","title":null,"matched_topics":["secret",null,"b","c","d","e","f"]},null],"next_cursor":null}""", DiscoveryPage::class.java)
        val state = DiscoveryQueueState().withPage(raw, null, 0)
        assertEquals("Untitled", state.items.single().title)
        assertEquals(5, state.items.single().matchedTopics.size)
        assertFalse(state.toString().contains("secret"))
        assertFalse(state.items.single().toString().contains("secret"))
        assertFalse(TopicWeight("secret", 1.0, 1).toString().contains("secret"))
        assertFalse(PinnedTopic("p", "secret", "date").toString().contains("secret"))
        assertFalse(FeedRequest("https://secret.example").toString().contains("secret"))
    }

    private fun item(id: String) = DiscoveryItem(id = id, url = "https://example.org/$id", title = "private $id")

    private class FakeApi : DiscoveryService {
        val pages = mutableListOf<Pair<String, String?>>()
        var getPage: suspend (String, String?) -> DiscoveryPage = { _, _ -> DiscoveryPage() }
        var getTopics: suspend () -> TopicsResponse = { TopicsResponse() }
        var ingestCount = 0
        var ingestResult: suspend () -> IngestJob = { IngestJob("job", "queued") }
        var feedRows: List<DiscoveryFeed> = emptyList()
        var refreshResult: suspend (String) -> DiscoveryFeed = { DiscoveryFeed(id = it) }
        override suspend fun discover(sort: String, limit: Int, cursor: String?): DiscoveryPage { pages += sort to cursor; return getPage(sort, cursor) }
        override suspend fun discoveryFeeds() = feedRows
        override suspend fun addDiscoveryFeed(body: FeedRequest) = DiscoveryFeed(id = "f", url = body.url)
        override suspend fun deleteDiscoveryFeed(id: String): Response<Unit> = Response.success(null)
        override suspend fun refreshDiscoveryFeed(id: String) = refreshResult(id)
        override suspend fun ingestDiscovered(id: String): IngestJob { ingestCount++; return ingestResult() }
        override suspend fun dismissDiscovered(id: String): Response<Unit> = Response.success(null)
        override suspend fun discoveryTopics() = getTopics()
        override suspend fun pinDiscoveryTopic(body: PinTopicRequest) = PinnedTopic("pin", body.term, "date")
        override suspend fun unpinDiscoveryTopic(id: String): Response<Unit> = Response.success(null)
    }

    @Test fun relevance409RestartsOnceAtFirstPageWithFixedMessage() = runBlocking {
        val api = FakeApi().apply { getPage = { _, cursor -> if (cursor != null) throw http(409, "private ranking data") else DiscoveryPage(listOf(item("a")), "next") } }
        val session = DiscoverySession("scope", api)
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val actions = DiscoveryActions(scope, { session })
        actions.sortQueue(DiscoverySort.RELEVANCE); actions.nextPage()
        assertEquals(listOf("relevance" to null, "relevance" to "next", "relevance" to null), api.pages)
        assertEquals(DISCOVERY_RANKING_CHANGED, actions.state.value.queue.message)
        assertEquals(listOf("a"), actions.state.value.queue.items.map { it.id })
        scope.cancel()
    }

    @Test fun clearAndScopeIdentityDropInFlightSuccessAndFailureIncludingABA() = runBlocking {
        val response = CompletableDeferred<DiscoveryPage>()
        val topics = CompletableDeferred<TopicsResponse>()
        val api = FakeApi().apply { getPage = { _, _ -> response.await() }; getTopics = { topics.await() } }
        val original = DiscoverySession("A", api)
        var session: DiscoverySession? = original
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val actions = DiscoveryActions(scope, { session })
        actions.loadQueue(); actions.loadTopics(); actions.clear()
        session = DiscoverySession("B", api); session = DiscoverySession("A", api)
        response.complete(DiscoveryPage(listOf(item("secret")), "cursor"))
        topics.complete(TopicsResponse(listOf(TopicWeight("secret", 1.0, 1))))
        assertTrue(actions.state.value.queue.items.isEmpty()); assertTrue(actions.state.value.topics.topics.isEmpty())
        assertFalse(actions.state.value.queue.loading)
        scope.cancel()
    }

    @Test fun changingSortDropsEarlierResponseAndNeverSortsPageLocally() = runBlocking {
        val old = CompletableDeferred<DiscoveryPage>()
        val api = FakeApi().apply { getPage = { sort, _ -> if (sort == "newest") old.await() else DiscoveryPage(listOf(item("z"), item("a"))) } }
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val session = DiscoverySession("scope", api)
        val actions = DiscoveryActions(scope, { session })
        actions.loadQueue(); actions.sortQueue(DiscoverySort.RELEVANCE)
        old.complete(DiscoveryPage(listOf(item("stale"))))
        assertEquals(listOf("z", "a"), actions.state.value.queue.items.map { it.id })
        scope.cancel()
    }

    @Test fun ingestAcceptedButTrackingFailureRemovesItemAndCannotPostTwice() = runBlocking {
        val api = FakeApi().apply { getPage = { _, _ -> DiscoveryPage(listOf(item("a"))) } }
        val session = DiscoverySession("scope", api)
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val actions = DiscoveryActions(scope, { session }, { _, _, _ -> throw IOException("private") })
        actions.loadQueue(); val row = actions.state.value.queue.items.single()
        actions.ingest(row); actions.ingest(row); actions.loadQueue()
        assertEquals(1, api.ingestCount); assertTrue(actions.state.value.queue.items.isEmpty())
        assertFalse(actions.state.value.queue.message.orEmpty().contains("private"))
        scope.cancel()
    }

    @Test fun retryAfterParsesSecondsAndHttpDateWithBoundedFallback() {
        assertEquals(30_000L, discoveryRetryDelay("30", 0))
        assertEquals(60_000L, discoveryRetryDelay("Thu, 01 Jan 1970 00:01:00 GMT", 0))
        assertEquals(30_000L, discoveryRetryDelay("private", 0))
        assertEquals(86_400_000L, discoveryRetryDelay("999999999999999999999", 0))
    }

    @Test fun pendingForgetBlocksPrivateProfilesAndRelevanceAndScrubsNewestChips() = runBlocking {
        var allowed = false
        var topicCalls = 0
        val api = FakeApi().apply {
            getPage = { _, _ -> DiscoveryPage(listOf(item("a").copy(matchedTopics = listOf("forgotten")))) }
            getTopics = { topicCalls++; TopicsResponse(listOf(TopicWeight("allowed", 1.0, 1))) }
        }
        val session = DiscoverySession("scope", api)
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val actions = DiscoveryActions(scope, { session }, topicsAllowed = { allowed })
        actions.loadTopics(); actions.sortQueue(DiscoverySort.RELEVANCE)
        assertEquals(0, topicCalls); assertTrue(api.pages.isEmpty())
        actions.sortQueue(DiscoverySort.NEWEST)
        assertTrue(actions.state.value.queue.items.single().matchedTopics.isEmpty())
        allowed = true; actions.loadTopics(); actions.sortQueue(DiscoverySort.RELEVANCE)
        assertEquals(1, topicCalls); assertEquals("relevance", api.pages.last().first)
        scope.cancel()
    }

    @Test fun acceptedIngestAfterCloseOrForgetStillTracksWithoutResurrectingQueue() = runBlocking {
        for (forget in listOf(false, true)) {
            val response = CompletableDeferred<IngestJob>()
            val api = FakeApi().apply { getPage = { _, _ -> DiscoveryPage(listOf(item("a"))) }; ingestResult = { response.await() } }
            val session = DiscoverySession("scope", api)
            val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
            var tracked = 0
            val actions = DiscoveryActions(scope, { session }, { _, _, _ -> tracked++ })
            actions.loadQueue(); actions.ingest(actions.state.value.queue.items.single())
            if (forget) actions.clear() else actions.closeQueue()
            response.complete(IngestJob("job", "queued"))
            assertEquals(1, tracked); assertTrue(actions.state.value.queue.items.isEmpty())
            assertNull(actions.state.value.queue.message)
            scope.cancel()
        }
    }

    @Test fun privacyPermissionRecheckedAfterResponseAndOldErrorsCannotCrossClear() = runBlocking {
        var allowed = true
        val response = CompletableDeferred<TopicsResponse>()
        val api = FakeApi().apply { getTopics = { response.await() } }
        val session = DiscoverySession("scope", api)
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val actions = DiscoveryActions(scope, { session }, topicsAllowed = { allowed })
        actions.loadTopics(); allowed = false
        response.complete(TopicsResponse(listOf(TopicWeight("forgotten", 1.0, 1))))
        assertTrue(actions.state.value.topics.topics.isEmpty())
        assertNotNull(actions.state.value.topics.error)
        val failure = CompletableDeferred<DiscoveryPage>()
        api.getPage = { _, _ -> failure.await() }
        actions.loadQueue(); actions.clear(); failure.completeExceptionally(IOException("secret"))
        assertNull(actions.state.value.queue.error); assertFalse(actions.state.value.queue.loading)
        scope.cancel()
    }

    @Test fun refreshFailurePreservesRowsAndDeletionInvalidatesInFlightPages() = runBlocking {
        val feed = DiscoveryFeed(id = "feed")
        val api = FakeApi().apply {
            feedRows = listOf(feed)
            getPage = { _, _ -> DiscoveryPage(listOf(item("a").copy(feedId = "feed")), "next") }
        }
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val session = DiscoverySession("scope", api)
        val actions = DiscoveryActions(scope, { session })
        actions.loadQueue(); api.getPage = { _, _ -> throw IOException("secret") }; actions.loadQueue()
        assertEquals(listOf("a"), actions.state.value.queue.items.map { it.id })
        val response = CompletableDeferred<DiscoveryPage>()
        api.getPage = { _, _ -> response.await() }
        actions.loadQueue(); actions.loadFeeds(); actions.removeFeed(feed)
        response.complete(DiscoveryPage(listOf(item("a").copy(feedId = "feed"))))
        assertTrue(actions.state.value.queue.items.isEmpty())
        scope.cancel()
    }

    @Test fun feedRateLimitBlocksRepeatedRefreshUntilRetryAfter() = runBlocking {
        var now = 1_000L
        var calls = 0
        val feed = DiscoveryFeed(id = "feed")
        val api = FakeApi().apply {
            feedRows = listOf(feed)
            refreshResult = { calls++; throw http(429, "secret") }
        }
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val session = DiscoverySession("scope", api)
        val actions = DiscoveryActions(scope, { session }, clock = { now })
        actions.loadFeeds(); actions.refreshFeed(feed); actions.refreshFeed(feed)
        assertEquals(1, calls); assertEquals(31_000L, actions.state.value.feeds.retryAtMillis[feed.id])
        actions.closeFeeds(); actions.loadFeeds(); actions.refreshFeed(feed)
        assertEquals(1, calls)
        now = 31_000; actions.refreshFeed(feed); assertEquals(2, calls)
        scope.cancel()
    }

    @Test fun forgetBetweenFreshnessCheckAndAtomicPublicationCannotResurrectContent() = runBlocking {
        val response = CompletableDeferred<DiscoveryPage>()
        val api = FakeApi().apply { getPage = { _, _ -> response.await() } }
        val session = DiscoverySession("scope", api)
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        var checks = 0
        var armed = false
        lateinit var actions: DiscoveryActions
        actions = DiscoveryActions(scope, {
            if (armed && ++checks == 2) actions.clear()
            session
        })
        actions.loadQueue(); armed = true
        response.complete(DiscoveryPage(listOf(item("private"))))
        assertEquals(1L, actions.state.value.epoch)
        assertTrue(actions.state.value.queue.items.isEmpty())
        assertFalse(actions.state.value.queue.loading)
        scope.cancel()
    }
}
