package org.openlearning.app

import kotlinx.coroutines.flow.emptyFlow
import org.junit.Assert.*
import org.junit.Test

/** The chat generation and the busy flag move together (pure, against the real publisher; no Room). */
class ChatSessionTest {
    private fun setup(): Pair<ScreenPublisher, ChatSession> {
        val p = ScreenPublisher({ emptySet() }, { emptyFlow() }).also { it.reset("A") }
        p.publishLocal { it.copy(selected = Insight(id = "x", documentId = "d2")) }
        return p to ChatSession(p)
    }

    @Test fun resetClearsTheChatAndBusyAndBumpsTheGeneration() {
        val (p, chat) = setup()
        val g = chat.begin("hi")!!
        chat.reset()
        assertTrue(p.value.chat.isEmpty()); assertFalse(p.value.chatBusy); assertFalse(chat.isCurrent(g))
    }
    @Test fun aSecondBumpAfterANewChatLeavesNothingStuckBusy() {
        val (p, chat) = setup()
        val g1 = chat.begin("one")!!
        chat.reset()                       // the hidden collector
        val g2 = chat.begin("two")!!       // another note, new chat
        chat.reset()                       // onForgotten from the forget completion
        assertFalse(p.value.chatBusy); assertTrue(p.value.chat.isEmpty())
        chat.end(g1); chat.end(g2)         // both late finals are no-ops
        assertFalse(p.value.chatBusy)
        assertNotNull(chat.begin("three"))
    }
    @Test fun aLateFinalOfAnOldChatNeverClearsTheBusyOfTheCurrentOne() {
        val (p, chat) = setup()
        val old = chat.begin("one")!!
        chat.reset()
        val current = chat.begin("two")!!
        chat.end(old)
        assertTrue(p.value.chatBusy)
        chat.end(current)
        assertFalse(p.value.chatBusy)
    }
    @Test fun aSecondBeginWhileBusyIsRefusedAndNoSelectionMeansNoChat() {
        val (p, chat) = setup()
        assertNotNull(chat.begin("one")); assertNull(chat.begin("two")); assertEquals(1, p.value.chat.size)
        val (_, empty) = ScreenPublisher({ emptySet() }, { emptyFlow() }).also { it.reset("A") }.let { it to ChatSession(it) }
        assertNull(empty.begin("x"))
    }
}
