package org.openlearning.app

import org.junit.Assert.*
import org.junit.Test

class NotesApiTest {
    private fun job(json: String) = apiGson.fromJson(json, IngestJob::class.java)
    @Test fun jobDocumentIdPresentAbsentOrNull() {
        assertEquals("d1", job("""{"id":"j","status":"completed","document_id":"d1"}""").documentId)
        assertNull(job("""{"id":"j","status":"completed"}""").documentId)
        assertNull(job("""{"id":"j","status":"completed","document_id":null}""").documentId)
    }
    @Test fun limitsDecodeAndTolerateMissingFields() {
        val full = apiGson.fromJson("""{"max_document_chars":1000,"min_text_chars":5,"max_title_chars":50,"max_label_chars":20}""", Limits::class.java)
        assertEquals(Limits(1000, 5, 50, 20), full)
        assertEquals(Limits(), apiGson.fromJson("{}", Limits::class.java))
    }
    @Test fun noteListDecodesIntoSummaries() {
        val items = apiGson.fromJson("""[{"document_id":"d1","title":"T","label":"L","created_at":"2026-01-02T03:04:05Z","char_count":321},
            {"document_id":"d2","title":null,"label":"","created_at":null,"char_count":null},{"title":"no id"}]""", Array<NoteListItem>::class.java).toList()
        val summaries = items.toSummaries()
        assertEquals(2, summaries.size)
        assertEquals(NoteSummary("d1", "T", "L", "2026-01-02T03:04:05Z", 321), summaries[0])
        assertEquals("2026-01-02", summaries[0].createdDate)
        assertEquals(NoteSummary("d2", "Untitled", null, "", 0), summaries[1])
    }
}
