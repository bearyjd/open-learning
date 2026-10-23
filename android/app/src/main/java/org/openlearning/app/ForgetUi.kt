package org.openlearning.app

/** Banner and list copy for the offline forget queue. Fixed strings only: no titles, no server text. */
const val FORGET_QUEUED_MESSAGE =
    "Note hidden on this device. It will be deleted from the server when a connection is available. There is no undo."
const val FORGET_LOCAL_FAILED_MESSAGE =
    "This device could not save the change, so nothing was hidden or deleted. Try again."

private fun notes(count: Int) = if (count == 1) "1 forgotten note" else "$count forgotten notes"

/** The notes list line item; null (hidden) when nothing waits. */
fun forgetWaitingLine(count: Int): String? = if (count <= 0) null else "${notes(count)} waiting for the server"

/** A failed row's text. Only the fixed errors are ever shown, whatever is stored. */
fun forgetFailedLine(row: PendingForget): String = row.error?.takeIf { it in FORGET_FIXED_ERRORS } ?: FORGET_REFUSED_ERROR
