package org.openlearning.app

/** Pure decisions behind the tutor chat's forget handling. */

/** True when the hidden set of the same account gained an id: any forget starting clears the whole tutor chat. */
fun hiddenGained(previous: Pair<String, Set<String>>, next: Pair<String, Set<String>>): Boolean =
    previous.first == next.first && (next.second - previous.second).isNotEmpty()

/** The whole chat goes (answers and questions may quote a forgotten note); the selection and the rest stay. */
fun ScreenState.withChatCleared(): ScreenState = copy(chat = emptyList(), chatBusy = false)

/** Appends [reply]; a reply citing a hidden document is dropped entirely, with the question line it answers. */
fun ScreenState.withReply(reply: ChatLine, hidden: Set<String>): ScreenState =
    if (reply.citations.any { it.documentId.isNotBlank() && it.documentId in hidden }) copy(chat = if (chat.lastOrNull()?.role == "You") chat.dropLast(1) else chat)
    else copy(chat = chat + reply)

/** A chat request for a selected note that is hidden is never sent. */
fun chatMayProceed(selected: Insight, hidden: Set<String>): Boolean = !selected.isHiddenBy(hidden)
