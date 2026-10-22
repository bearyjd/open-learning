package org.openlearning.app

import kotlinx.coroutines.flow.emptyFlow
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Test

/** The single publication path, against the real [ScreenPublisher] with a fake hidden source (no Room). */
class ScreenPublisherTest {
    private fun ins(id: String, doc: String) = Insight(id = id, documentId = doc)
    private var gained = 0
    private fun publisher(now: suspend (String) -> Set<String> = { emptySet() }) = ScreenPublisher(now, { emptyFlow() }) { gained++ }
        .also { it.reset("A") }

    @Test fun aPublicationForTheOldScopeAfterAResetIsDropped() = runBlocking {
        lateinit var p: ScreenPublisher
        p = publisher { scope -> p.reset("B"); emptySet() }
        val applied = p.publishFresh("A") { it.copy(results = listOf(ins("a", "d1")), searched = true) }
        assertFalse(applied)
        assertEquals("B", p.value.scope); assertTrue(p.value.results.isEmpty()); assertFalse(p.value.searched)
    }
    @Test fun aPublicationForTheCurrentScopeIsApplied() = runBlocking {
        val p = publisher()
        assertTrue(p.publishFresh("A") { it.copy(results = listOf(ins("a", "d1"))) })
        assertEquals(listOf("a"), p.value.results.map { it.id })
    }
    @Test fun aFreshReadPurgesEvenWhenTheSnapshotLags() = runBlocking {
        val p = publisher { setOf("d1") }
        p.hiddenArrived("A" to emptySet())
        p.publishFresh("A") { it.copy(selected = ins("a", "d1")) }
        assertNull(p.value.selected)
    }
    @Test fun theTransformRunsOnceEvenWhenTheSnapshotChangesMidPublish() {
        val p = publisher()
        var changed = false
        p.publishLocal { state ->
            if (!changed) { changed = true; p.hiddenArrived("A" to setOf("zzz")) }
            state.copy(chat = state.chat + ChatLine("Tutor", "answer"))
        }
        assertEquals(1, p.value.chat.size)
    }
    @Test fun aSnapshotThatArrivesMidPublishIsStillApplied() {
        val p = publisher()
        p.publishLocal { state -> p.hiddenArrived("A" to setOf("d1")); state.copy(feed = listOf(ins("a", "d1"), ins("b", "d2"))) }
        assertEquals(listOf("b"), p.value.feed.map { it.id })
    }
    @Test fun aSnapshotOfAnotherScopeIsUnknownSoTheFlowPublicationReadsRoom() = runBlocking {
        val p = publisher { scope -> if (scope == "A") setOf("d1") else emptySet() }
        p.hiddenArrived("OLD" to emptySet())
        p.publishFlow { it.copy(feed = listOf(ins("a", "d1"), ins("b", "d2"))) }
        assertEquals(listOf("b"), p.value.feed.map { it.id })
    }
    @Test fun aFlowPublicationDroppedWhenTheScopeChangedDuringTheRead() = runBlocking {
        lateinit var p: ScreenPublisher
        p = publisher { p.reset("B"); emptySet() }
        p.hiddenArrived("OLD" to emptySet())
        p.publishFlow { it.copy(feed = listOf(ins("a", "d1"))) }
        assertTrue(p.value.feed.isEmpty())
    }

    @Test fun aGainedHiddenIdClearsTheWholeChatAndNotifies() {
        val p = publisher()
        p.hiddenArrived("A" to emptySet())
        p.publishLocal { it.copy(selected = ins("x", "d2"), chat = listOf(ChatLine("You", "what is in my note?"), ChatLine("Tutor", "your note says secret")), chatBusy = true) }
        p.hiddenArrived("A" to setOf("d1"))
        assertTrue(p.value.chat.isEmpty()); assertFalse(p.value.chatBusy); assertEquals(1, gained)
        assertEquals("x", p.value.selected?.id)
    }
    @Test fun noGainLeavesTheChat() {
        val p = publisher()
        p.hiddenArrived("A" to setOf("d1"))
        p.publishLocal { it.copy(selected = ins("x", "d2"), chat = listOf(ChatLine("You", "hi"))) }
        p.hiddenArrived("A" to setOf("d1")); p.hiddenArrived("A" to emptySet())
        assertEquals(1, p.value.chat.size); assertEquals(0, gained)
    }
    @Test fun hiddenGainedOnlyForTheSameScopeAndNewIds() {
        assertTrue(hiddenGained("A" to emptySet(), "A" to setOf("d1")))
        assertTrue(hiddenGained("A" to setOf("d1"), "A" to setOf("d1", "d2")))
        assertFalse(hiddenGained("A" to setOf("d1"), "A" to setOf("d1")))
        assertFalse(hiddenGained("A" to setOf("d1"), "A" to emptySet()))
        assertFalse(hiddenGained("" to emptySet(), "A" to setOf("d1")))
    }

    private val question = ChatLine("You", "what does my note say?")
    @Test fun aReplyCitingAHiddenDocumentIsDroppedWithItsQuestion() {
        val state = ScreenState(scope = "A", chat = listOf(ChatLine("You", "earlier"), question))
        val next = state.withReply(ChatLine("Tutor", "it says secret", "q", listOf(Citation(documentId = "d1"))), setOf("d1"))
        assertEquals(listOf("earlier"), next.chat.map { it.text })
    }
    @Test fun aCleanReplyIsAppended() {
        val state = ScreenState(scope = "A", chat = listOf(question))
        val next = state.withReply(ChatLine("Tutor", "fine", "q", listOf(Citation(documentId = "d2"))), setOf("d1"))
        assertEquals(2, next.chat.size)
    }
    @Test fun aHiddenSelectionNeverProceedsToChat() {
        assertFalse(chatMayProceed(ins("a", "d1"), setOf("d1")))
        assertTrue(chatMayProceed(ins("a", "d2"), setOf("d1")))
        assertTrue(chatMayProceed(ins("a", ""), setOf("")))
    }
    @Test fun withChatClearedKeepsTheRest() {
        val state = ScreenState(scope = "A", selected = ins("a", "d2"), chat = listOf(question), chatBusy = true, learning = true)
        val next = state.withChatCleared()
        assertTrue(next.chat.isEmpty()); assertFalse(next.chatBusy); assertTrue(next.learning); assertEquals("a", next.selected?.id)
    }
}
