// SPDX-License-Identifier: GPL-3.0-only
package org.openlearning.app

import com.google.gson.JsonParser
import retrofit2.HttpException
import java.io.IOException
import java.net.URI
import java.text.Normalizer
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter
import java.util.Locale

const val DISCOVERY_CONNECTION_ERROR = "Reading queue needs a connection. Check your connection and try again."
const val DISCOVERY_RANKING_CHANGED = "Your topics changed. The reading queue has been refreshed."
const val DISCOVERY_TOPIC_INVALID = "Enter a topic with 3–80 characters and up to 5 words."

/** Python uses Unicode code points (not UTF-16 length), NFKC/lower/NFKC, and category C rejection. */
fun normalizeTopic(input: String): String? {
    val points = input.codePoints().toArray()
    if (points.size !in 1..80 || points.any { Character.getType(it) in setOf(
            Character.CONTROL.toInt(), Character.FORMAT.toInt(), Character.SURROGATE.toInt(),
            Character.PRIVATE_USE.toInt(), Character.UNASSIGNED.toInt()) }) return null
    val normalized = Normalizer.normalize(Normalizer.normalize(input, Normalizer.Form.NFKC).lowercase(Locale.ROOT), Normalizer.Form.NFKC)
    val words = mutableListOf<String>()
    val token = StringBuilder()
    normalized.codePoints().forEach { point ->
        val type = Character.getType(point)
        val alnum = Character.isLetter(point) || type in setOf(Character.DECIMAL_DIGIT_NUMBER.toInt(),
            Character.LETTER_NUMBER.toInt(), Character.OTHER_NUMBER.toInt())
        val mark = type in setOf(Character.NON_SPACING_MARK.toInt(), Character.COMBINING_SPACING_MARK.toInt(), Character.ENCLOSING_MARK.toInt())
        if (alnum || (token.isNotEmpty() && mark)) token.appendCodePoint(point)
        else if (token.isNotEmpty()) { words += token.toString(); token.setLength(0) }
    }
    if (token.isNotEmpty()) words += token.toString()
    val result = words.joinToString(" ")
    return result.takeIf { words.size in 1..5 && it.codePointCount(0, it.length) in 3..80 }
}

fun validFeedUrl(input: String): String? {
    // Validate before trimming so control characters cannot be hidden by the UI normalization.
    if (input.any { it.isISOControl() }) return null
    val value = input.trim()
    if (value.length !in 1..2048) return null
    val uri = runCatching { URI(value) }.getOrNull() ?: return null
    return value.takeIf { uri.scheme?.lowercase(Locale.ROOT) in setOf("http", "https") &&
        !uri.host.isNullOrBlank() && uri.userInfo == null && uri.rawFragment == null }
}

fun feedErrorMessage(code: String?): String = when (code) {
    "feed_invalid_url", "feed_url_invalid" -> "That feed URL is not allowed."
    "feed_invalid", "feed_invalid_response", "feed_encoding" -> "The server could not read a valid RSS or Atom feed."
    "feed_too_large" -> "This feed is too large."
    "feed_timeout" -> "The feed took too long to respond. Try again."
    "feed_redirect_error" -> "The feed redirected to an address the server could not use."
    "feed_http_error", "feed_connection_error", "feed_incomplete", "feed_fetch_failed" -> "The server could not fetch this feed. Try again."
    else -> "The server could not refresh this feed. Try again."
}

/** Only exact known codes select fixed text. Never render arbitrary HTTP bodies or exception messages. */
fun discoveryError(error: Throwable): String {
    if (error is IOException) return DISCOVERY_CONNECTION_ERROR
    if (error !is HttpException) return "Could not complete this request. Please try again."
    val detail = runCatching {
        val body = error.response()?.errorBody() ?: return@runCatching null
        body.use {
            val source = it.source()
            source.request(4097)
            if (source.buffer.size > 4096) null else JsonParser.parseString(source.buffer.clone().readUtf8())
                .asJsonObject.get("detail")?.takeIf { field -> field.isJsonPrimitive && field.asJsonPrimitive.isString }?.asString
        }
    }.getOrNull()
    return when (error.code()) {
        401, 403 -> "Access denied. Check your server token in Connection."
        404 -> "This item is no longer available. Refresh and try again."
        409 -> when (detail) {
            "Feed already subscribed" -> "This feed is already subscribed."
            "Feed subscription limit reached" -> "You can subscribe to up to 50 feeds."
            "Pinned topic limit reached" -> "You can pin up to 30 topics."
            else -> "This item changed. Refresh and try again."
        }
        422 -> if (detail in setOf("feed_invalid_url", "feed_url_invalid", "feed_invalid", "feed_invalid_response",
            "feed_encoding", "feed_too_large", "feed_timeout", "feed_redirect_error", "feed_http_error",
            "feed_connection_error", "feed_incomplete", "feed_fetch_failed")) feedErrorMessage(detail)
            else "Check the address or topic and try again."
        429 -> "The server is busy. Wait before trying again."
        in 500..599 -> "The server could not complete this request. Try again."
        else -> "Could not complete this request. Please try again."
    }
}

/** Feed refresh follows the existing 30-second minimum when the server omits a valid delay. */
fun discoveryRetryDelay(header: String?, now: Long): Long {
    val seconds = header?.trim()?.takeIf { it.isNotEmpty() && it.all(Char::isDigit) }
    if (seconds != null) return (seconds.toLongOrNull()?.coerceAtMost(86_400)?.times(1000) ?: 86_400_000).coerceAtLeast(1_000)
    val date = runCatching { ZonedDateTime.parse(header, DateTimeFormatter.RFC_1123_DATE_TIME).toInstant().toEpochMilli() }.getOrNull()
    return date?.let { (it - now).coerceIn(1_000, 86_400_000) } ?: 30_000
}
