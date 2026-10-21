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
    @Test fun exactlyAtCapIsAccepted() = loaded(read("a.txt", ByteArray(NOTE_FILE_MAX_BYTES) { 'a'.code.toByte() }))
        .let { assertEquals(NOTE_FILE_MAX_BYTES, it.text.length) }
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
