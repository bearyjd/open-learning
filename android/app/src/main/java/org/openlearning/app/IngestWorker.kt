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
            val paused = row.copy(status = "paused", error = "Reconnect to the original account, then retry this import.")
            if (row.kind == "note") dao.updateJob(paused) else dao.putJob(paused)
            return Result.failure()
        }
        val repository = container.repository(config)
        if (row.kind == "note") return runNote(container, repository, row)
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
    // Note text lives only in Room (never WorkManager input). Writes use @Update: 0 rows means the
    // user removed the job meanwhile, and a REPLACE insert would resurrect it.
    private suspend fun runNote(container: AppContainer, repository: LearningRepository, start: PendingIngest): Result {
        val dao = container.database.dao()
        var row = start
        suspend fun save(next: PendingIngest): Boolean { row = next; return dao.updateJob(next) > 0 }
        if (row.text == null && row.jobId == null) {
            save(row.copy(status = "failed", error = NOTE_EXPIRED))
            return Result.failure()
        }
        try {
            val job = try {
                submitOrPoll(repository, row)
            } catch (conflict: HttpException) {
                // The server no longer accepts this job: resubmit from the local text, which it dedups by hash.
                if (conflict.code() != 409 || row.jobId == null || row.text == null) throw conflict
                submitOrPoll(repository, row.copy(jobId = null))
            }
            val state = job.status.lowercase()
            val finished = state in COMPLETED || state == "cancelled"
            val next = row.copy(jobId = job.id, status = job.status, error = job.error)
            if (!save(if (finished) next.copy(text = null) else next)) return Result.success()
            if (finished) container.database.checkpoint()
            return when {
                state in COMPLETED -> { repository.refreshFeed(); Result.success() }
                state in listOf("failed", "error", "cancelled") -> Result.failure()
                else -> Result.retry()
            }
        } catch (cancelled: kotlinx.coroutines.CancellationException) { throw cancelled }
        catch (error: Exception) {
            val code = (error as? HttpException)?.code()
            val permanent = code != null && code in 400..499 && code != 429
            save(row.copy(status = if (permanent) "failed" else "waiting", error = error.noteMessage()))
            return if (permanent) Result.failure() else Result.retry()
        }
    }
    private suspend fun submitOrPoll(repository: LearningRepository, row: PendingIngest): IngestJob =
        if (row.jobId == null) repository.api.ingestText(TextRequest(row.title.orEmpty(), row.text.orEmpty(), row.label)) else repository.api.job(row.jobId)
    companion object {
        /** Identifiers only: note text must never enter WorkManager's unencrypted, size-limited store. */
        fun input(scope: String, id: String): Data = workDataOf("scope" to scope, "id" to id)
        fun cancel(context: Context, scope: String, id: String) { WorkManager.getInstance(context).cancelUniqueWork("ingest-$scope-$id") }
        private val COMPLETED = listOf("completed", "complete", "succeeded", "success", "done")
        fun schedule(context: Context, scope: String, id: String, replace: Boolean = false) {
            val request = OneTimeWorkRequestBuilder<IngestWorker>()
                .setInputData(input(scope, id))
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 10, TimeUnit.SECONDS)
                .addTag("ingest").build()
            WorkManager.getInstance(context).enqueueUniqueWork("ingest-$scope-$id", if (replace) ExistingWorkPolicy.REPLACE else ExistingWorkPolicy.KEEP, request)
        }
    }
}
