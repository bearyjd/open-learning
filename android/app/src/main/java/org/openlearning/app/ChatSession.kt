package org.openlearning.app

/**
 * The tutor chat's ownership of [ScreenState.chat] and [ScreenState.chatBusy]. A reply or its final busy reset only counts
 * while the generation it started under is still the current one. [reset] is the ONLY way to bump the generation and it
 * clears the chat and the busy flag in the same step, so no path can orphan a busy flag whose owner was just invalidated.
 */
class ChatSession(private val publisher: ScreenPublisher) {
    var generation = 0; private set

    /** Invalidates every chat in flight: generation up, chat empty, not busy. */
    fun reset() {
        generation++
        publisher.publishLocal { it.withChatCleared() }
    }

    /** Starts a chat turn under the current generation; null when nothing is selected or a turn is already running. */
    fun begin(message: String): Int? {
        if (publisher.value.selected == null || publisher.value.chatBusy) return null
        publisher.publishLocal { it.copy(chatBusy = true, error = null, chat = if (message.isBlank()) it.chat else it.chat + ChatLine("You", message)) }
        return generation
    }

    fun isCurrent(generation: Int) = generation == this.generation

    /** Clears busy only when [generation] still owns the chat (a newer owner or a reset has its own state). */
    fun end(generation: Int) { if (isCurrent(generation)) publisher.publishLocal { it.copy(chatBusy = false) } }
}
