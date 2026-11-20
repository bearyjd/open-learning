package org.openlearning.app

import org.junit.Assert.*
import org.junit.Test

class ChatHistoryTest {
    private val card = Insight(id = "x", documentId = "d1")
    private val state = ScreenState(selected = card)
    private val lines = listOf(ChatLine("You", "Why?"), ChatLine("Tutor", "Because."))

    @Test fun mapperTurnsRolesIntoChatLinesAndSkipsUnknown() {
        val rows = listOf(HistoryLine("x", "user", "Why?"), HistoryLine("x", "assistant", "Because."), HistoryLine("x", "system", "hidden"))
        assertEquals(lines, rows.toChatLines())
    }
    @Test fun currentGenerationFillsTheChat() {
        assertEquals(lines, state.withHistory("x", lines, emptySet(), current = true).chat)
    }
    @Test fun staleGenerationNeverGetsLines() {
        assertTrue(state.withHistory("x", lines, emptySet(), current = false).chat.isEmpty())
    }
    @Test fun otherOrClosedCardNeverGetsLines() {
        assertTrue(state.withHistory("y", lines, emptySet(), current = true).chat.isEmpty())
        assertTrue(ScreenState().withHistory("x", lines, emptySet(), current = true).chat.isEmpty())
    }
    @Test fun hiddenCardNeverGetsLines() {
        assertTrue(state.withHistory("x", lines, setOf("d1"), current = true).chat.isEmpty())
    }
    @Test fun aChatAlreadyInProgressIsKept() {
        val busy = state.copy(chat = listOf(ChatLine("You", "now")), chatBusy = true)
        assertEquals(busy, busy.withHistory("x", lines, emptySet(), current = true))
    }
    @Test fun resetBumpsTheGenerationSoAnOldFetchIsStale() {
        val p = ScreenPublisher({ emptySet() }, { kotlinx.coroutines.flow.emptyFlow() }).also { it.reset("A") }
        val session = ChatSession(p)
        val g = session.generation
        session.reset()
        assertFalse(session.isCurrent(g))
    }
}
