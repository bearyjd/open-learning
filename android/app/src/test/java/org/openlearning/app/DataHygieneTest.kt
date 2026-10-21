package org.openlearning.app

import org.junit.Assert.*
import org.junit.Test
import java.io.File
import javax.xml.parsers.DocumentBuilderFactory

class DataHygieneTest {
    private fun module(path: String) = File(path).takeIf { it.exists() } ?: File("app/$path")

    @Test fun checkpointOnlySucceedsWhenNotBusy() {
        assertTrue(checkpointSucceeded(0))
        assertFalse(checkpointSucceeded(1))
        assertFalse(checkpointSucceeded(null))
    }
    @Test fun completedStatesMatchCaseInsensitively() {
        assertTrue(isCompletedState("Completed")); assertTrue(isCompletedState("done"))
        assertFalse(isCompletedState("failed")); assertFalse(isCompletedState("cancelled"))
    }
    @Test fun manifestKeepsBackupOffAndPointsAtExtractionRules() {
        val manifest = module("src/main/AndroidManifest.xml").readText()
        assertTrue(manifest.contains("""android:allowBackup="false""""))
        assertTrue(manifest.contains("""android:dataExtractionRules="@xml/data_extraction_rules""""))
    }
    @Test fun extractionRulesExcludeDatabaseAndSharedPrefsForBothFlows() {
        val doc = DocumentBuilderFactory.newInstance().newDocumentBuilder().parse(module("src/main/res/xml/data_extraction_rules.xml"))
        for (flow in listOf("cloud-backup", "device-transfer")) {
            val node = doc.getElementsByTagName(flow).item(0)
            assertNotNull(flow, node)
            val excludes = (0 until node.childNodes.length).map { node.childNodes.item(it) }.filter { it.nodeName == "exclude" }
                .map { it.attributes.getNamedItem("domain").nodeValue }
            assertTrue("$flow: $excludes", excludes.containsAll(listOf("database", "sharedpref")))
            assertTrue(flow, (0 until node.childNodes.length).none { node.childNodes.item(it).nodeName == "include" })
        }
    }
}
