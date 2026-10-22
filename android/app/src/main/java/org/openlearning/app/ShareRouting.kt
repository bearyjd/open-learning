package org.openlearning.app

/** Where text shared into the app goes. Never submits anything: the person confirms in the note editor. */
sealed interface ShareRoute {
    /** An http(s) link: goes to the import field, as before. */
    data class Url(val url: String) : ShareRoute
    /** Plain text: opens the note editor with this pre-filled. [text] is verbatim, so an over-limit share shows as over-limit. */
    data class Note(val title: String, val text: String) : ShareRoute
    data object Ignore : ShareRoute
}

const val SHARE_TITLE_MAX = 80
/** A share that is a link plus at most this much other text (a page title, "via ...") still counts as a link. */
const val SHARE_URL_EXTRA_MAX = 300

private val LINE_BREAKS = Regex("[\\r\\n\\u2028\\u2029]")

/** First non-blank line, trimmed and cut to [max] code points (never inside a surrogate pair). Blank when there is none. */
fun titleFromText(text: String, max: Int = SHARE_TITLE_MAX): String =
    text.split(LINE_BREAKS).firstOrNull { it.isNotBlank() }.orEmpty().trim().takeCodePoints(max).trim()

fun routeShare(shared: String?, limits: EffectiveLimits = DEFAULT_LIMITS): ShareRoute {
    if (shared.isNullOrBlank()) return ShareRoute.Ignore
    val url = sharedUrl(shared)
    if (url != null) {
        val extra = shared.replace(url, "").trim()
        if (extra.codePointCount(0, extra.length) <= SHARE_URL_EXTRA_MAX) return ShareRoute.Url(url)
    }
    return ShareRoute.Note(titleFromText(shared, minOf(SHARE_TITLE_MAX, limits.maxTitle)), shared)
}
