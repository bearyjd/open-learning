package org.openlearning.app

import org.junit.Assert.*
import org.junit.Test

class AttributionTest {
    private val note = Insight(documentId = "d1", sourceTitle = "My title", sourceKind = "note", sourceLabel = "Lecture 3")

    @Test fun noteUsesLabel() = assertEquals("Your note: Lecture 3", note.attribution())
    @Test fun blankLabelFallsBackToTitle() = assertEquals("Your note: My title", note.copy(sourceLabel = "  ").attribution())
    @Test fun urlSourceKeepsTitleOrDefault() {
        assertEquals("Example", Insight(sourceTitle = "Example").attribution())
        assertEquals("Source article", Insight().attribution())
    }
    @Test fun noteWithUrlIsNeverLinked() {
        assertNull(note.copy(sourceUrl = "https://evil.example").linkUrl())
        assertNull(Citation(sourceKind = "note", sourceUrl = "https://evil.example").linkUrl())
    }
    @Test fun urlKindLinksOnlyHttp() {
        assertEquals("https://a.example", Insight(sourceUrl = "https://a.example").linkUrl())
        assertNull(Insight(sourceUrl = "javascript:alert(1)").linkUrl())
        assertNull(Insight(sourceUrl = "").linkUrl())
    }
    @Test fun citationTitleFallbacks() {
        val citation = Citation(documentId = "d1", sourceKind = "note")
        assertEquals("My title", citationTitle(citation, note, "Cached"))
        assertEquals("Cached", citationTitle(citation, note.copy(documentId = "other"), "Cached"))
        assertEquals("Your note", citationTitle(citation, null, null))
        assertEquals("Your note", citationTitle(citation, null, " "))
    }
    @Test fun blankDocumentIdsNeverMatch() {
        val blank = Citation(documentId = "", sourceKind = "note")
        assertEquals("Your note", citationTitle(blank, note.copy(documentId = ""), null))
        assertEquals("Source article", citationTitle(Citation(documentId = ""), Insight(documentId = "", sourceTitle = "Wrong"), null))
    }
    @Test fun v1PayloadDecodesAsUrl() {
        val item = apiGson.fromJson("""{"id":"1","title":"t","source_title":"s","source_url":"https://x.example"}""", Insight::class.java)
        assertEquals("url", item.sourceKind)
        assertEquals("", item.sourceLabel)
        assertEquals("https://x.example", item.linkUrl())
        val citation = apiGson.fromJson("""{"document_id":"d","source_url":"https://x.example","passage":"p"}""", Citation::class.java)
        assertEquals("url", citation.sourceKind)
    }
    @Test fun nullLabelAndKindAreSafe() {
        val item = apiGson.fromJson("""{"source_title":"T","source_kind":"note","source_label":null,"source_url":null}""", Insight::class.java)
        assertEquals("Your note: T", item.attribution())
        assertNull(item.linkUrl())
        val nullKind = apiGson.fromJson("""{"source_kind":null,"source_title":"T","source_url":"https://a.example"}""", Insight::class.java)
        assertEquals("T", nullKind.attribution())
    }
}
