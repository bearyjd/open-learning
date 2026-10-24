// SPDX-License-Identifier: GPL-3.0-only
package org.openlearning.app

import retrofit2.Response
import retrofit2.http.Body
import retrofit2.http.DELETE
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Path
import retrofit2.http.Query

const val DISCOVERY_PAGE_LIMIT = 50
const val DISCOVERY_MAX_ITEMS = 500

/** These values only live in the discovery controller's memory, never in Room or saved state. */
data class DiscoveryItem(
    val id: String = "", val feedId: String = "", val url: String = "", val title: String = "",
    val publishedAt: String? = null, val snippet: String = "", val createdAt: String = "",
    val matchedTopics: List<String> = emptyList(),
) {
    override fun toString() = "DiscoveryItem(<redacted>)"
}
data class DiscoveryPage(val items: List<DiscoveryItem?>? = emptyList(), val nextCursor: String? = null) {
    override fun toString() = "DiscoveryPage(<redacted>)"
}
data class DiscoveryFeed(
    val id: String = "", val url: String = "", val title: String = "", val lastFetchedAt: String? = null,
    val lastError: String? = null, val paused: Boolean = false,
) {
    override fun toString() = "DiscoveryFeed(<redacted>)"
}
data class TopicWeight(val term: String = "", val weight: Double = 0.0, val count: Int = 0) {
    override fun toString() = "TopicWeight(<redacted>)"
}
data class PinnedTopic(val id: String = "", val term: String = "", val createdAt: String = "") {
    override fun toString() = "PinnedTopic(<redacted>)"
}
data class TopicsResponse(val topics: List<TopicWeight?>? = emptyList(), val pinned: List<PinnedTopic?>? = emptyList()) {
    override fun toString() = "TopicsResponse(<redacted>)"
}
data class FeedRequest(val url: String) { override fun toString() = "FeedRequest(<redacted>)" }
data class PinTopicRequest(val term: String) { override fun toString() = "PinTopicRequest(<redacted>)" }

/** Inherited by LearningApi so all requests use its existing token, HTTP consent and no-redirect client. */
interface DiscoveryService {
    @GET("api/v1/discover") suspend fun discover(@Query("sort") sort: String = "newest",
        @Query("limit") limit: Int = DISCOVERY_PAGE_LIMIT, @Query("cursor") cursor: String? = null): DiscoveryPage
    @GET("api/v1/feeds") suspend fun discoveryFeeds(): List<DiscoveryFeed>
    @POST("api/v1/feeds") suspend fun addDiscoveryFeed(@Body body: FeedRequest): DiscoveryFeed
    @DELETE("api/v1/feeds/{id}") suspend fun deleteDiscoveryFeed(@Path("id") id: String): Response<Unit>
    @POST("api/v1/feeds/{id}/refresh") suspend fun refreshDiscoveryFeed(@Path("id") id: String): DiscoveryFeed
    @POST("api/v1/discover/{id}/ingest") suspend fun ingestDiscovered(@Path("id") id: String): IngestJob
    @POST("api/v1/discover/{id}/dismiss") suspend fun dismissDiscovered(@Path("id") id: String): Response<Unit>
    @GET("api/v1/topics") suspend fun discoveryTopics(): TopicsResponse
    @POST("api/v1/topics/pinned") suspend fun pinDiscoveryTopic(@Body body: PinTopicRequest): PinnedTopic
    @DELETE("api/v1/topics/pinned/{id}") suspend fun unpinDiscoveryTopic(@Path("id") id: String): Response<Unit>
}

internal fun DiscoveryItem.safe(): DiscoveryItem? = id?.takeIf { it.isNotBlank() }?.let {
    copy(id = it, feedId = feedId.orEmpty(), url = url.orEmpty(), title = title.orEmpty().ifBlank { "Untitled" },
        snippet = snippet.orEmpty(), createdAt = createdAt.orEmpty(),
        matchedTopics = matchedTopics.orEmpty().filterNotNull().filter { topic -> topic.isNotBlank() }.take(5))
}
internal fun DiscoveryFeed.safe(): DiscoveryFeed? = id?.takeIf { it.isNotBlank() }?.let {
    copy(id = it, url = url.orEmpty(), title = title.orEmpty())
}
