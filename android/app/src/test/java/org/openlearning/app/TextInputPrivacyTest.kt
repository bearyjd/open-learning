package org.openlearning.app

import androidx.compose.ui.text.input.KeyboardType
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test

class TextInputPrivacyTest {
    private class FakeTarget(override var importance: Int) : AutofillTarget

    @Test fun optOutSetsExcludeModeOnEveryTargetAndRestoresEachPreviousValue() {
        val a = FakeTarget(1)
        val b = FakeTarget(4)
        val restore = optOutOfAutofill(listOf(a, b))
        assertEquals(TextInputPrivacy.AUTOFILL_MODE, a.importance)
        assertEquals(TextInputPrivacy.AUTOFILL_MODE, b.importance)
        restore()
        assertEquals(1, a.importance)
        assertEquals(4, b.importance)
    }

    @Test fun optOutRestoresOriginalEvenWhenTargetIsListedTwice() {
        val a = FakeTarget(2)
        optOutOfAutofill(listOf(a, a))()
        assertEquals(2, a.importance)
    }

    @Test fun optOutWithNoTargetsIsANoOp() {
        optOutOfAutofill(emptyList())()
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
