// SPDX-License-Identifier: GPL-3.0-only
package org.openlearning.app

import org.junit.Assert.*
import org.junit.Test
import java.io.File

class DiscoveryJobTest {
    @Test fun discoveryJobsNeverShowBackendStatusOrErrorText() {
        val row = PendingIngest("scope", "local", "", jobId = "server", status = "SECRET-STATUS", error = "SECRET-ERROR", kind = "discovery")
        assertEquals("Reading queue import", row.discoveryJobLabel())
        assertEquals("Processing", row.discoveryJobStatus())
        assertEquals("This import could not be completed. Try again.", row.discoveryJobError())
        assertFalse(row.discoveryJobStatus().contains("SECRET"))
    }
    @Test fun knownStatusesAreFixedAndMissingErrorsStayAbsent() {
        val row = PendingIngest("scope", "local", "", kind = "discovery", status = "completed")
        assertEquals("Completed", row.discoveryJobStatus())
        assertNull(row.discoveryJobError())
        assertEquals("Failed", row.copy(status = "failed").discoveryJobStatus())
    }
    @Test fun workerSanitizesDiscoveryServerErrorBeforePersistence() {
        val row = PendingIngest("scope", "local", "", kind = "discovery")
        assertEquals("This import could not be completed. Try again.", row.discoveryStoredError("SECRET"))
        assertNull(row.discoveryStoredError(null))
        assertEquals("processing", row.discoveryStoredStatus("SECRET"))
        assertEquals("completed", row.discoveryStoredStatus("completed"))
        assertEquals("existing", row.copy(kind = "url").discoveryStoredError("existing"))
    }
    @Test fun viewModelTrackingKeepsDiscoveryContentOutOfRoom() {
        val source = File("src/main/java/org/openlearning/app/LearningViewModel.kt").readText()
        val tracking = source.substringAfter("private suspend fun trackDiscoveryIngest").substringBefore("fun loadNotes")
        assertTrue(tracking.contains("discoverySession !== session"))
        assertTrue(tracking.contains("kind = " + '"' + "discovery" + '"'))
        assertFalse(tracking.contains("item.url")); assertFalse(tracking.contains("item.title"))
        assertTrue(source.contains("private fun clearDiscovery() { discoveryActions.clear() }"))
        assertTrue(source.contains("forgets(scope).none { it.status != FORGET_CONFIRMED }"))
    }
}
