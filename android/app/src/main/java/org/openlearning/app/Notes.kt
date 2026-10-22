package org.openlearning.app

import java.io.ByteArrayOutputStream
import java.io.IOException
import java.io.InputStream
import java.nio.ByteBuffer
import java.nio.charset.CharacterCodingException
import java.nio.charset.CodingErrorAction
import java.text.Normalizer
import retrofit2.HttpException

// Mirrors backend/open_learning/notes.py. The server stays authoritative: its 422 decides.
const val NOTE_MIN_CHARS = 100
const val NOTE_MAX_CHARS = 60_000
const val NOTE_MAX_TITLE = 500
const val NOTE_MAX_LABEL = 100
const val NOTE_EXPIRED = "This note's text expired; add it again."

data class ValidNote(val title: String, val text: String, val label: String?) {
    override fun toString() = "ValidNote(text=<redacted>)"
}

sealed interface NoteCheck {
    data class Valid(val note: ValidNote) : NoteCheck {
        override fun toString() = "Valid(note=<redacted>)"
    }
    data class Invalid(val message: String) : NoteCheck
}

private fun isBidi(c: Char) = c in '\u202A'..'\u202E' || c in '\u2066'..'\u2069'
private fun isC1OrDel(c: Char) = c in '\u007f'..'\u009f'
private fun hasLoneSurrogate(s: String): Boolean {
    var i = 0
    while (i < s.length) {
        val c = s[i]
        if (Character.isHighSurrogate(c) && i + 1 < s.length && Character.isLowSurrogate(s[i + 1])) i += 2
        else if (Character.isSurrogate(c)) return true
        else i++
    }
    return false
}
private fun codePoints(s: String) = s.codePointCount(0, s.length)
/** Like take(), but never splits a surrogate pair. */
internal fun String.takeCodePoints(count: Int): String =
    if (codePoints(this) <= count) this else substring(0, offsetByCodePoints(0, count))
private fun String.strip() = trim { it.isWhitespace() }
// C0 except tab/newline, DEL, C1, bidi. U+2028/2029 stay allowed in the body.
private fun badTextChar(c: Char) = (c < ' ' && c != '\t' && c != '\n') || isC1OrDel(c) || isBidi(c)
private fun badLineChar(c: Char) = c < ' ' || isC1OrDel(c) || c == '\u2028' || c == '\u2029' || isBidi(c)

fun validateNote(title: String, text: String, label: String?, limits: EffectiveLimits = DEFAULT_LIMITS): NoteCheck {
    if (hasLoneSurrogate(title) || hasLoneSurrogate(text) || (label != null && hasLoneSurrogate(label)))
        return NoteCheck.Invalid("Text must be valid Unicode.")
    val body = Normalizer.normalize(text.replace("\r\n", "\n").replace('\r', '\n'), Normalizer.Form.NFC)
    // Check before trim: trimming would silently drop edge controls such as U+0085.
    if (body.any(::badTextChar)) return NoteCheck.Invalid("Text contains control characters that can't be saved.")
    val cleanText = body.strip()
    if (codePoints(cleanText) !in limits.minChars..limits.maxChars)
        return NoteCheck.Invalid("Text must be between ${formatCount(limits.minChars)} and ${formatCount(limits.maxChars)} characters.")
    val rawTitle = Normalizer.normalize(title, Normalizer.Form.NFC)
    if (rawTitle.any(::badLineChar)) return NoteCheck.Invalid("Title must be a single line.")
    val cleanTitle = rawTitle.strip()
    if (codePoints(cleanTitle) !in 1..limits.maxTitle) return NoteCheck.Invalid("Title must be between 1 and ${formatCount(limits.maxTitle)} characters.")
    val rawLabel = label?.let { Normalizer.normalize(it, Normalizer.Form.NFC) }
    if (rawLabel != null && rawLabel.any(::badLineChar)) return NoteCheck.Invalid("Label must be a single line.")
    val cleanLabel = rawLabel?.strip()?.takeIf { it.isNotEmpty() }
    if (cleanLabel != null && codePoints(cleanLabel) > limits.maxLabel) return NoteCheck.Invalid("Label must be at most ${formatCount(limits.maxLabel)} characters.")
    return NoteCheck.Valid(ValidNote(cleanTitle, cleanText, cleanLabel))
}

sealed interface NoteFileResult {
    data class Loaded(val title: String, val text: String) : NoteFileResult {
        override fun toString() = "Loaded(text=<redacted>)"
    }
    data class Rejected(val message: String) : NoteFileResult
}

private val NOTE_EXTENSIONS = listOf(".txt", ".md", ".markdown")

/** [open] is called only after the name and size checks pass. The caller must never keep the Uri. */
fun readNoteFile(name: String?, size: Long?, limits: EffectiveLimits = DEFAULT_LIMITS, open: () -> InputStream?): NoteFileResult {
    val maxBytes = noteFileMaxBytes(limits)
    val fileName = name.orEmpty()
    val extension = NOTE_EXTENSIONS.firstOrNull { fileName.lowercase().endsWith(it) }
        ?: return NoteFileResult.Rejected("Choose a .txt or .md file.")
    if (size != null && size > maxBytes) return NoteFileResult.Rejected(fileTooLargeMessage(limits))
    val bytes = try {
        (open() ?: return NoteFileResult.Rejected("Could not open that file.")).use { readCapped(it, maxBytes) }
    } catch (error: IOException) {
        return NoteFileResult.Rejected("Could not read that file.")
    }
    if (bytes.size > maxBytes) return NoteFileResult.Rejected(fileTooLargeMessage(limits))
    val utf16 = bytes.size >= 2 && ((bytes[0] == 0xFF.toByte() && bytes[1] == 0xFE.toByte()) || (bytes[0] == 0xFE.toByte() && bytes[1] == 0xFF.toByte()))
    if (utf16) return NoteFileResult.Rejected("That file is not UTF-8 text.")
    if (bytes.any { it == 0.toByte() }) return NoteFileResult.Rejected("That file is not plain text.")
    val decoded = try {
        Charsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT).onUnmappableCharacter(CodingErrorAction.REPORT)
            .decode(ByteBuffer.wrap(bytes)).toString()
    } catch (error: CharacterCodingException) {
        return NoteFileResult.Rejected("That file is not UTF-8 text.")
    }
    val text = decoded.removePrefix("\uFEFF")
    // Same measure validateNote applies, so a file that loads here can still be submitted.
    if (codePoints(text.replace("\r\n", "\n").replace('\r', '\n').strip()) > limits.maxChars)
        return NoteFileResult.Rejected("That file has more than ${formatCount(limits.maxChars)} characters.")
    val title = fileName.dropLast(extension.length).map { if (badLineChar(it)) ' ' else it }.joinToString("").strip().takeCodePoints(limits.maxTitle)
    return NoteFileResult.Loaded(title.ifEmpty { "Note" }, text)
}

private fun readCapped(input: InputStream, maxBytes: Int): ByteArray {
    val out = ByteArrayOutputStream()
    val buffer = ByteArray(8192)
    var remaining = maxBytes + 1
    while (remaining > 0) {
        val read = input.read(buffer, 0, minOf(buffer.size, remaining))
        if (read < 0) break
        out.write(buffer, 0, read)
        remaining -= read
    }
    return out.toByteArray()
}

/** Fixed strings only: the response body can echo user text, so it is never read. */
fun Throwable.noteMessage(): String = when ((this as? HttpException)?.code()) {
    413 -> "This note is larger than your server accepts."
    422 -> "The server rejected it: 100–60,000 chars, no control characters."
    429 -> "The server is busy, retrying automatically."
    409 -> NOTE_EXPIRED
    else -> userMessage()
}

fun Insight.isNote() = sourceKind == "note"

/** Gson can leave non-null Kotlin fields null when the payload says null, so read through nullable views. */
private fun String?.safe() = this.orEmpty()

fun Insight.attribution(): String {
    val title: String? = sourceTitle
    if (isNote()) {
        val label: String? = sourceLabel
        val who = label.safe().ifBlank { title.safe() }
        return if (who.isBlank()) "Your note" else "Your note: $who"
    }
    return title.safe().ifBlank { "Source article" }
}

private fun httpOrNull(url: String?) = url?.takeIf { it.startsWith("https://") || it.startsWith("http://") }
/** Notes are never linked, even if the server sent a URL. */
fun Insight.linkUrl(): String? = if (isNote()) null else httpOrNull(sourceUrl)
fun Citation.linkUrl(): String? = if (sourceKind == "note") null else httpOrNull(sourceUrl)

fun citationTitle(citation: Citation, selected: Insight?, cachedTitle: String?): String {
    if (selected != null && citation.documentId.isNotBlank() && selected.documentId == citation.documentId && selected.sourceTitle.isNotBlank()) return selected.sourceTitle
    cachedTitle?.takeIf { it.isNotBlank() }?.let { return it }
    return if (citation.sourceKind == "note") "Your note" else "Source article"
}
