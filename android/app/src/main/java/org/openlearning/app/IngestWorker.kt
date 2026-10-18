package org.openlearning.app

import android.content.Context
import androidx.work.*
import retrofit2.HttpException
import java.util.UUID
import java.util.concurrent.TimeUnit

class IngestWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val container = (applicationContext as LearningApplication).container
        val scope = inputData.getString("scope") ?: return Result.failure()
        val id = inputData.getString("id") ?: return Result.failure()
        val dao = container.database.dao()
        var row = dao.job(scope, id) ?: return Result.failure()
        val config = container.credentials.connection.value
        if (config == null || config.scope != scope) {
            dao.putJob(row.copy(status = "paused", error = "Reconnect to the original account, then retry this import."))
            return Result.failure()
        }
        val repository = container.repository(config)
        try {
            val job = if (row.jobId == null) repository.api.ingest(UrlRequest(row.url)) else repository.api.job(row.jobId)
            row = row.copy(jobId = job.id, status = job.status, error = job.error)
            dao.putJob(row)
            return when (job.status.lowercase()) {
                "completed", "complete", "succeeded", "success", "done" -> {
                    repository.refreshFeed()
                    Result.success()
                }
                "failed", "error", "cancelled" -> Result.failure()
                else -> Result.retry()
            }
        } catch (cancelled: kotlinx.coroutines.CancellationException) { throw cancelled }
        catch (error: Exception) {
            val permanent = error is HttpException && error.code() in 400..499 && error.code() != 429
            dao.putJob(row.copy(status = if (permanent) "failed" else "waiting", error = error.userMessage()))
            return if (permanent) Result.failure() else Result.retry()
        }
    }
    companion object {
        fun schedule(context: Context, scope: String, id: String, replace: Boolean = false) {
            val request = OneTimeWorkRequestBuilder<IngestWorker>()
                .setInputData(workDataOf("scope" to scope, "id" to id))
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 10, TimeUnit.SECONDS)
                .addTag("ingest").build()
            WorkManager.getInstance(context).enqueueUniqueWork("ingest-$scope-$id", if (replace) ExistingWorkPolicy.REPLACE else ExistingWorkPolicy.KEEP, request)
        }
    }
}
