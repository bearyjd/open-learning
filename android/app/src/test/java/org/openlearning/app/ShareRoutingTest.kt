package org.openlearning.app

import org.junit.Assert.*
import org.junit.Test

class ShareRoutingTest {
    @Test fun blankOrNullIsIgnored() {
        assertEquals(ShareRoute.Ignore, routeShare(null))
        assertEquals(ShareRoute.Ignore, routeShare("  \n "))
    }
    @Test fun plainUrlAndUrlWithPageTitleRouteToImport() {
        assertEquals(ShareRoute.Url("https://a.example/x"), routeShare("https://a.example/x"))
        assertEquals(ShareRoute.Url("https://a.example/x"), routeShare("Great article\nhttps://a.example/x"))
        assertEquals(ShareRoute.Url("http://a.example"), routeShare("http://a.example"))
    }
    @Test fun nonUrlTextRoutesToNoteWithTitleFromFirstLine() {
        val text = "\n  My lecture notes  \nsecond line with more words"
        assertEquals(ShareRoute.Note("My lecture notes", text), routeShare(text))
    }
    @Test fun otherSchemesAreNotLinks() {
        val text = "ftp://a.example/file"
        assertTrue(routeShare(text) is ShareRoute.Note)
    }
    @Test fun longTextWithAnIncidentalLinkIsStillANote() {
        val text = "word ".repeat(200) + "see https://a.example"
        assertTrue(routeShare(text) is ShareRoute.Note)
    }
    @Test fun overLimitTextIsKeptVerbatim() {
        val text = "a".repeat(DEFAULT_LIMITS.maxChars + 500)
        val route = routeShare(text) as ShareRoute.Note
        assertEquals(text, route.text)
        assertTrue(text.length > DEFAULT_LIMITS.maxChars)
    }
    @Test fun titleIsCutByCodePointsNotUtf16Units() {
        val title = titleFromText("😀".repeat(100), 5)
        assertEquals(5, title.codePointCount(0, title.length))
        assertEquals("😀".repeat(5), title)
        assertEquals("", titleFromText(" \n\n  ", 10))
    }
    @Test fun titleRespectsTheServerTitleLimit() {
        val route = routeShare("abcdefghij line", effectiveLimits(Limits(maxTitleChars = 4))) as ShareRoute.Note
        assertEquals("abcd", route.title)
        assertEquals(SHARE_TITLE_MAX, (routeShare("x".repeat(200)) as ShareRoute.Note).title.length)
    }
}
