package org.openlearning.app

import androidx.compose.ui.text.input.KeyboardType
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test

class TextInputPrivacyTest {
    @Test fun autofillModeExcludesHostAndDescendants() {
        // View.IMPORTANT_FOR_AUTOFILL_NO_EXCLUDE_DESCENDANTS == 8
        assertEquals(8, TextInputPrivacy.AUTOFILL_MODE)
    }

    @Test fun credentialKeyboardKeepsRequestedTypeAndDisablesAutoCorrect() {
        val token = TextInputPrivacy.credentialKeyboard(KeyboardType.Password)
        val server = TextInputPrivacy.credentialKeyboard(KeyboardType.Uri)
        assertEquals(KeyboardType.Password, token.keyboardType)
        assertEquals(KeyboardType.Uri, server.keyboardType)
        assertFalse(token.autoCorrectEnabled == true)
        assertFalse(server.autoCorrectEnabled == true)
    }

    @Test fun noteKeyboardDisablesAutoCorrect() {
        assertFalse(TextInputPrivacy.noLearningKeyboard.autoCorrectEnabled == true)
    }
}
