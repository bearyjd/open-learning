package org.openlearning.app

data class ChatLine(val role: String, val text: String, val question: String = "", val citations: List<Citation> = emptyList())
data class ScreenState(
    val feed: List<Insight> = emptyList(), val library: List<Insight> = emptyList(),
    val results: List<Insight> = emptyList(), val searched: Boolean = false,
    val jobs: List<PendingIngest> = emptyList(), val reviews: List<Review> = emptyList(), val reviewsLoaded: Boolean = false,
    val scope: String = "", val selected: Insight? = null, val learning: Boolean = false, val chat: List<ChatLine> = emptyList(), val chatBusy: Boolean = false,
    val revealed: Boolean = false, val reviewSource: Insight? = null, val busy: Boolean = false, val error: String? = null, val notice: Notice? = null,
    val notes: NotesListState = NotesListState(),
)
