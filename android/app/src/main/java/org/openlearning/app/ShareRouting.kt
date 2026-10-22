package org.openlearning.app

/** Where text shared into the app goes. Never submits anything: the person confirms in the note editor. */
sealed interface ShareRoute {
    /** An http(s) link: goes to the import field, as before. */
    data class Url(val url: String) : ShareRoute
    /** Plain text: opens the note editor with this pre-filled. [text] is verbatim, so an over-limit share shows as over-limit. */
    data class Note(val title: String, val text: String) : ShareRoute
    /** Over the share cap: rejected before it reaches the draft, never truncated. */
    data class TooLong(val chars: Int, val cap: Int) : ShareRoute
    data object Ignore : ShareRoute
}

const val SHARE_TITLE_MAX = 80
/** A share that is a link plus at most this much other text (a page title, "via ...") still counts as a link. */
const val SHARE_URL_EXTRA_MAX = 300
/** A share may overshoot the note limit by this much (the editor then shows it as over-limit) but never past the hard ceiling. */
const val SHARE_OVER_LIMIT_SLACK = 10_000
const val SHARE_HARD_CAP_CHARS = 256_000

fun shareCap(limits: EffectiveLimits): Int = minOf(limits.maxChars.toLong() + SHARE_OVER_LIMIT_SLACK, SHARE_HARD_CAP_CHARS.toLong()).toInt()

private val HTTP_URL = Regex("https?://", RegexOption.IGNORE_CASE)

private val LINE_BREAKS = Regex("[\\r\\n\\u2028\\u2029]")

/** First non-blank line, trimmed and cut to [max] code points (never inside a surrogate pair). Blank when there is none. */
fun titleFromText(text: String, max: Int = SHARE_TITLE_MAX): String =
    text.split(LINE_BREAKS).firstOrNull { it.isNotBlank() }.orEmpty().trim().takeCodePoints(max).trim()

fun routeShare(shared: String?, limits: EffectiveLimits = DEFAULT_LIMITS): ShareRoute {
    if (shared.isNullOrBlank()) return ShareRoute.Ignore
    val chars = shared.codePointCount(0, shared.length)
    val cap = shareCap(limits)
    if (chars > cap) return ShareRoute.TooLong(chars, cap)
    val url = sharedUrl(shared)
    if (url != null) {
        val extra = shared.replace(url, "").trim()
        // A second link in the leftover text means this is a note: importing only the first would silently drop it.
        if (extra.codePointCount(0, extra.length) <= SHARE_URL_EXTRA_MAX && !HTTP_URL.containsMatchIn(extra)) return ShareRoute.Url(url)
    }
    return ShareRoute.Note(titleFromText(shared, minOf(SHARE_TITLE_MAX, limits.maxTitle)), shared)
}
