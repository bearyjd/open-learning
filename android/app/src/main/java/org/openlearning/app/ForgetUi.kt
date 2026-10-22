package org.openlearning.app

/** Banner and list copy for the offline forget queue. Fixed strings only: no titles, no server text. */
const val FORGET_QUEUED_MESSAGE =
    "Note hidden on this device. It will be deleted from the server when a connection is available. There is no undo."
const val FORGET_LOCAL_FAILED_MESSAGE =
    "This device could not save the change, so nothing was hidden or deleted. Try again."

private fun notes(count: Int) = if (count == 1) "1 forgotten note" else "$count forgotten notes"

/** The notes list line item; null (hidden) when nothing waits. */
fun forgetWaitingLine(count: Int): String? = if (count <= 0) null else "${notes(count)} waiting for the server"

/** A failed row's text. Only the two fixed errors are ever shown, whatever is stored. */
fun forgetFailedLine(row: PendingForget): String = if (row.error == FORGET_REJECTED_ERROR) FORGET_REJECTED_ERROR else FORGET_REFUSED_ERROR

/** Waiting and failed counts for banners; null when there is nothing to report. */
fun forgetQueueSummary(pending: Int, failed: Int): String? =
    listOfNotNull(
        if (pending > 0) "${notes(pending)} waiting for the server." else null,
        if (failed > 0) "${notes(failed)} could not be deleted. Retry from Your notes." else null,
    ).joinToString(" ").ifEmpty { null }

/** The banner for a finished forget: its outcome, then the queue state when there is one. */
fun forgetBanner(result: ForgetResult, pending: Int, failed: Int): String =
    listOfNotNull(result.message(), if (result is ForgetResult.Done) forgetQueueSummary(pending, failed) else null).joinToString(" ")
