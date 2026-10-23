package org.openlearning.app

import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import okhttp3.mockwebserver.SocketPolicy
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger

/** GET /notes, GET /limits and DELETE /notes/{id}. Records only method and path, never bodies. */
internal class ListFixture {
    val server = MockWebServer()
    val requests = CopyOnWriteArrayList<RecordedRequest>()
    val down = AtomicBoolean(false)
    val deleteStatus = AtomicInteger(204)
    private val deleted = CopyOnWriteArrayList<String>()
    private val notes = mapOf(
        TestIds.DOC_A to """{"document_id":"${TestIds.DOC_A}","title":"Alpha note","label":"Lecture","created_at":"2026-10-22T09:30:00Z","char_count":1234}""",
        TestIds.DOC_B to """{"document_id":"${TestIds.DOC_B}","title":"Beta note","label":null,"created_at":"2026-10-21T08:00:00Z","char_count":456}""",
    )

    fun start() {
        server.dispatcher = object : Dispatcher() {
            // DISCONNECT_AT_START is only honoured at accept time, via peek(); a pooled keep-alive connection never gets there.
            override fun peek(): MockResponse = if (down.get()) downResponse() else super.peek()
            override fun dispatch(request: RecordedRequest): MockResponse {
                if (down.get()) return downResponse()
                requests += request
                if (request.getHeader("Authorization") != "Bearer alpha") return MockResponse().setResponseCode(401)
                val path = request.path.orEmpty().substringBefore('?')
                return when {
                    request.method == "GET" && path == "/api/v1/notes" -> json("[" + notes.filterKeys { it !in deleted }.values.joinToString(",") + "]")
                    request.method == "GET" && path == "/api/v1/limits" -> json("""{"max_document_chars":60000,"min_text_chars":100,"max_title_chars":100,"max_label_chars":100}""")
                    request.method == "DELETE" && path.startsWith("/api/v1/notes/") -> {
                        if (deleteStatus.get() in 200..299 || deleteStatus.get() == 404) deleted += path.substringAfterLast('/')
                        MockResponse().setResponseCode(deleteStatus.get())
                    }
                    path == "/api/v1/feed/today" || path == "/api/v1/library" -> json("[]")
                    else -> MockResponse().setResponseCode(404)
                }
            }
        }
        server.start()
    }
    private fun json(body: String) = MockResponse().addHeader("Content-Type", "application/json").setBody(body)
    // DISCONNECT_AFTER_REQUEST drops requests that arrive on an already-open connection; peek() covers new connections.
    private fun downResponse() = MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AFTER_REQUEST)
    fun count(method: String, path: String) = requests.count { it.method == method && it.path?.substringBefore('?') == path }
}
