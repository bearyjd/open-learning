package org.openlearning.app

import org.junit.Assert.*
import org.junit.Test

/** The pure part of the one cache writer: which rows may be written, and with which flags. */
class CardWriterTest {
    private fun card(id: String, doc: String = "", saved: Boolean = false) = Insight(id = id, documentId = doc, saved = saved)
    private fun row(id: String, inFeed: Boolean, saved: Boolean = false) = CachedInsight("s", id, "{}", saved, inFeed)

    @Test fun hiddenDocumentsAreNeverWritten() {
        val rows = cardRows("s", listOf(card("a", "d1"), card("b", "d2")), setOf("d1"), emptyMap(), CardList.FEED)
        assertEquals(listOf("b"), rows.map { it.id })
    }
    @Test fun blankDocumentIdIsNeverHidden() {
        val rows = cardRows("s", listOf(card("a", "")), setOf(""), emptyMap(), null)
        assertEquals(listOf("a"), rows.map { it.id })
    }
    @Test fun feedReplaceMarksRowsInFeed() {
        val rows = cardRows("s", listOf(card("a")), emptySet(), emptyMap(), CardList.FEED)
        assertTrue(rows.single().inFeed)
        assertEquals("s", rows.single().scope)
    }
    @Test fun libraryReplaceMarksSavedAndKeepsInFeed() {
        val previous = mapOf("a" to row("a", inFeed = true), "b" to row("b", inFeed = false))
        val rows = cardRows("s", listOf(card("a"), card("b"), card("c")), emptySet(), previous, CardList.LIBRARY)
        assertEquals(listOf(true, true, true), rows.map { it.saved })
        assertEquals(listOf(true, false, false), rows.map { it.inFeed })
        assertTrue(apiGson.fromJson(rows[0].payload, Insight::class.java).saved)
    }
    @Test fun plainWriteKeepsInFeedFromThePreviousRow() {
        val previous = mapOf("a" to row("a", inFeed = true))
        val rows = cardRows("s", listOf(card("a"), card("z")), emptySet(), previous, null)
        assertEquals(listOf(true, false), rows.map { it.inFeed })
    }
    @Test fun plainWriteUsesTheItemsOwnSavedFlag() {
        val rows = cardRows("s", listOf(card("a", saved = true), card("b")), emptySet(), emptyMap(), null)
        assertEquals(listOf(true, false), rows.map { it.saved })
    }
    @Test fun payloadRoundTripsTheDocumentId() {
        val rows = cardRows("s", listOf(card("a", "d9")), emptySet(), emptyMap(), null)
        assertEquals("d9", documentIdOfPayload(rows.single().payload))
    }
    @Test fun nothingToWriteGivesNoRows() {
        assertTrue(cardRows("s", emptyList(), setOf("d"), emptyMap(), CardList.FEED).isEmpty())
    }
}
