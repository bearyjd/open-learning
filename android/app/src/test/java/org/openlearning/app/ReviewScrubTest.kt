package org.openlearning.app

import kotlinx.coroutines.runBlocking
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.*
import org.junit.Test

/** Reviews without a document id are resolved on every load, so a forget that starts later can still scrub them. */
class ReviewScrubTest {
    private fun review(id: String, insight: String, doc: String = "") = Review(id, insight, "q", "2026-01-01", 1.0, documentId = doc)

    @Test fun blankIdReviewsAreResolvedEvenWhenNothingIsHidden() = runBlocking {
        val view = resolveReviews(listOf(review("r1", "i1"), review("r2", "i2")), emptySet(), { if (it.insightId == "i1") "d1" else null }, { "d2" })
        assertEquals(listOf("r1", "r2"), view.reviews.map { it.id })
        assertEquals(mapOf("i1" to "d1", "i2" to "d2"), view.documents)
    }
    @Test fun unresolvableBlankIdReviewFailsClosedEvenWhenNothingIsHidden() = runBlocking {
        val view = resolveReviews(listOf(review("r1", "i1")), emptySet(), { null }, { throw java.io.IOException() })
        assertTrue(view.reviews.isEmpty())
    }
    @Test fun aNotFoundDropsOnlyThatReviewWhenNothingIsHidden() = runBlocking {
        val view = resolveReviews(listOf(review("r1", "i1"), review("r2", "i2")), emptySet(), { null }, { if (it.insightId == "i1") throw http404() else "d2" })
        assertEquals(listOf("r2"), view.reviews.map { it.id })
    }
    @Test fun reviewsKnowingTheirDocumentNeedNoLookup() = runBlocking {
        val reviews = listOf(review("r1", "i1", "d1"))
        assertEquals(reviews, resolveReviews(reviews, emptySet(), { error("cache") }, { error("network") }).reviews)
    }
    @Test fun lookupsAreCappedEvenWhenNothingIsHidden() = runBlocking {
        var calls = 0
        val view = resolveReviews((1..30).map { review("r$it", "i$it") }, emptySet(), { null }, { calls++; "d$calls" })
        assertEquals(REVIEW_FETCH_CAP, calls); assertEquals(REVIEW_FETCH_CAP, view.reviews.size)
    }
    @Test fun reviewLoadedBeforeAForgetIsScrubbedWhenTheForgetStarts() = runBlocking {
        val view = resolveReviews(listOf(review("r1", "i1"), review("r2", "i2")), emptySet(), { if (it.insightId == "i1") "d1" else "d2" }, { error("network") })
        val loaded = ScreenState(reviews = view.reviews, reviewDocs = view.documents)
        val afterForget = loaded.withoutHidden(setOf("d1"))
        assertEquals(listOf("r2"), afterForget.reviews.map { it.id })
    }
    @Test fun aBlankCachedDocumentIdIsUnresolvedAndFallsThroughToTheNetwork() = runBlocking {
        val view = resolveReviews(listOf(review("r1", "i1")), emptySet(), { "" }, { "d9" })
        assertEquals(mapOf("i1" to "d9"), view.documents)
    }
    @Test fun aBlankCachedIdWithAFailedLookupIsDroppedAndNeverMapped() = runBlocking {
        val view = resolveReviews(listOf(review("r1", "i1")), emptySet(), { "" }, { throw java.io.IOException() })
        assertTrue(view.reviews.isEmpty()); assertTrue(view.documents.isEmpty())
    }
    @Test fun aBlankIdFromTheNetworkIsDroppedAndNeverMapped() = runBlocking {
        val view = resolveReviews(listOf(review("r1", "i1"), review("r2", "i2")), emptySet(), { null }, { if (it.insightId == "i1") "" else "d2" })
        assertEquals(listOf("r2"), view.reviews.map { it.id }); assertEquals(mapOf("i2" to "d2"), view.documents)
    }
    @Test fun aBlankIdFromTheNetworkIsDroppedEvenWithNothingHiddenAndDoesNotStopLaterLookups() = runBlocking {
        var calls = 0
        val view = resolveReviews(listOf(review("r1", "i1"), review("r2", "i2")), emptySet(), { null }, { calls++; if (calls == 1) "" else "d2" })
        assertEquals(2, calls); assertEquals(listOf("r2"), view.reviews.map { it.id })
    }
    @Test fun aBlankIdInThePayloadIsTreatedAsUnresolved() = runBlocking {
        val view = resolveReviews(listOf(review("r1", "i1", "")), emptySet(), { null }, { "" })
        assertTrue(view.reviews.isEmpty()); assertTrue(view.documents.isEmpty())
    }
    @Test fun aNonBlankResolutionStillWorks() = runBlocking {
        val view = resolveReviews(listOf(review("r1", "i1")), setOf("dx"), { "d1" }, { error("network") })
        assertEquals(mapOf("i1" to "d1"), view.documents)
    }
    private fun http404() = retrofit2.HttpException(retrofit2.Response.error<Any>(404, "".toResponseBody()))
}
