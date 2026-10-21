package org.openlearning.app

import org.junit.Assert.*
import org.junit.Test

class NoteValidationTest {
    private fun body(n: Int, ch: String = "a") = ch.repeat(n)
    private fun check(text: String, title: String = "Title", label: String? = null) = validateNote(title, text, label)
    private fun valid(result: NoteCheck) = (result as NoteCheck.Valid).note
    private fun invalid(result: NoteCheck) = assertTrue(result.toString(), result is NoteCheck.Invalid)

    @Test fun textBounds() {
        invalid(check(body(99)))
        assertEquals(100, valid(check(body(100))).text.length)
        assertEquals(60_000, valid(check(body(60_000))).text.length)
        invalid(check(body(60_001)))
    }
    @Test fun emojiCountsAsOneCodePoint() {
        assertEquals(100, valid(check("😀".repeat(100))).text.codePointCount(0, 200))
        invalid(check("😀".repeat(99)))
        assertEquals(60_000, valid(check("😀".repeat(60_000))).text.codePointCount(0, 120_000))
    }
    @Test fun crlfAndCrBecomeLf() {
        val note = valid(check("a\r\nb\rc" + body(100)))
        assertFalse(note.text.contains('\r'))
        assertTrue(note.text.startsWith("a\nb\nc"))
    }
    @Test fun normalisesToNfcAndTrims() {
        assertEquals("é", valid(check("  é" + body(100) + "  ")).text.take(1))
        assertEquals("T", valid(check(body(100), title = "  T ")).title)
    }
    @Test fun titleBounds() {
        invalid(check(body(100), title = ""))
        invalid(check(body(100), title = "   "))
        assertEquals(500, valid(check(body(100), title = body(500))).title.length)
        invalid(check(body(100), title = body(501)))
    }
    @Test fun labelBoundsAndBlankBecomesNull() {
        assertNull(valid(check(body(100), label = "   ")).label)
        assertNull(valid(check(body(100), label = null)).label)
        assertEquals(100, valid(check(body(100), label = body(100))).label!!.length)
        invalid(check(body(100), label = body(101)))
    }
    @Test fun bodyAllowsTabNewlineAndParagraphSeparators() {
        valid(check("a\tb\nc d " + body(100)))
    }
    @Test fun bodyRejectsControls() {
        for (c in listOf('\u0000', '\u0001', '\u000b', '\u001f', '\u007f', '\u0085', '\u009f', '‪', '‮', '⁦', '⁩'))
            invalid(check("a${c}b" + body(100)))
    }
    @Test fun edgeControlIsRejectedNotTrimmed() = invalid(check(body(100) + "\u0085"))
    @Test fun titleAndLabelRejectAnyControl() {
        for (c in listOf('\t', '\n', '\r', '\u0000', ' ', ' ', '\u0085', '‭'))
            { invalid(check(body(100), title = "a${c}b")); invalid(check(body(100), label = "a${c}b")) }
    }
    @Test fun rejectsLoneSurrogates() {
        invalid(check("\ud800" + body(100)))
        invalid(check(body(100) + "\udc00"))
        invalid(check(body(100), title = "a\ud800"))
        invalid(check(body(100), label = "\udc00"))
        valid(check("😀" + body(100)))
    }
    @Test fun errorsNeverEchoInput() {
        val result = check("SENTINEL\u0000" + body(100)) as NoteCheck.Invalid
        assertFalse(result.message.contains("SENTINEL"))
    }
    @Test fun redactedToString() {
        val row = PendingIngest("s", "i", "", kind = "note", text = "SECRET-BODY")
        assertFalse(row.toString().contains("SECRET-BODY"))
        assertFalse(NoteDraft("s", "t", "l", "SECRET-BODY").toString().contains("SECRET-BODY"))
    }
}
