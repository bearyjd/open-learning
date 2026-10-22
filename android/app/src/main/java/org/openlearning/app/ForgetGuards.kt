package org.openlearning.app

import com.google.gson.JsonParseException
import kotlinx.coroutines.CancellationException
import androidx.room.withTransaction
import retrofit2.HttpException

/** Pure decisions behind the forget queue's hardening. Nothing here touches Room or WorkManager. */

/** How a worker is enqueued. REPLACE restarts the unique work, KEEP leaves existing work alone. */
enum class ForgetEnqueue { KEEP, REPLACE }
enum class ForgetTrigger { NEW_FORGET, RETRY, RESUME }

/**
 * A new forget or an explicit Retry replaces the unique work, so it is never dropped behind a worker that is finishing.
 * That is safe because the worker re-reads its row at start and exits cleanly when it is not pending, and the server
 * DELETE is idempotent. The resume that runs on every connect and every sweep keeps work that exists, so repeated calls cannot pile up.
 */
fun forgetEnqueuePolicy(trigger: ForgetTrigger) = if (trigger == ForgetTrigger.RESUME) ForgetEnqueue.KEEP else ForgetEnqueue.REPLACE

const val FORGET_ID_MAX_LENGTH = 64
private val FORGET_ID_CHARSET = Regex("[0-9a-fA-F-]+")

/** The server issues uuid4 strings (36 chars, hex and dashes). Anything else is never queued: it goes into WorkManager input. */
fun validForgetDocumentId(id: String) = id.length in 1..FORGET_ID_MAX_LENGTH && FORGET_ID_CHARSET.matches(id)

/** A blank id is as invalid as a malformed one: the caller must see a failure, never an empty "success". */
fun requireForgetDocumentId(id: String) { if (!validForgetDocumentId(id)) throw InvalidForgetIdException() }

/** Validates, then lowercases: ids are stored and compared in lower case, so "ABC" and "abc" are one note. */
fun normalizedForgetDocumentId(id: String): String { requireForgetDocumentId(id); return id.lowercase() }

class InvalidForgetIdException : IllegalArgumentException("Invalid document id")

const val FORGET_INVALID_ID_MESSAGE =
    "This note has an id the server does not issue, so nothing was queued or hidden. Refresh Your notes and try again."

/** WorkManager accepted the request but its operation failed: a later resume can still succeed. */
class ForgetScheduleTransientException(cause: Throwable) : Exception("Scheduling the forget failed", cause)

/** Only bad input is permanent (an id that cannot be queued). Anything else may succeed later, so the row stays pending. */
fun scheduleFailureIsPermanent(error: Throwable) = error is IllegalArgumentException

/**
 * Schedules each row on its own. A permanent failure (see [scheduleFailureIsPermanent]) goes to [onFailed] and the others
 * still run; a transient one leaves the row pending for the next resume. Returns the number of permanent failures.
 * Cancellation propagates.
 */
suspend fun scheduleEach(
    rows: List<PendingForget>, trigger: ForgetTrigger, schedule: suspend (String, String, ForgetEnqueue) -> Unit, onFailed: suspend (PendingForget) -> Unit,
): Int {
    var failed = 0
    for (row in rows) {
        try { schedule(row.scope, row.documentId, forgetEnqueuePolicy(trigger)) }
        catch (cancelled: CancellationException) { throw cancelled }
        catch (error: Exception) { if (scheduleFailureIsPermanent(error)) { failed++; onFailed(row) } }
    }
    return failed
}

/** Runs every step after the commit. A failure is counted, never raised: the commit is the success point and resume recovers the rest. */
suspend fun runBestEffort(vararg steps: suspend () -> Unit): Int {
    var failed = 0
    for (step in steps) {
        try { step() } catch (cancelled: CancellationException) { throw cancelled } catch (error: Exception) { failed++ }
    }
    return failed
}

/** The confirmation cleanup repeats only what is certain: rows linked to the document. The title fallback runs once, at forget time. */
fun linkedRowsToPurge(completedNoteRows: List<PendingIngest>, documentId: String) = completedNoteRows.filter { it.documentId.equals(documentId, ignoreCase = true) }

/** A malformed cached payload cannot be matched to a document; null instead of an exception that would roll the forget back. */
fun documentIdOfPayload(payload: String): String? =
    try { apiGson.fromJson(payload, Insight::class.java)?.documentId?.lowercase() } catch (error: JsonParseException) { null }

fun Insight.isHiddenBy(hidden: Set<String>) = documentId.isNotBlank() && hidden.hides(documentId)

/** A completed import row names the document it created; a row of a forgotten document is never shown or kept. */
fun PendingIngest.isHiddenBy(hidden: Set<String>) = !documentId.isNullOrBlank() && hidden.hides(documentId)

/**
 * Saves a worker's update of an import row unless its document is hidden, in ONE transaction: the hidden set is read and the
 * row written (or deleted, returning 0 as for a row the user removed) under the same lock, so a forget that commits in between
 * cannot be missed. A delayed import therefore never puts a forgotten note's title back in Recent imports.
 * [afterHiddenRead] runs inside the transaction between the read and the write; only the tests pass one, to hold the window open.
 */
suspend fun LearningDatabase.updateJobUnlessHidden(row: PendingIngest, afterHiddenRead: suspend () -> Unit = {}): Int = withTransaction {
    val dao = dao()
    val hidden = dao.hiddenIds(row.scope).toSet()
    afterHiddenRead()
    when (jobWriteDecision(row, hidden)) {
        JobWrite.DELETE -> { dao.deleteJob(row.scope, row.id); 0 }
        JobWrite.UPDATE -> dao.updateJob(row)
    }
}

enum class JobWrite { UPDATE, DELETE }

/** A row whose document is hidden is deleted, anything else is updated. Decided on a hidden set read in the same transaction as the write. */
fun jobWriteDecision(row: PendingIngest, hidden: Set<String>) = if (row.isHiddenBy(hidden)) JobWrite.DELETE else JobWrite.UPDATE

const val REVIEW_FETCH_CAP = 20

/** The document of a review; Gson leaves it null when an older server does not send it. */
fun Review.docId(): String { val id: String? = documentId; return id.orEmpty() }

/** Reviews that may be shown, and the document id learned for each (insight id to document id). */
data class ReviewView(val reviews: List<Review>, val documents: Map<String, String>)

/**
 * A review names its document itself (older servers send nothing: blank). A blank one fails closed, always (also when
 * nothing is hidden yet, so a forget that starts later can still scrub it): its document is looked up through [cached]
 * (free) or [fetch] (network, at most [cap] reviews, none after the first non-404 failure) and a review that cannot be
 * resolved is dropped. A blank id from any source (payload, cache, network) is unresolved, exactly like a failed lookup:
 * a blank cached value falls through to the network, and a blank mapping is never recorded. A 404 drops only that review. Reviews that name their document need no lookup.
 */
suspend fun resolveReviews(
    reviews: List<Review>, hidden: Set<String>, cached: suspend (Review) -> String?, fetch: suspend (Review) -> String, cap: Int = REVIEW_FETCH_CAP,
): ReviewView {
    var fetched = 0; var networkDown = false
    val documents = LinkedHashMap<String, String>()
    val shown = reviews.filter { review ->
        val documentId = review.docId().ifBlank { null } ?: cached(review)?.ifBlank { null } ?: if (fetched < cap && !networkDown) {
            fetched++
            try { fetch(review).ifBlank { null } }
            catch (cancelled: CancellationException) { throw cancelled }
            catch (error: HttpException) { if (error.code() != 404) networkDown = true; null }
            catch (error: Exception) { networkDown = true; null }
        } else null
        if (documentId != null) documents[review.insightId] = documentId
        documentId != null && !hidden.hides(documentId)
    }
    return ReviewView(shown, documents.filterKeys { key -> shown.any { it.insightId == key } })
}

const val FORGET_ELSEWHERE_NOTICE =
    "Notes you forgot while connected to another server or token are visible again on this device and are not yet deleted on the server."

/** Shown once per app start. Rows of another scope that still wait or failed; never names a server. */
fun forgetElsewhereNotice(rows: List<PendingForget>, activeScope: String): String? =
    if (rows.any { it.scope != activeScope && (it.status == FORGET_PENDING || it.status == FORGET_FAILED) }) FORGET_ELSEWHERE_NOTICE else null

const val FORGET_STOP_MESSAGE =
    "The note stays on the server and will reappear here on the next refresh. This only stops this device from trying to delete it."

fun canStopTrying(row: PendingForget) = row.status == FORGET_FAILED
