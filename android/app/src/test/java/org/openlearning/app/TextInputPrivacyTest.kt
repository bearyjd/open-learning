package org.openlearning.app

import androidx.compose.ui.text.input.KeyboardType
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Test

class TextInputPrivacyTest {
    private inner class FakeTarget(
        override var importance: Int,
        override val isDialogRoot: Boolean = false,
        override val dialogDecor: AutofillTarget? = null,
        val name: String = "",
    ) : AutofillTarget

    @Test fun dialogChainTargetsDialogLayoutAndDecorOnly() {
        val decor = FakeTarget(0, name = "decor")
        val layout = FakeTarget(0, isDialogRoot = true, dialogDecor = decor)
        val content = FakeTarget(0)
        val picked = autofillTargetsFor(listOf(content, layout, decor))
        assertEquals(listOf<AutofillTarget>(layout, decor), picked)
    }

    @Test fun activityChainTargetsOnlyNearestAncestor() {
        val near = FakeTarget(0)
        val far = FakeTarget(0)
        assertEquals(listOf<AutofillTarget>(near), autofillTargetsFor(listOf(near, far)))
    }

    @Test fun noAncestorFailsLoudly() {
        val e = assertThrows(IllegalStateException::class.java) { autofillTargetsFor(emptyList()) }
        assertEquals(NO_AUTOFILL_ANCESTOR, e.message)
    }

    @Test fun composeViewItselfIsNeverFlaggedWhenOnlyAncestorsAreProvided() {
        val composeView = FakeTarget(1)
        val parent = FakeTarget(0)
        optOutOfAutofill(autofillTargetsFor(listOf(parent)))
        assertEquals(1, composeView.importance)
        assertEquals(TextInputPrivacy.AUTOFILL_MODE, parent.importance)
    }

    @Test fun duplicateDialogTargetsAreCollapsed() {
        val layout = FakeTarget(0, isDialogRoot = true)
        val picked = autofillTargetsFor(listOf(layout))
        assertEquals(1, picked.size)
    }

    @Test fun restoreRunsInReverseOrder() {
        val order = mutableListOf<String>()
        class Rec(val n: String) : AutofillTarget {
            override var importance: Int = 0
                set(v) { field = v; order += "$n=$v" }
        }
        val a = Rec("a"); val b = Rec("b")
        order.clear()
        val restore = optOutOfAutofill(listOf(a, b))
        order.clear()
        restore()
        assertEquals(listOf("b=0", "a=0"), order)
    }

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
