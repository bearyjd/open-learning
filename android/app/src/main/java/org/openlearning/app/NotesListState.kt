package org.openlearning.app

/**
 * The notes list screen. Rows come from GET /notes and live in memory only: titles and labels are never cached in Room.
 * Every transition returns a new value.
 */
data class NotesListState(
    val items: List<NoteSummary> = emptyList(), val loading: Boolean = false, val loaded: Boolean = false,
    val error: String? = null, val forgetting: Set<String> = emptySet(), val message: String? = null,
) {
    val isEmpty get() = loaded && items.isEmpty()
}

fun NotesListState.refreshStarted() = copy(loading = true, error = null)

/** A note being forgotten stays out of the list even if a refresh that started earlier still returns it. */
fun NotesListState.refreshed(fetched: List<NoteSummary>) =
    copy(items = fetched.filterNot { it.documentId in forgetting }, loading = false, loaded = true, error = null)

/** Keeps the rows already shown: a failed refresh must not blank the list. */
fun NotesListState.refreshFailed(message: String) = copy(loading = false, error = message)

fun NotesListState.forgetStarted(documentId: String) = copy(forgetting = forgetting + documentId, message = null)

fun NotesListState.forgetSucceeded(documentId: String) =
    copy(items = items.filterNot { it.documentId == documentId }, forgetting = forgetting - documentId, message = "Note forgotten.")

/** Nothing was cleared: the row stays so the person can try again. */
fun NotesListState.forgetFailed(documentId: String, message: String) = copy(forgetting = forgetting - documentId, message = message)

fun NotesListState.messageShown() = copy(message = null)
