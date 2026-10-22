package org.openlearning.app

import android.content.Context
import androidx.work.*
import java.util.concurrent.TimeUnit

/** Replays one queued forget: DELETE /api/v1/notes/{id}. Only identifiers enter WorkManager input. */
class ForgetWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val container = (applicationContext as LearningApplication).container
        val scope = inputData.getString("scope") ?: return Result.failure()
        val documentId = inputData.getString("documentId") ?: return Result.failure()
        val queue = container.forgetQueue
        if (container.database.dao().forget(scope, documentId)?.status != FORGET_PENDING) return Result.success()
        val config = container.credentials.connection.value
        // Another account is active (or none): keep the row pending, it is scheduled again when its account connects.
        if (forgetStart(config?.scope, scope) == ForgetStart.LEAVE_PENDING || config == null) return Result.success()
        val code = try {
            container.repository(config).api.forgetNote(documentId).code()
        } catch (cancelled: kotlinx.coroutines.CancellationException) { throw cancelled }
        catch (error: Exception) { null } // IOException or anything unexpected: no answer, so retry
        return when (val replay = forgetReplay(code)) {
            // The DELETE is idempotent (404 is Done), so a failed local cleanup is repeated by retrying the whole job.
            ForgetReplay.Done -> try { queue.confirmed(scope, documentId); Result.success() }
                catch (cancelled: kotlinx.coroutines.CancellationException) { throw cancelled }
                catch (error: Exception) { android.util.Log.w("OpenLearning", "Cleaning up a forgotten note failed, retrying"); Result.retry() }
            is ForgetReplay.Failed -> { queue.markFailed(scope, documentId, replay.error); Result.failure() }
            ForgetReplay.Retry -> Result.retry()
        }
    }
    companion object {
        fun input(scope: String, documentId: String): Data = workDataOf("scope" to scope, "documentId" to documentId)
        fun schedule(context: Context, scope: String, documentId: String) {
            val request = OneTimeWorkRequestBuilder<ForgetWorker>()
                .setInputData(input(scope, documentId))
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, FORGET_BACKOFF_SECONDS, TimeUnit.SECONDS)
                .addTag("forget").build()
            WorkManager.getInstance(context).enqueueUniqueWork(forgetWorkName(scope, documentId), ExistingWorkPolicy.KEEP, request)
        }
    }
}
