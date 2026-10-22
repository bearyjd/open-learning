package org.openlearning.app

/**
 * The notes list screen. Rows come from GET /notes and live in memory only: titles and labels are never cached in Room.
 * Every transition returns a new value.
 */
data class NotesListState(
    val items: List<NoteSummary> = emptyList(), val loading: Boolean = false, val loaded: Boolean = false,
    val error: String? = null, val forgetting: Set<String> = emptySet(), val message: String? = null,
    /** Counts loads started; only the newest load may change the list. */
    val generation: Int = 0,
    /** Ids the server confirmed deleted. Kept out of the list until a fresh server answer no longer contains them. */
    val gone: Set<String> = emptySet(),
) {
    val isEmpty get() = loaded && items.isEmpty()
    // Titles are private: keep them out of logs and crash reports.
    override fun toString() = "NotesListState(items=${items.size}, loading=$loading, loaded=$loaded, forgetting=${forgetting.size})"
}

fun NotesListState.refreshStarted() = copy(loading = true, error = null, generation = generation + 1)

/**
 * A note being forgotten, or already deleted, stays out of the list even if a load that started earlier returns it.
 * A load that is not the newest ([generation]) is ignored. A deleted id is dropped from [NotesListState.gone] once an
 * answer no longer contains it.
 */
fun NotesListState.refreshed(fetched: List<NoteSummary>, generation: Int = this.generation): NotesListState {
    if (generation != this.generation) return this
    val fetchedIds = fetched.mapTo(HashSet()) { it.documentId }
    return copy(items = fetched.filterNot { it.documentId in forgetting || it.documentId in gone }, loading = false, loaded = true,
        error = null, gone = gone.intersect(fetchedIds))
}

/** Keeps the rows already shown: a failed refresh must not blank the list. A stale load's failure is ignored. */
fun NotesListState.refreshFailed(message: String, generation: Int = this.generation) =
    if (generation != this.generation) this else copy(loading = false, error = message)

fun NotesListState.forgetStarted(documentId: String) = copy(forgetting = forgetting + documentId, message = null)

fun NotesListState.forgetSucceeded(documentId: String, message: String = FORGET_QUEUED_MESSAGE) =
    copy(items = items.filterNot { it.documentId == documentId }, forgetting = forgetting - documentId, gone = gone + documentId, message = message)

/** Nothing was cleared: the row stays so the person can try again. */
fun NotesListState.forgetFailed(documentId: String, message: String) = copy(forgetting = forgetting - documentId, message = message)

fun NotesListState.messageShown() = copy(message = null)

/**
 * Closing empties the list but keeps counting generations: resetting to 0 would let a load started before the close
 * share a generation with one started after reopening, and be accepted.
 */
fun NotesListState.closed() = NotesListState(generation = generation + 1)
