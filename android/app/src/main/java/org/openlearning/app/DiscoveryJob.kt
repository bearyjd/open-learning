// SPDX-License-Identifier: GPL-3.0-only
package org.openlearning.app

private const val DISCOVERY_JOB_FAILED = "This import could not be completed. Try again."
private val DISCOVERY_JOB_STATUSES = setOf("queued", "pending", "processing", "running", "waiting", "paused", "completed", "complete", "succeeded", "success", "done", "failed", "error", "cancelled")

internal fun PendingIngest.discoveryJobLabel(): String = "Reading queue import"
internal fun PendingIngest.discoveryJobStatus(): String = when (status.lowercase()) {
    "completed", "complete", "succeeded", "success", "done" -> "Completed"
    "failed", "error" -> "Failed"
    "cancelled" -> "Cancelled"
    "queued", "pending" -> "Queued"
    "waiting" -> "Waiting for a connection"
    "paused" -> "Paused"
    else -> "Processing"
}
internal fun PendingIngest.discoveryJobError(): String? = error?.let { DISCOVERY_JOB_FAILED }
internal fun PendingIngest.discoveryStoredError(error: String?): String? =
    if (kind == "discovery") error?.let { DISCOVERY_JOB_FAILED } else error
internal fun PendingIngest.discoveryStoredStatus(status: String): String =
    if (kind == "discovery") status.lowercase().takeIf { it in DISCOVERY_JOB_STATUSES } ?: "processing" else status
