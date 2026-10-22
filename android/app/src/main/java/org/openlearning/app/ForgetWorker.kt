package org.openlearning.app

import android.content.Context
import androidx.work.*
import java.util.concurrent.TimeUnit

/** Replays one queued forget: DELETE /api/v1/notes/{id}. Only identifiers enter WorkManager input. */
class ForgetWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    /**
     * Never returns [Result.failure] for a row outcome: a failed result would poison an APPEND chain and cancel work queued
     * behind it. A rejected row is marked failed and the work succeeds; the row, not WorkManager, carries the outcome.
     */
    override suspend fun doWork(): Result {
        val container = (applicationContext as LearningApplication).container
        val scope = inputData.getString("scope") ?: return Result.success()
        val documentId = inputData.getString("documentId") ?: return Result.success()
        val queue = container.forgetQueue
        // REPLACE can start this work again for a row that was settled meanwhile: only a pending row has anything to do.
        if (container.database.dao().forget(scope, documentId)?.status != FORGET_PENDING) return Result.success()
        // No connection: keep the row pending, it is scheduled again when an account connects.
        val config = container.credentials.connection.value ?: return Result.success()
        // Another account is active: keep the row pending, it is scheduled again when its account connects.
        if (forgetStart(config.scope, scope) == ForgetStart.LEAVE_PENDING) return Result.success()
        val code = try {
            container.repository(config).api.forgetNote(documentId).code()
        } catch (cancelled: kotlinx.coroutines.CancellationException) { throw cancelled }
        catch (error: Exception) { null } // IOException or anything unexpected: no answer, so retry
        return when (val replay = forgetReplay(code, runAttemptCount)) {
            // The DELETE is idempotent (404 is Done), so a failed local cleanup is repeated by retrying the whole job.
            ForgetReplay.Done -> try { queue.confirmed(scope, documentId); Result.success() }
                catch (cancelled: kotlinx.coroutines.CancellationException) { throw cancelled }
                catch (error: Exception) { android.util.Log.w("OpenLearning", "Cleaning up a forgotten note failed, retrying"); Result.retry() }
            is ForgetReplay.Failed -> { queue.markFailed(scope, documentId, replay.error); Result.success() }
            ForgetReplay.Retry -> Result.retry()
        }
    }
    companion object {
        fun input(scope: String, documentId: String): Data = workDataOf("scope" to scope, "documentId" to documentId)
        /**
         * Enqueues the unique work and waits for WorkManager to accept it. Throws when the request cannot be built (an
         * oversized id: [IllegalArgumentException], permanent, the queue marks that row failed) or when the enqueue
         * operation fails ([ForgetScheduleTransientException], the row stays pending for the next resume).
         */
        suspend fun schedule(context: Context, scope: String, documentId: String, enqueue: ForgetEnqueue) {
            require(validForgetDocumentId(documentId)) { "Invalid document id" }
            val request = OneTimeWorkRequestBuilder<ForgetWorker>()
                .setInputData(input(scope, documentId))
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, FORGET_BACKOFF_SECONDS, TimeUnit.SECONDS)
                .addTag("forget").build()
            val operation = WorkManager.getInstance(context).enqueueUniqueWork(forgetWorkName(scope, documentId), when (enqueue) {
                ForgetEnqueue.KEEP -> ExistingWorkPolicy.KEEP
                ForgetEnqueue.REPLACE -> ExistingWorkPolicy.REPLACE
            }, request)
            try { operation.await() }
            catch (cancelled: kotlinx.coroutines.CancellationException) { throw cancelled }
            catch (error: Exception) { throw ForgetScheduleTransientException(error) }
        }
    }
}
