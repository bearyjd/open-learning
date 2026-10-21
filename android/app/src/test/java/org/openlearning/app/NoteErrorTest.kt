package org.openlearning.app

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.*
import org.junit.Test
import retrofit2.HttpException
import retrofit2.Response
import java.io.IOException

class NoteErrorTest {
    private val sentinel = "SENTINEL-USER-TEXT"
    private fun error(code: Int) = HttpException(Response.error<Any>(code, """{"detail":"$sentinel"}""".toResponseBody("application/json".toMediaType())))

    @Test fun fixedMessages() {
        assertTrue(error(413).noteMessage().contains("larger than your server accepts"))
        assertTrue(error(422).noteMessage().contains("100–60,000 chars, no control characters"))
        assertTrue(error(429).noteMessage().contains("busy, retrying automatically"))
        assertEquals(NOTE_EXPIRED, error(409).noteMessage())
    }
    @Test fun bodyIsNeverEchoed() {
        for (code in listOf(400, 401, 404, 409, 413, 422, 429, 500)) assertFalse("$code", error(code).noteMessage().contains(sentinel))
        assertFalse(IOException(sentinel).noteMessage().contains(sentinel))
        assertFalse(IllegalStateException(sentinel).noteMessage().contains(sentinel))
    }
    @Test fun goneResponsesCountAsSuccess() {
        assertTrue(Response.success<Unit>(204, Unit).isGone())
        assertTrue(Response.error<Unit>(404, "".toResponseBody()).isGone())
        assertFalse(Response.error<Unit>(500, "".toResponseBody()).isGone())
    }
}
