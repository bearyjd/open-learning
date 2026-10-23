package org.openlearning.app

import android.content.Context
import androidx.work.*
import java.util.concurrent.TimeUnit

/**
 * Every six hours while connected: resumes pending forgets and, when a confirmed row is past its TTL, settles the
 * confirmed rows against the server list. Never fails: the next period tries again.
 */
class ForgetSweepWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val container = (applicationContext as LearningApplication).container
        val config = container.credentials.connection.value ?: return Result.success()
        val queue = container.forgetQueue
        try {
            queue.resumePending(config.scope)
            if (queue.confirmedNeedReconcile(config.scope)) {
                val startedAt = System.currentTimeMillis()
                val api = container.repository(config).api
                val scan = scanNotes { limit, offset -> api.notes(limit, offset) }
                queue.reconcile(config.scope, scan.ids, scan.complete, startedAt)
            }
        } catch (cancelled: kotlinx.coroutines.CancellationException) { throw cancelled }
        catch (error: Exception) { android.util.Log.w("OpenLearning", "Forget sweep did not finish") }
        return Result.success()
    }
    companion object {
        const val WORK_NAME = "forget-sweep"
        const val PERIOD_HOURS = 6L
        /** KEEP: scheduling again on every connect never resets the period. */
        fun schedule(context: Context) {
            val request = PeriodicWorkRequestBuilder<ForgetSweepWorker>(PERIOD_HOURS, TimeUnit.HOURS)
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()).addTag("forget").build()
            WorkManager.getInstance(context).enqueueUniquePeriodicWork(WORK_NAME, ExistingPeriodicWorkPolicy.KEEP, request)
        }
    }
}
