package org.openlearning.app

/** What the notes list screen shows for a [NotesListState]. */
enum class NotesBody { Loading, Error, Empty, List }

fun notesBody(state: NotesListState): NotesBody = when {
    state.loading && !state.loaded -> NotesBody.Loading
    state.error != null && state.items.isEmpty() -> NotesBody.Error
    state.isEmpty -> NotesBody.Empty
    else -> NotesBody.List
}

/** "Label · 1,234 characters · 2026-10-22"; the label is left out when there is none. */
fun NoteSummary.rowMeta(): String {
    val chars = "${formatCount(charCount)} ${if (charCount == 1) "character" else "characters"}"
    return listOfNotNull(label, chars, createdDate.ifBlank { "date unknown" }).joinToString(" · ")
}

/**
 * Forget dialog copy. Rows made before the note link existed (schema v3) cannot be matched to one note, so the
 * dialog says so instead of claiming every completed import is cleared.
 */
const val FORGET_NOTE_MESSAGE =
    "This deletes the note and the ideas made from it from your server and this device. It also removes this note's " +
        "entry from Recent imports. Entries from before this update can't be matched to a note: if none is linked to " +
        "this one, completed note entries without a link are removed too. It needs a connection and can't be undone."
