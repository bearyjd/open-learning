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
    val chatCitesHidden = chat.any { line -> line.citations.any { goneId(it.documentId) } }
    val touched = feed.any(::gone) || library.any(::gone) || results.any(::gone) || selectedGone || sourceGone ||
        jobs.any { goneId(it.documentId) } || reviews.any(dropReview) || reviewDocs.values.any(::goneId) ||
        chatCitesHidden || notes.items.any { goneId(it.documentId) }
    if (!touched) return this
    return copy(
        feed = feed.filterNot(::gone), library = library.filterNot(::gone), results = results.filterNot(::gone),
        selected = if (selectedGone) null else selected,
        chat = if (selectedGone || chatCitesHidden) emptyList() else chat,
        chatBusy = if (selectedGone || chatCitesHidden) false else chatBusy,
        learning = if (selectedGone) false else learning,
        jobs = jobs.filterNot { goneId(it.documentId) },
        reviews = reviews.filterNot(dropReview),
        reviewDocs = reviewDocs.filterValues { !goneId(it) },
        reviewSource = if (sourceGone) null else reviewSource,
        revealed = if (sourceGone) false else revealed,
        notes = if (notes.items.any { goneId(it.documentId) }) notes.copy(items = notes.items.filterNot { goneId(it.documentId) }) else notes,
    )
}

/** Whether [review] is the review the screen currently shows first: the head of the visible list, not of the raw one. */
fun ScreenState.canReveal(review: Review, hidden: Set<String>): Boolean = withoutHidden(hidden).reviews.firstOrNull()?.id == review.id

/**
 * The permanent form of [withoutHidden], applied to the retained state itself whenever the hidden set changes. Masking alone
 * would let the raw content come back once a confirmed tombstone is pruned and the hidden set shrinks.
 */
fun ScreenState.purgedOf(hidden: Set<String>): ScreenState = withoutHidden(hidden)

/** What the screen shows: a hidden set that belongs to another account (a switch in flight) is never applied, so nothing is shown until its own set arrives. */
fun ScreenState.viewFor(hiddenScope: String, hidden: Set<String>): ScreenState =
    if (scope == hiddenScope) withoutHidden(hidden) else ScreenState(scope = scope)

/** The retained state is purged only with the hidden set of its own account. */
fun ScreenState.purgedFor(hiddenScope: String, hidden: Set<String>): ScreenState =
    if (scope == hiddenScope) purgedOf(hidden) else this

/**
 * The only way a publication reaches the retained state: [transform] is applied, then the result is purged with every hidden
 * set supplied (each only if it belongs to the state's own account). A response for a document forgotten meanwhile is
 * therefore never stored, so it cannot come back when a confirmed tombstone is pruned.
 */
fun ScreenState.published(sets: List<Pair<String, Set<String>>>, transform: (ScreenState) -> ScreenState): ScreenState =
    sets.fold(transform(this)) { state, (scope, ids) -> state.purgedFor(scope, ids) }
