package org.openlearning.app

import android.view.View
import android.view.inputmethod.EditorInfo
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.ui.ExperimentalComposeUiApi
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.platform.PlatformTextInputInterceptor
import androidx.compose.ui.platform.PlatformTextInputMethodRequest
import androidx.compose.ui.text.input.KeyboardType

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

/** Excludes the host view (the dialog or activity content) from autofill while composed; restores the prior value. */
@Composable
internal fun ExcludeFromAutofill() {
    val view = LocalView.current
    DisposableEffect(view) {
        val previous = view.importantForAutofill
        view.importantForAutofill = TextInputPrivacy.AUTOFILL_MODE
        onDispose { view.importantForAutofill = previous }
    }
}
