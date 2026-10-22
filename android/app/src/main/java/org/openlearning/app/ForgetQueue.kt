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
    private val forgotten: ForgottenNotes,
    private val schedule: (scope: String, documentId: String) -> Unit,
    private val cancelIngest: (scope: String, id: String) -> Unit,
    private val clock: () -> Long = System::currentTimeMillis,
) {
    private val dao = database.dao()

    /**
     * One transaction: queue the forget (idempotent), purge cached cards of the document, the completed import rows
     * linked to it ([noteRowsToPurge]) and the queued, running or failed import rows linked to it. Then the worker is
     * scheduled. Returns the purged card ids. An unsent draft or an unlinked queued note has no document id, so it
     * cannot be matched and is left alone.
     */
    suspend fun forgetNoteOffline(scope: String, documentId: String, title: String? = null): List<String> {
        if (documentId.isBlank()) return emptyList()
        forgotten.add(scope, documentId)
        val active = mutableListOf<PendingIngest>()
        val purged = database.withTransaction {
            dao.putForget(PendingForget(scope, documentId, clock(), FORGET_PENDING))
            active += activeRowsToCancel(dao.linkedNoteRows(scope, documentId), documentId)
            active.forEach { dao.deleteJob(it.scope, it.id) }
            purgeLocal(scope, documentId, title)
        }
        active.forEach { cancelIngest(it.scope, it.id) }
        database.checkpointBestEffort()
        schedule(scope, documentId)
        return purged
    }

    /** Local cleanup, safe to repeat: cards of the document and its completed import rows. Never touches PendingForget. */
    suspend fun purgeLocal(scope: String, documentId: String, title: String?): List<String> {
        val ids = dao.allInsights(scope).filter { apiGson.fromJson(it.payload, Insight::class.java).documentId == documentId }.map { it.id }
        if (ids.isNotEmpty()) dao.deleteInsights(scope, ids)
        noteRowsToPurge(dao.completedNoteRows(scope, COMPLETED_STATES), documentId, title).forEach { dao.deleteJob(it.scope, it.id) }
        return ids
    }

    /** The server confirmed (or had nothing to delete): drop the queue row and repeat the local purge. */
    suspend fun confirmed(scope: String, documentId: String) {
        database.withTransaction { dao.deleteForget(scope, documentId); purgeLocal(scope, documentId, null) }
        database.checkpointBestEffort()
    }

    suspend fun markFailed(scope: String, documentId: String, error: String) { dao.setForgetStatus(scope, documentId, FORGET_FAILED, error) }

    /** Failed rows only: puts the row back to pending and schedules the worker again. */
    suspend fun retryForget(scope: String, documentId: String) {
        val row = dao.forget(scope, documentId) ?: return
        if (row.status != FORGET_FAILED) return
        dao.setForgetStatus(scope, documentId, FORGET_PENDING, null)
        schedule(scope, documentId)
    }

    /** On connect: every pending row of the account gets its worker back (KEEP makes a running one a no-op). */
    suspend fun resumePending(scope: String) { dao.pendingForgets(scope).forEach { schedule(it.scope, it.documentId) } }

    fun pendingForgetCount(scope: String): Flow<Int> = dao.pendingForgetCount(scope)
    fun failedForgets(scope: String): Flow<List<PendingForget>> = dao.failedForgets(scope)
    fun hiddenIds(scope: String): Flow<Set<String>> = dao.observeForgets(scope).map { hiddenIds(it, scope) }
    suspend fun hidden(scope: String): Set<String> = dao.hiddenIds(scope).toSet()

    suspend fun visibleNotes(scope: String, notes: List<NoteSummary>) = notes.hideForgotten(hidden(scope)) { it.documentId }
    suspend fun visibleInsights(scope: String, items: List<Insight>) = items.hideForgotten(hidden(scope)) { it.documentId }

    /**
     * Reviews carry only an insight id. Resolve its document from the cache, else from the server (the card was purged
     * locally); a review that cannot be resolved while a forget is queued is hidden rather than risk showing it.
     */
    suspend fun visibleReviews(repository: LearningRepository, reviews: List<Review>): List<Review> {
        val scope = repository.config.scope
        val hidden = hidden(scope)
        if (hidden.isEmpty()) return reviews
        return reviews.filter { review ->
            val documentId = try {
                dao.get(scope, review.insightId)?.let(repository::decode)?.documentId ?: repository.api.insight(review.insightId).documentId
            } catch (cancelled: CancellationException) { throw cancelled } catch (error: Exception) { return@filter false }
            documentId.isBlank() || documentId !in hidden
        }
    }
}
