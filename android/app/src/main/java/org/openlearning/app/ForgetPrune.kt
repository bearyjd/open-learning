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
    val listed = serverIds.mapTo(HashSet()) { it.lowercase() }
    val revive = old.filter { it.documentId.lowercase() in listed }.map { it.documentId }
    val delete = if (complete) old.filter { it.documentId.lowercase() !in listed }.map { it.documentId } else emptyList()
    return ConfirmedDecisions(delete, revive)
}

/** What a GET /notes answer tells the queue. [complete] is false when the page is full, so the list may continue. */
data class ServerPage(val ids: Set<String>, val complete: Boolean)

fun serverPage(raw: List<NoteListItem>): ServerPage =
    ServerPage(raw.mapNotNullTo(HashSet()) { it.documentId?.takeIf(String::isNotBlank) }, raw.size < NOTES_PAGE_LIMIT)

/** The background sweep fetches the list only when a confirmed row is past its TTL. */
fun sweepNeedsReconcile(rows: List<PendingForget>, now: Long, ttl: Long = FORGET_CONFIRMED_TTL_MS): Boolean =
    rows.any { it.status == FORGET_CONFIRMED && now - it.createdAt >= ttl }

/** The server accepts up to 500 rows per page. */
const val NOTES_SCAN_PAGE_LIMIT = 500
/** A scan reads at most this many pages (5000 notes); a longer list is never treated as complete. */
const val NOTES_SCAN_MAX_PAGES = 10

/** [ids] are lower case. [complete] is true only when a short final page was read, so nothing may be missing from [ids]. */
data class NotesScan(val ids: Set<String>, val complete: Boolean)

/** Pages through GET /notes. Fails closed: [NotesScan.complete] stays false when the page budget runs out. */
suspend fun scanNotes(
    limit: Int = NOTES_SCAN_PAGE_LIMIT, maxPages: Int = NOTES_SCAN_MAX_PAGES, fetch: suspend (limit: Int, offset: Int) -> List<NoteListItem>,
): NotesScan {
    val ids = HashSet<String>()
    for (page in 0 until maxPages) {
        val items = fetch(limit, page * limit)
        items.forEach { item -> item.documentId?.takeIf(String::isNotBlank)?.let { ids += it.lowercase() } }
        if (items.size < limit) return NotesScan(ids, true)
    }
    return NotesScan(ids, false)
}
