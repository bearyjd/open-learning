package org.openlearning.app

const val NOTES_PAGE_LIMIT = 100

/** One row of GET /notes. Gson leaves absent fields at their defaults and can null non-null ones, so read through [toSummary]. */
data class NoteListItem(
    val documentId: String? = null, val title: String? = null, val label: String? = null,
    val createdAt: String? = null, val charCount: Int? = null,
)

/** GET /limits. Every field is optional: a missing one falls back to the default in [effectiveLimits]. */
data class Limits(
    val maxDocumentChars: Int? = null, val minTextChars: Int? = null,
    val maxTitleChars: Int? = null, val maxLabelChars: Int? = null,
)

/** What the notes list shows. Held in memory only; never written to Room. */
data class NoteSummary(val documentId: String, val title: String, val label: String?, val createdAt: String, val charCount: Int) {
    /** YYYY-MM-DD from the ISO timestamp, for display. */
    val createdDate get() = createdAt.take(10)
}

/** Null when the server sent no document id: such a row could not be forgotten. */
fun NoteListItem.toSummary(): NoteSummary? {
    val id = documentId?.takeIf { it.isNotBlank() } ?: return null
    return NoteSummary(id, title.orEmpty().ifBlank { "Untitled" }, label?.takeIf { it.isNotBlank() }, createdAt.orEmpty(), (charCount ?: 0).coerceAtLeast(0))
}
fun List<NoteListItem>.toSummaries(): List<NoteSummary> = mapNotNull { it.toSummary() }
