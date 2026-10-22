package org.openlearning.app

import androidx.room.withTransaction
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

/**
 * The offline forget queue: a note is hidden on this device at once and deleted on the server when a connection allows.
 * State lives in the PendingForget table (ids, timestamps, status, fixed error), so it survives process death.
 * [schedule] starts the unique worker for a row; [cancelIngest] stops a note import job of the same scope.
 */
class ForgetQueue(
    private val database: LearningDatabase,
    private val schedule: suspend (scope: String, documentId: String, enqueue: ForgetEnqueue) -> Unit,
    private val cancelIngest: (scope: String, id: String) -> Unit,
    private val clock: () -> Long = System::currentTimeMillis,
) {
    private val dao = database.dao()

    /**
     * Called right after a commit that hides ids, before the function returns: the screen drops them at once
     * ([ScreenPublisher.purgeNow]) instead of waiting for the Room flow. Set by the view model.
     */
    @Volatile var onHidden: (scope: String, ids: Set<String>) -> Unit = { _, _ -> }

    /**
     * Called after a PendingForget row was deleted (stop trying, TTL prune) so the purge floor lets go of its ids at once
     * ([ScreenPublisher.release]). Every delete of PendingForget rows here is followed by this call (a test scans for it).
     */
    @Volatile var onReleased: (scope: String, ids: Set<String>) -> Unit = { _, _ -> }

    /**
     * Validates the id first (a bad one is never queued). One transaction: queue the forget (idempotent), purge cached cards
     * of the document, the completed import rows linked to it ([noteRowsToPurge]) and the queued, running or failed import
     * rows linked to it. The commit is the success point, and from then on [LearningRepository.writeCards] keeps any late
     * response from writing the cards back. Cancelling the ingest, the checkpoint and the scheduling are best effort
     * afterwards, because [resumePending] recovers an unscheduled row. Returns the purged card ids. An unsent draft or an
     * unlinked queued note has no document id, so it cannot be matched and is left alone.
     */
    suspend fun forgetNoteOffline(scope: String, documentId: String, title: String? = null): List<String> {
        if (documentId.isBlank()) return emptyList()
        if (!validForgetDocumentId(documentId)) throw InvalidForgetIdException()
        val active = mutableListOf<PendingIngest>()
        val purged = database.withTransaction {
            dao.putForget(PendingForget(scope, documentId, clock(), FORGET_PENDING))
            active += activeRowsToCancel(dao.linkedNoteRows(scope, documentId), documentId)
            active.forEach { dao.deleteJob(it.scope, it.id) }
            purgeLocal(scope, documentId, title)
        }
        onHidden(scope, setOf(documentId))
        val cancels = active.map { suspend { cancelIngest(it.scope, it.id) } }
        runBestEffort(*cancels.toTypedArray(), { schedule(scope, documentId, forgetEnqueuePolicy(ForgetTrigger.NEW_FORGET)) })
        database.checkpointBestEffort()
        return purged
    }

    /** Local cleanup, safe to repeat: cards of the document and its completed import rows. Never touches PendingForget. */
    suspend fun purgeLocal(scope: String, documentId: String, title: String?): List<String> {
        val ids = dao.allInsights(scope).filter { documentIdOfPayload(it.payload) == documentId }.map { it.id }
        if (ids.isNotEmpty()) dao.deleteInsights(scope, ids)
        noteRowsToPurge(dao.completedNoteRows(scope, COMPLETED_STATES), documentId, title).forEach { dao.deleteJob(it.scope, it.id) }
        return ids
    }

    /**
     * The server confirmed (or had nothing to delete). One transaction: the row becomes a durable "confirmed" tombstone
     * (upserted, so a row that went missing or failed is recorded too), then the cards and the linked completed import
     * rows are purged (linked rows only: the title fallback ran once at forget time and must not widen). The tombstone keeps
     * hiding the note, and [LearningRepository.writeCards] keeps refusing its cards, until [reconcile] settles it.
     */
    suspend fun confirmed(scope: String, documentId: String) {
        database.withTransaction {
            dao.confirmForget(PendingForget(scope, documentId, clock(), FORGET_CONFIRMED))
            val ids = dao.allInsights(scope).filter { documentIdOfPayload(it.payload) == documentId }.map { it.id }
            if (ids.isNotEmpty()) dao.deleteInsights(scope, ids)
            linkedRowsToPurge(dao.completedNoteRows(scope, COMPLETED_STATES), documentId).forEach { dao.deleteJob(it.scope, it.id) }
        }
        onHidden(scope, setOf(documentId))
        database.checkpointBestEffort()
    }

    /**
     * Settles confirmed rows against a server list answer ([serverIds], [complete], and [requestStartedAt], the clock when the
     * request began). See [confirmedDecisions]. One transaction, no network inside. A revived row (the server still lists
     * the note after the TTL) goes back to pending and its worker is enqueued again; the DELETE is idempotent.
     */
    suspend fun reconcile(scope: String, serverIds: Set<String>, complete: Boolean, requestStartedAt: Long) {
        var decisionsDeleted = emptyList<String>()
        val revived = database.withTransaction {
            val decisions = confirmedDecisions(scope, dao.confirmedForgets(scope), serverIds, complete, requestStartedAt)
            if (decisions.delete.isNotEmpty()) dao.deleteForgets(scope, decisions.delete)
            decisionsDeleted = decisions.delete
            decisions.revive.map { id -> PendingForget(scope, id, clock(), FORGET_PENDING).also { dao.confirmForget(it) } }
        }
        if (decisionsDeleted.isNotEmpty()) onReleased(scope, decisionsDeleted.toSet())
        if (revived.isNotEmpty()) scheduleEach(revived, ForgetTrigger.RETRY, schedule) { markFailed(it.scope, it.documentId, FORGET_SCHEDULE_ERROR) }
    }

    /** The background sweep only fetches the list when this is true. */
    suspend fun confirmedNeedReconcile(scope: String): Boolean = sweepNeedsReconcile(dao.confirmedForgets(scope), clock())

    suspend fun markFailed(scope: String, documentId: String, error: String) { dao.setForgetStatus(scope, documentId, FORGET_FAILED, error) }

    /** Failed rows only: puts the row back to pending and schedules the worker again (appended, never dropped). */
    suspend fun retryForget(scope: String, documentId: String) {
        val row = dao.forget(scope, documentId) ?: return
        if (!canStopTrying(row)) return
        dao.setForgetStatus(scope, documentId, FORGET_PENDING, null)
        scheduleEach(listOf(row), ForgetTrigger.RETRY, schedule) { markFailed(it.scope, it.documentId, FORGET_SCHEDULE_ERROR) }
    }

    /** Failed rows only: the person gives up. The row goes and the note is shown again (it stays on the server). */
    suspend fun stopTrying(scope: String, documentId: String): Boolean {
        val row = dao.forget(scope, documentId) ?: return false
        if (!canStopTrying(row)) return false
        dao.deleteForget(scope, documentId)
        onReleased(scope, setOf(documentId))
        return true
    }

    /**
     * Every pending row of the account gets its worker back (KEEP: a running one is left alone). Runs on connect and
     * when the notes list opens. A row whose worker cannot be built is marked failed with a fixed error; the app never crashes.
     */
    suspend fun resumePending(scope: String) {
        scheduleEach(dao.pendingForgets(scope), ForgetTrigger.RESUME, schedule) { markFailed(it.scope, it.documentId, FORGET_SCHEDULE_ERROR) }
    }

    /** Rows of every other account (token rotation can strand them): the connect notice counts them. */
    suspend fun otherScopeRows(scope: String): List<PendingForget> = dao.forgetsOutside(scope)

    fun pendingForgetCount(scope: String): Flow<Int> = dao.pendingForgetCount(scope)
    fun failedForgets(scope: String): Flow<List<PendingForget>> = dao.failedForgets(scope)
    fun hiddenIds(scope: String): Flow<Set<String>> = dao.observeForgets(scope).map { hiddenIds(it, scope) }
    suspend fun hidden(scope: String): Set<String> = dao.hiddenIds(scope).toSet()

    /** Reviews carry only an insight id: see [resolveReviews]. The document ids learned are returned for later hiding. */
    suspend fun visibleReviews(repository: LearningRepository, reviews: List<Review>): ReviewView {
        val scope = repository.config.scope
        return resolveReviews(reviews, hidden(scope),
            { review -> dao.get(scope, review.insightId)?.let(repository::decode)?.documentId },
            { review -> repository.api.insight(review.insightId).documentId })
    }
}
