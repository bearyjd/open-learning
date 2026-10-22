package org.openlearning.app

import android.view.View
import androidx.compose.ui.InternalComposeUiApi
import androidx.compose.ui.platform.ViewRootForTest
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.compose.ui.window.DialogWindowProvider
import androidx.test.core.app.ActivityScenario
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Compiled in CI; run on the acceptance app later (NOT device-verified).
 *
 * Asserts, for the open connect dialog, that the dialog's own views carry NO_EXCLUDE_DESCENDANTS:
 * the DialogLayout that hosts the "Access token" field's ComposeView (the DialogWindowProvider ancestor,
 * NOT the activity's ComposeView or any other ancestor) and that dialog window's decor view. It does not
 * assert that an autofill service actually stays silent; that needs a real password manager on a device.
 */
@RunWith(AndroidJUnit4::class)
class ConnectAutofillTest {
    @get:Rule val compose = createEmptyComposeRule()
    private val app: LearningApplication get() = ApplicationProvider.getApplicationContext()

    // ViewRootForTest is the only way to reach the Android View that hosts a semantics node; it is internal API
    // (ViewRootForTest + InternalComposeUiApi) and may change between Compose releases.
    @OptIn(InternalComposeUiApi::class)
    @Test fun connectDialogWindowIsExcludedFromAutofill() {
        check(app.packageName == "org.openlearning.app.acceptance") { "Refusing to touch a non-acceptance app" }
        app.container.credentials.save("https://learning.example.com/", "alpha", false)
        ActivityScenario.launch(MainActivity::class.java).use {
            compose.onNodeWithContentDescription("Connection settings").performClick()
            compose.waitUntil(5_000) { compose.onAllNodesWithText("Access token").fetchSemanticsNodes().isNotEmpty() }
            val host = (compose.onNodeWithText("Access token").fetchSemanticsNode().root as ViewRootForTest).view
            val dialog = generateSequence(host as View?) { it.parent as? View }.filterIsInstance<DialogWindowProvider>().firstOrNull()
            assertNotNull("token field must be hosted by a dialog window, not the activity", dialog)
            val dialogLayout = dialog as View
            assertEquals("DialogLayout", View.IMPORTANT_FOR_AUTOFILL_NO_EXCLUDE_DESCENDANTS, dialogLayout.importantForAutofill)
            assertEquals("dialog decor view", View.IMPORTANT_FOR_AUTOFILL_NO_EXCLUDE_DESCENDANTS, dialog.window.decorView.importantForAutofill)
        }
    }
}
