package org.openlearning.app

import android.view.View
import android.view.ViewGroup
import androidx.compose.ui.InternalComposeUiApi
import androidx.compose.ui.platform.ViewRootForTest
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.test.core.app.ActivityScenario
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/** The connect dialog must hide the server URL and token from autofill. Compiled in CI; run on the acceptance app later. */
@RunWith(AndroidJUnit4::class)
class ConnectAutofillTest {
    @get:Rule val compose = createEmptyComposeRule()
    private val app: LearningApplication get() = ApplicationProvider.getApplicationContext()

    @OptIn(InternalComposeUiApi::class)
    @Test fun connectDialogExcludesDescendantsFromAutofill() {
        check(app.packageName == "org.openlearning.app.acceptance") { "Refusing to touch a non-acceptance app" }
        app.container.credentials.save("https://learning.example.com/", "alpha", false)
        ActivityScenario.launch(MainActivity::class.java).use {
            compose.onNodeWithContentDescription("Connection settings").performClick()
            compose.waitUntil(5_000) { compose.onAllNodesWithText("Access token").fetchSemanticsNodes().isNotEmpty() }
            val field = compose.onNodeWithText("Access token").fetchSemanticsNode().root as ViewRootForTest
            var excluded = false
            var node: View? = field.view
            while (node != null) {
                if (node.importantForAutofill == View.IMPORTANT_FOR_AUTOFILL_NO_EXCLUDE_DESCENDANTS) excluded = true
                node = (node.parent as? ViewGroup)
            }
            assertTrue("connect dialog must be excluded from autofill", excluded)
        }
    }
}
