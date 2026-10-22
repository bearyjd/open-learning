package org.openlearning.app

/**
 * The screen's view of its own state: whatever was published, a document in [hidden] (pending, failed or confirmed forgets)
 * is dropped from every field that can hold it. Applied on every emission, so a stale publication is scrubbed whenever it
 * lands. Returns the same instance when nothing matches, so an unchanged state costs no recomposition.
 */
fun ScreenState.withoutHidden(hidden: Set<String>): ScreenState {
    if (hidden.isEmpty()) return this
    fun gone(item: Insight) = item.isHiddenBy(hidden)
    fun goneId(documentId: String?) = !documentId.isNullOrBlank() && documentId in hidden
    val selectedGone = selected?.let(::gone) == true
    val sourceGone = reviewSource?.let(::gone) == true
    val dropReview = { review: Review -> goneId(review.docId()) || goneId(reviewDocs[review.insightId]) || (sourceGone && review.insightId == reviewSource?.id) }
    val touched = feed.any(::gone) || library.any(::gone) || results.any(::gone) || selectedGone || sourceGone ||
        reviews.any(dropReview) || reviewDocs.values.any(::goneId) ||
        chat.any { line -> line.citations.any { goneId(it.documentId) } } || notes.items.any { goneId(it.documentId) }
    if (!touched) return this
    return copy(
        feed = feed.filterNot(::gone), library = library.filterNot(::gone), results = results.filterNot(::gone),
        selected = if (selectedGone) null else selected,
        chat = if (selectedGone) emptyList() else chat.map { line -> if (line.citations.any { goneId(it.documentId) }) line.copy(citations = line.citations.filterNot { goneId(it.documentId) }) else line },
        chatBusy = if (selectedGone) false else chatBusy,
        learning = if (selectedGone) false else learning,
        reviews = reviews.filterNot(dropReview),
        reviewDocs = reviewDocs.filterValues { !goneId(it) },
        reviewSource = if (sourceGone) null else reviewSource,
        revealed = if (sourceGone) false else revealed,
        notes = if (notes.items.any { goneId(it.documentId) }) notes.copy(items = notes.items.filterNot { goneId(it.documentId) }) else notes,
    )
}
