package org.openlearning.app

import org.junit.Assert.*
import org.junit.Test
import java.io.ByteArrayInputStream

class NoteLimitsTest {
    @Test fun nullOrEmptyServerLimitsGiveDefaults() {
        assertEquals(DEFAULT_LIMITS, effectiveLimits(null))
        assertEquals(DEFAULT_LIMITS, effectiveLimits(Limits()))
        assertEquals(EffectiveLimits(60_000, 100, 500, 100), DEFAULT_LIMITS)
    }
    @Test fun serverValuesWinFieldByField() {
        assertEquals(EffectiveLimits(1000, 100, 500, 100), effectiveLimits(Limits(maxDocumentChars = 1000)))
        assertEquals(EffectiveLimits(2000, 10, 20, 30), effectiveLimits(Limits(2000, 10, 20, 30)))
    }
    @Test fun invalidServerValuesFallBack() {
        assertEquals(DEFAULT_LIMITS, effectiveLimits(Limits(0, -5, 0, -1)))
        assertEquals(DEFAULT_LIMITS, effectiveLimits(Limits(maxDocumentChars = 50, minTextChars = 100)))
    }
    @Test fun fileCapIsFourBytesPerCharCappedAtOneMiB() {
        assertEquals(240_000, noteFileMaxBytes(DEFAULT_LIMITS))
        assertEquals(4_000, noteFileMaxBytes(effectiveLimits(Limits(maxDocumentChars = 1000, minTextChars = 10))))
        assertEquals(1024 * 1024, noteFileMaxBytes(effectiveLimits(Limits(maxDocumentChars = 500_000))))
        assertEquals(1024 * 1024, noteFileMaxBytes(EffectiveLimits(maxChars = Int.MAX_VALUE)))
    }
    @Test fun validationUsesServerLimits() {
        val limits = effectiveLimits(Limits(maxDocumentChars = 200, minTextChars = 20, maxTitleChars = 5, maxLabelChars = 3))
        assertTrue(validateNote("Title", "a".repeat(20), null, limits) is NoteCheck.Valid)
        assertEquals("Text must be between 20 and 200 characters.", (validateNote("T", "a".repeat(19), null, limits) as NoteCheck.Invalid).message)
        assertTrue(validateNote("T", "a".repeat(201), null, limits) is NoteCheck.Invalid)
        assertEquals("Title must be between 1 and 5 characters.", (validateNote("Too long", "a".repeat(20), null, limits) as NoteCheck.Invalid).message)
        assertEquals("Label must be at most 3 characters.", (validateNote("T", "a".repeat(20), "label", limits) as NoteCheck.Invalid).message)
        assertTrue(validateNote("T", "a".repeat(201), null) is NoteCheck.Valid)
    }
    @Test fun fileReaderUsesServerLimits() {
        val limits = effectiveLimits(Limits(maxDocumentChars = 1000, minTextChars = 10))
        val ok = readNoteFile("a.txt", 4000, limits) { ByteArrayInputStream("a".repeat(1000).toByteArray()) }
        assertTrue(ok is NoteFileResult.Loaded)
        val tooBig = readNoteFile("a.txt", 4001, limits) { ByteArrayInputStream(ByteArray(0)) }
        assertEquals("That file is larger than 4 KB.", (tooBig as NoteFileResult.Rejected).message)
        val tooLong = readNoteFile("a.txt", null, limits) { ByteArrayInputStream("a".repeat(1001).toByteArray()) }
        assertTrue((tooLong as NoteFileResult.Rejected).message.contains("1,000"))
        assertEquals("That file is larger than 1 MB.", (readNoteFile("a.txt", 2_000_000, effectiveLimits(Limits(maxDocumentChars = 900_000))) { null } as NoteFileResult.Rejected).message)
    }
    @Test fun counterShowsTheEffectiveLimit() {
        assertEquals("3 / 1,000", noteCounter("abc", 1000))
        assertEquals("3 / 60,000", noteCounter("abc"))
    }
}
