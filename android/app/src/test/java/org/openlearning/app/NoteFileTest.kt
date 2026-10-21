package org.openlearning.app

import org.junit.Assert.*
import org.junit.Test
import java.io.ByteArrayInputStream
import java.io.InputStream

class NoteFileTest {
    private fun read(name: String?, bytes: ByteArray, size: Long? = bytes.size.toLong()) = readNoteFile(name, size) { ByteArrayInputStream(bytes) }
    private fun rejected(result: NoteFileResult) = (result as NoteFileResult.Rejected).message
    private fun loaded(result: NoteFileResult) = result as NoteFileResult.Loaded

    @Test fun oversizeIsRejectedWithoutOpening() {
        var opened = false
        val result = readNoteFile("a.txt", NOTE_FILE_MAX_BYTES + 1L) { opened = true; ByteArrayInputStream(ByteArray(0)) }
        assertTrue(result is NoteFileResult.Rejected)
        assertFalse(opened)
    }
    @Test fun readStopsAtCapPlusOne() {
        var served = 0
        val endless = object : InputStream() { override fun read(): Int { served++; return 'a'.code } 
            override fun read(b: ByteArray, off: Int, len: Int): Int { java.util.Arrays.fill(b, off, off + len, 'a'.code.toByte()); served += len; return len } }
        val result = readNoteFile("a.txt", null) { endless }
        assertTrue(result is NoteFileResult.Rejected)
        assertEquals(NOTE_FILE_MAX_BYTES + 1, served)
    }
    @Test fun exactlyAtCodePointCapIsAccepted() = assertEquals(NOTE_MAX_CHARS, loaded(read("a.txt", "a".repeat(NOTE_MAX_CHARS).toByteArray())).text.length)
    @Test fun oneOverCodePointCapIsRejected() =
        assertTrue(rejected(read("a.txt", "a".repeat(NOTE_MAX_CHARS + 1).toByteArray())).contains("60,000"))
    @Test fun codePointsNotUtf16UnitsAreCounted() {
        assertEquals(NOTE_MAX_CHARS * 2, loaded(read("a.txt", "😀".repeat(NOTE_MAX_CHARS).toByteArray())).text.length)
        assertTrue(read("a.txt", "😀".repeat(NOTE_MAX_CHARS + 1).toByteArray()) is NoteFileResult.Rejected)
    }
    @Test fun capUsesTheSameCrlfAndTrimMeasureAsValidation() {
        val padded = "\r\n".repeat(10) + "a".repeat(NOTE_MAX_CHARS) + "  \r\n\r\n"
        assertTrue(read("a.txt", padded.toByteArray()) is NoteFileResult.Loaded)
    }
    @Test fun longTitleIsCutOnCodePointBoundary() {
        val title = loaded(read("😀".repeat(NOTE_MAX_TITLE + 5) + ".txt", "x".toByteArray())).title
        assertEquals(NOTE_MAX_TITLE, title.codePointCount(0, title.length))
        assertFalse(hasBrokenPair(title))
        val odd = loaded(read("a" + "😀".repeat(NOTE_MAX_TITLE) + ".txt", "x".toByteArray())).title
        assertFalse(hasBrokenPair(odd))
    }
    @Test fun loadedToStringIsRedacted() = assertFalse(loaded(read("a.txt", "SECRET-BODY".toByteArray())).toString().contains("SECRET"))
    private fun hasBrokenPair(s: String) = s.isNotEmpty() && (Character.isHighSurrogate(s.last()) || Character.isLowSurrogate(s.first()))
    @Test fun invalidUtf8IsRejected() = assertTrue(rejected(read("a.txt", byteArrayOf(0x61, 0xC3.toByte(), 0x28))).contains("UTF-8"))
    @Test fun nulIsNotPlainText() = assertTrue(rejected(read("a.txt", byteArrayOf(0x61, 0x00, 0x62))).contains("plain text"))
    @Test fun utf16BomIsRejected() {
        assertTrue(read("a.txt", byteArrayOf(0xFF.toByte(), 0xFE.toByte(), 0x61, 0x00)) is NoteFileResult.Rejected)
        assertTrue(read("a.txt", byteArrayOf(0xFE.toByte(), 0xFF.toByte(), 0x00, 0x61)) is NoteFileResult.Rejected)
    }
    @Test fun utf8BomIsStripped() =
        assertEquals("héllo", loaded(read("a.txt", byteArrayOf(0xEF.toByte(), 0xBB.toByte(), 0xBF.toByte()) + "héllo".toByteArray())).text)
    @Test fun titleComesFromFileName() {
        assertEquals("My notes", loaded(read("My notes.md", "x".toByteArray())).title)
        assertEquals("a.b", loaded(read("a.b.MARKDOWN", "x".toByteArray())).title)
        assertEquals("Note", loaded(read(".txt", "x".toByteArray())).title)
    }
    @Test fun wrongExtensionIsRejectedWithoutOpening() {
        var opened = false
        assertTrue(readNoteFile("a.pdf", 10) { opened = true; null } is NoteFileResult.Rejected)
        assertTrue(readNoteFile(null, 10) { opened = true; null } is NoteFileResult.Rejected)
        assertFalse(opened)
    }
}
