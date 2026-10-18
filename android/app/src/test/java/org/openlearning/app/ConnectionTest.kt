package org.openlearning.app

import org.junit.Assert.*
import org.junit.Test

class ConnectionTest {
    @Test fun differentServerOrAccountNeverSharesACache() {
        val first = Connection("https://one.example/", "first-account").scope
        assertNotEquals(first, Connection("https://two.example/", "first-account").scope)
        assertNotEquals(first, Connection("https://one.example/", "second-account").scope)
        assertEquals(first, Connection("https://one.example/", "first-account").scope)
        assertFalse(first.contains("first-account"))
    }
    @Test fun serverAddressRequiresExplicitHttpConsent() {
        assertEquals("https://example.com/", normalizeServer(" https://example.com ", false))
        assertEquals("http://10.0.2.2:8000/", normalizeServer("http://10.0.2.2:8000", true))
        assertThrows(IllegalArgumentException::class.java) { normalizeServer("http://example.com", false) }
        assertThrows(IllegalArgumentException::class.java) { normalizeServer("https://token@example.com", false) }
        assertThrows(IllegalArgumentException::class.java) { normalizeServer("https://example.com?token=secret", false) }
        assertThrows(IllegalArgumentException::class.java) { normalizeServer("file:///etc/passwd", true) }
    }
    @Test fun browserShareExtractsAnArticleLink() {
        assertEquals("https://example.com/article", sharedUrl("Interesting read: https://example.com/article"))
        assertNull(sharedUrl("An article without a link"))
    }
}
