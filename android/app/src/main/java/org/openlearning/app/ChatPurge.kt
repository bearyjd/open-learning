package org.openlearning.app

/** Pure decisions behind the tutor chat's forget handling. */

/** True when the hidden set of the same account gained an id: any forget starting clears the whole tutor chat. */
fun hiddenGained(previous: Pair<String, Set<String>>, next: Pair<String, Set<String>>): Boolean =
    previous.first == next.first && (next.second - previous.second).isNotEmpty()

/** The whole chat goes (answers and questions may quote a forgotten note); the selection and the rest stay. */
fun ScreenState.withChatCleared(): ScreenState = copy(chat = emptyList(), chatBusy = false)

/** Appends [reply]; a reply citing a hidden document is dropped entirely, with the question line it answers. */
fun ScreenState.withReply(reply: ChatLine, hidden: Set<String>): ScreenState =
    if (reply.citations.any { it.documentId.isNotBlank() && hidden.hides(it.documentId) }) copy(chat = if (chat.lastOrNull()?.role == "You") chat.dropLast(1) else chat)
    else copy(chat = chat + reply)

/** A chat request for a selected note that is hidden is never sent. */
fun chatMayProceed(selected: Insight, hidden: Set<String>): Boolean = !selected.isHiddenBy(hidden)

/** Saved tutor turns as chat lines; unknown roles are skipped. The server returns them oldest first. */
fun List<HistoryLine>.toChatLines(): List<ChatLine> = mapNotNull {
    when (it.role) { "user" -> ChatLine("You", it.message); "assistant" -> ChatLine("Tutor", it.message); else -> null }
}

/** Fills an untouched chat with [lines] only for the same card, the same chat generation and a card that is not hidden. */
fun ScreenState.withHistory(itemId: String, lines: List<ChatLine>, hidden: Set<String>, current: Boolean): ScreenState {
    val card = selected
    return if (!current || card == null || card.id != itemId || card.isHiddenBy(hidden) || chat.isNotEmpty() || chatBusy) this else copy(chat = lines)
}
