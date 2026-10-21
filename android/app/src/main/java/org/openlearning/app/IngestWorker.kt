package org.openlearning.app

import android.content.Context
import android.util.Log
import androidx.work.*
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.withContext
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
        if (start.text == null && start.jobId == null) {
            dao.updateJob(start.copy(status = "failed", error = NOTE_EXPIRED))
            return Result.failure()
        }
        var attempt = start
        try {
            val job = try {
                submitOrPoll(repository, attempt)
            } catch (gone: HttpException) {
                // The server forgot the job: resubmit once from the local text, which it dedups by hash.
                if (!shouldResubmit(attempt, gone.code())) throw gone
                attempt = attempt.copy(jobId = null)
                submitOrPoll(repository, attempt)
            }
            val update = noteAfterResponse(attempt, job)
            val changed = dao.updateJob(update.row)
            val followUp = afterSave(changed, createdServerJob = attempt.jobId == null)
            if (followUp != SaveFollowUp.CONTINUE) {
                // Non-cancellable so a concurrent cancel cannot skip removing the job this run just created.
                if (followUp == SaveFollowUp.DROP_AND_DELETE_SERVER_JOB) withContext(NonCancellable) { discardOrphan(repository, dao, attempt, job.id) }
                return Result.success()
            }
            // Past this point the row is already saved: a failure here must not regress a completed job.
            if (update.row.text == null) container.database.checkpointBestEffort()
            if (update.refresh) refreshQuietly(repository)
            return update.outcome.toResult()
        } catch (cancelled: kotlinx.coroutines.CancellationException) { throw cancelled }
        catch (error: Exception) {
            val update = noteAfterError(attempt, (error as? HttpException)?.code(), error.noteMessage())
            dao.updateJob(update.row)
            return update.outcome.toResult()
        }
    }
    private suspend fun discardOrphan(repository: LearningRepository, dao: LearningDao, row: PendingIngest, jobId: String) {
        try {
            if (mayDeleteServerJob(jobId, dao.otherHolders(jobId, row.scope, row.id))) repository.api.deleteJob(jobId)
        } catch (error: Exception) { Log.w("OpenLearning", "Could not remove an orphaned note job") }
    }
    private suspend fun refreshQuietly(repository: LearningRepository) {
        try { repository.refreshFeed() }
        catch (cancelled: kotlinx.coroutines.CancellationException) { throw cancelled }
        catch (error: Exception) { Log.w("OpenLearning", "Feed refresh after a completed note failed") }
    }
    private suspend fun submitOrPoll(repository: LearningRepository, row: PendingIngest): IngestJob =
        if (row.jobId == null) repository.api.ingestText(TextRequest(row.title.orEmpty(), row.text.orEmpty(), row.label)) else repository.api.job(row.jobId)
    companion object {
        /** Identifiers only: note text must never enter WorkManager's unencrypted, size-limited store. */
        fun input(scope: String, id: String): Data = workDataOf("scope" to scope, "id" to id)
        private fun workName(scope: String, id: String) = "ingest-$scope-$id"
        fun cancel(context: Context, scope: String, id: String) { WorkManager.getInstance(context).cancelUniqueWork(workName(scope, id)) }
        /** Bounded wait for the unique work to finish after [cancel]. Returns false on timeout. */
        suspend fun awaitStopped(context: Context, scope: String, id: String): Boolean =
            awaitSettled(WorkManager.getInstance(context).getWorkInfosForUniqueWorkFlow(workName(scope, id)), 5_000) { infos -> infos.all { it.state.isFinished } }
        fun schedule(context: Context, scope: String, id: String, replace: Boolean = false) {
            val request = OneTimeWorkRequestBuilder<IngestWorker>()
                .setInputData(input(scope, id))
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 10, TimeUnit.SECONDS)
                .addTag("ingest").build()
            WorkManager.getInstance(context).enqueueUniqueWork(workName(scope, id), if (replace) ExistingWorkPolicy.REPLACE else ExistingWorkPolicy.KEEP, request)
        }
    }
}
