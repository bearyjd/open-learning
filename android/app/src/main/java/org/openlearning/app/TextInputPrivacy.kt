package org.openlearning.app

import android.view.View
import android.view.inputmethod.EditorInfo
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.ui.ExperimentalComposeUiApi
import androidx.compose.ui.platform.LocalAutofillManager
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.platform.PlatformTextInputInterceptor
import androidx.compose.ui.platform.PlatformTextInputMethodRequest
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.window.DialogWindowProvider

/** Shared decisions for screens whose text must not reach autofill services or keyboard learning. */
internal object TextInputPrivacy {
    /** Hides the view and its descendants from autofill, so no fill request and no "save" prompt is raised. */
    const val AUTOFILL_MODE = View.IMPORTANT_FOR_AUTOFILL_NO_EXCLUDE_DESCENDANTS

    val noLearningKeyboard = KeyboardOptions(autoCorrectEnabled = false)

    fun credentialKeyboard(type: KeyboardType) = KeyboardOptions(keyboardType = type, autoCorrectEnabled = false)
}

/** Asks the keyboard not to learn from what is typed here (EditorInfo.IME_FLAG_NO_PERSONALIZED_LEARNING). */
@OptIn(ExperimentalComposeUiApi::class)
internal val NoPersonalizedLearning = PlatformTextInputInterceptor { request, nextHandler ->
    nextHandler.startInputMethod(object : PlatformTextInputMethodRequest {
        override fun createInputConnection(outAttrs: EditorInfo) = request.createInputConnection(outAttrs).also {
            outAttrs.imeOptions = outAttrs.imeOptions or EditorInfo.IME_FLAG_NO_PERSONALIZED_LEARNING
        }
    })
}

/** Something whose autofill importance can be read and set; a [View] in production, a fake in JVM tests. */
internal interface AutofillTarget {
    var importance: Int
}

private class ViewAutofillTarget(private val view: View) : AutofillTarget {
    override var importance: Int
        get() = view.importantForAutofill
        set(value) { view.importantForAutofill = value }
}

/**
 * Sets [TextInputPrivacy.AUTOFILL_MODE] on each distinct target and returns a function that puts every
 * previous value back. Restoring runs in reverse order so a target listed twice still ends up original.
 */
internal fun optOutOfAutofill(targets: List<AutofillTarget>): () -> Unit {
    val previous = targets.map { it.importance }
    targets.forEach { it.importance = TextInputPrivacy.AUTOFILL_MODE }
    return { targets.indices.reversed().forEach { targets[it].importance = previous[it] } }
}

/** Excludes the host view (the activity content) from autofill while composed; restores the prior value. */
@Composable
internal fun ExcludeFromAutofill() {
    val view = LocalView.current
    DisposableEffect(view) {
        val restore = optOutOfAutofill(listOf(ViewAutofillTarget(view)))
        onDispose(restore)
    }
}

/** The views a Compose Dialog's content lives in: its own DialogLayout and the dialog window's decor view. */
internal fun dialogAutofillViews(host: View): List<View> =
    listOfNotNull(host, (host as? DialogWindowProvider)?.window?.decorView).distinct()

/**
 * For use INSIDE dialog content, where [LocalView] is the dialog's DialogLayout rather than the activity's
 * ComposeView. Excludes the DialogLayout and the dialog window's decor view from autofill, and on dispose
 * restores both and asks the autofill framework to drop any pending (unsaved) context.
 */
@Composable
internal fun ExcludeDialogFromAutofill() {
    val view = LocalView.current
    val autofillManager = LocalAutofillManager.current
    DisposableEffect(view) {
        val restore = optOutOfAutofill(dialogAutofillViews(view).map(::ViewAutofillTarget))
        onDispose {
            restore()
            autofillManager?.cancel()
        }
    }
}
