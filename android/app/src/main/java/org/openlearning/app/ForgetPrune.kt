package org.openlearning.app

/** Pure decisions for settling confirmed forget rows against a server list. Nothing here touches Room. */

/** [delete] ids may go (the server list no longer holds them); [revive] ids go back to pending and are enqueued again. */
data class ConfirmedDecisions(val delete: List<String>, val revive: List<String>)

/**
 * Only confirmed rows of [scope] older than [ttl] at [startedAt] (the start of the list request, so a row confirmed while
 * it was in flight is never judged by it) are touched. Past the TTL: not listed on a complete page, delete; still listed
 * (the server did not delete it, whatever the page size), revive; not listed on an incomplete page, keep.
 */
fun confirmedDecisions(
    scope: String, rows: List<PendingForget>, serverIds: Set<String>, complete: Boolean, startedAt: Long, ttl: Long = FORGET_CONFIRMED_TTL_MS,
): ConfirmedDecisions {
    val old = rows.filter { it.scope == scope && it.status == FORGET_CONFIRMED && startedAt - it.createdAt >= ttl }
    val revive = old.filter { it.documentId in serverIds }.map { it.documentId }
    val delete = if (complete) old.filter { it.documentId !in serverIds }.map { it.documentId } else emptyList()
    return ConfirmedDecisions(delete, revive)
}

/** What a GET /notes answer tells the queue. [complete] is false when the page is full, so the list may continue. */
data class ServerPage(val ids: Set<String>, val complete: Boolean)

fun serverPage(raw: List<NoteListItem>): ServerPage =
    ServerPage(raw.mapNotNullTo(HashSet()) { it.documentId?.takeIf(String::isNotBlank) }, raw.size < NOTES_PAGE_LIMIT)

/** The background sweep fetches the list only when a confirmed row is past its TTL. */
fun sweepNeedsReconcile(rows: List<PendingForget>, now: Long, ttl: Long = FORGET_CONFIRMED_TTL_MS): Boolean =
    rows.any { it.status == FORGET_CONFIRMED && now - it.createdAt >= ttl }
