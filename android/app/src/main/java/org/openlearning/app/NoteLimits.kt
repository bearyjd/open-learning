package org.openlearning.app

import java.text.NumberFormat
import java.util.Locale

/** The limits the editor and validation apply: the server's when known, else the defaults mirrored from the backend. */
data class EffectiveLimits(
    val maxChars: Int = NOTE_MAX_CHARS, val minChars: Int = NOTE_MIN_CHARS,
    val maxTitle: Int = NOTE_MAX_TITLE, val maxLabel: Int = NOTE_MAX_LABEL,
)

val DEFAULT_LIMITS = EffectiveLimits()

/** A missing, non-positive or contradictory (min above max) server value falls back to the default. */
fun effectiveLimits(server: Limits?): EffectiveLimits {
    fun pick(value: Int?, default: Int) = value?.takeIf { it > 0 } ?: default
    val max = pick(server?.maxDocumentChars, NOTE_MAX_CHARS)
    val min = pick(server?.minTextChars, NOTE_MIN_CHARS)
    val (maxChars, minChars) = if (min > max) NOTE_MAX_CHARS to NOTE_MIN_CHARS else max to min
    return EffectiveLimits(maxChars, minChars, pick(server?.maxTitleChars, NOTE_MAX_TITLE), pick(server?.maxLabelChars, NOTE_MAX_LABEL))
}

const val NOTE_FILE_HARD_CAP_BYTES = 1024 * 1024

/** Four bytes per code point covers any UTF-8 text at the limit; never more than 1 MiB. */
fun noteFileMaxBytes(limits: EffectiveLimits): Int = minOf(4L * limits.maxChars, NOTE_FILE_HARD_CAP_BYTES.toLong()).toInt()

fun formatCount(n: Int): String = NumberFormat.getIntegerInstance(Locale.US).format(n)

fun fileTooLargeMessage(limits: EffectiveLimits): String {
    val cap = noteFileMaxBytes(limits)
    return "That file is larger than ${if (cap >= NOTE_FILE_HARD_CAP_BYTES) "1 MB" else "${cap / 1000} KB"}."
}
