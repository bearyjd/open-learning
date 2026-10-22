package org.openlearning.app

import com.google.gson.JsonParseException
import kotlinx.coroutines.CancellationException

/** Pure decisions behind the forget queue's hardening. Nothing here touches Room or WorkManager. */

/** How a worker is enqueued. KEEP is only safe for the idempotent resume: it can lose to a worker that is finishing. */
enum class ForgetEnqueue { KEEP, APPEND_OR_REPLACE }
enum class ForgetTrigger { NEW_FORGET, RETRY, RESUME }

/**
 * A new forget or an explicit Retry must never be dropped because a previous worker is still finishing (it may have
 * published its outcome before WorkManager recorded it), so they append. The resume that runs on every connect and
 * every time the notes list opens keeps a running worker instead, so repeated calls cannot pile up a chain.
 */
fun forgetEnqueuePolicy(trigger: ForgetTrigger) = if (trigger == ForgetTrigger.RESUME) ForgetEnqueue.KEEP else ForgetEnqueue.APPEND_OR_REPLACE

const val FORGET_ID_MAX_LENGTH = 64
private val FORGET_ID_CHARSET = Regex("[0-9a-fA-F-]+")

/** The server issues uuid4 strings (36 chars, hex and dashes). Anything else is never queued: it goes into WorkManager input. */
fun validForgetDocumentId(id: String) = id.length in 1..FORGET_ID_MAX_LENGTH && FORGET_ID_CHARSET.matches(id)

class InvalidForgetIdException : IllegalArgumentException("Invalid document id")

const val FORGET_INVALID_ID_MESSAGE =
    "This note has an id the server does not issue, so nothing was queued or hidden. Refresh Your notes and try again."

/**
 * Schedules each row on its own: a row whose worker cannot be built (for example an id too large for WorkManager input)
 * is passed to [onFailed] and the others still run. Returns the number of rows that failed. Cancellation propagates.
 */
suspend fun scheduleEach(
    rows: List<PendingForget>, trigger: ForgetTrigger, schedule: (String, String, ForgetEnqueue) -> Unit, onFailed: suspend (PendingForget) -> Unit,
): Int {
    var failed = 0
    for (row in rows) {
        try { schedule(row.scope, row.documentId, forgetEnqueuePolicy(trigger)) }
        catch (cancelled: CancellationException) { throw cancelled }
        catch (error: Exception) { failed++; onFailed(row) }
    }
    return failed
}

/** The marker hides the note from in-flight refreshes only while the commit can still succeed; any failure removes it. */
suspend fun <T> markedUntilCommit(mark: () -> Unit, unmark: () -> Unit, commit: suspend () -> T): T {
    mark()
    try { return commit() } catch (error: Throwable) { unmark(); throw error }
}

/** Runs every step after the commit. A failure is counted, never raised: the commit is the success point and resume recovers the rest. */
fun runBestEffort(vararg steps: () -> Unit): Int {
    var failed = 0
    for (step in steps) {
        try { step() } catch (cancelled: CancellationException) { throw cancelled } catch (error: Exception) { failed++ }
    }
    return failed
}

/** The confirmation cleanup repeats only what is certain: rows linked to the document. The title fallback runs once, at forget time. */
fun linkedRowsToPurge(completedNoteRows: List<PendingIngest>, documentId: String) = completedNoteRows.filter { it.documentId == documentId }

/** A malformed cached payload cannot be matched to a document; null instead of an exception that would roll the forget back. */
fun documentIdOfPayload(payload: String): String? =
    try { apiGson.fromJson(payload, Insight::class.java)?.documentId } catch (error: JsonParseException) { null }

fun Insight.isHiddenBy(hidden: Set<String>) = documentId.isNotBlank() && documentId in hidden

const val REVIEW_FETCH_CAP = 20

/** Reviews that may be shown, and the document id learned for each (insight id to document id). */
data class ReviewView(val reviews: List<Review>, val documents: Map<String, String>)

/**
 * Reviews carry only an insight id. With a forget queued, a review whose document cannot be resolved is hidden rather
 * than risk showing it. [cached] is free; [fetch] is a network call, made for at most [cap] reviews and not at all after
 * the first failure (offline). Nothing is looked up when nothing is hidden.
 */
suspend fun resolveReviews(
    reviews: List<Review>, hidden: Set<String>, cached: suspend (Review) -> String?, fetch: suspend (Review) -> String, cap: Int = REVIEW_FETCH_CAP,
): ReviewView {
    if (hidden.isEmpty()) return ReviewView(reviews, emptyMap())
    var fetched = 0; var networkDown = false
    val documents = LinkedHashMap<String, String>()
    val shown = reviews.filter { review ->
        val documentId = cached(review) ?: if (fetched < cap && !networkDown) {
            fetched++
            try { fetch(review) } catch (cancelled: CancellationException) { throw cancelled } catch (error: Exception) { networkDown = true; null }
        } else null
        if (documentId != null) documents[review.insightId] = documentId
        documentId != null && (documentId.isBlank() || documentId !in hidden)
    }
    return ReviewView(shown, documents.filterKeys { key -> shown.any { it.insightId == key } })
}

const val FORGET_TOKEN_ROTATION_NOTICE =
    "Notes you forgot while signed in with the previous token are still waiting to be deleted from the server. " +
        "Reconnect with that token to finish."

/** One-time notice: the server is unchanged but queued forgets belong to another token's scope. Never names the server. */
fun tokenRotationNotice(previousServer: String?, server: String, otherScopeRows: List<PendingForget>): String? =
    if (previousServer == server && otherScopeRows.isNotEmpty()) FORGET_TOKEN_ROTATION_NOTICE else null

const val FORGET_STOP_MESSAGE =
    "The note stays on the server and will reappear here on the next refresh. This only stops this device from trying to delete it."

fun canStopTrying(row: PendingForget) = row.status == FORGET_FAILED
