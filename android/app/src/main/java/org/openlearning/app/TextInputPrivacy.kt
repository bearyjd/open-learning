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

/**
 * Something whose autofill importance can be read and set; a [View] in production, a fake in JVM tests.
 * [isDialogRoot] marks a DialogWindowProvider (Compose's DialogLayout); [dialogDecor] is then its window's decor view.
 */
internal interface AutofillTarget {
    var importance: Int
    val isDialogRoot: Boolean get() = false
    val dialogDecor: AutofillTarget? get() = null
}

private data class ViewAutofillTarget(private val view: View) : AutofillTarget {
    override var importance: Int
        get() = view.importantForAutofill
        set(value) { view.importantForAutofill = value }
    override val isDialogRoot: Boolean get() = view is DialogWindowProvider
    override val dialogDecor: AutofillTarget?
        get() = (view as? DialogWindowProvider)?.window?.decorView?.let(::ViewAutofillTarget)
}

internal const val NO_AUTOFILL_ANCESTOR = "No ancestor view found to exclude from autofill; refusing to continue unprotected"

/**
 * Picks what to flag from the ANCESTORS of the compose view, nearest first (the compose view itself must never be
 * passed in: AndroidComposeView reports IMPORTANT_FOR_AUTOFILL_YES, so a flag on it is ignored).
 * Dialog: the DialogLayout and its window decor view. Otherwise: only the nearest ancestor. None: fails loudly.
 */
internal fun autofillTargetsFor(ancestors: List<AutofillTarget>): List<AutofillTarget> {
    val dialog = ancestors.firstOrNull { it.isDialogRoot }
    if (dialog != null) return listOfNotNull(dialog, dialog.dialogDecor).distinct()
    return listOf(ancestors.firstOrNull() ?: error(NO_AUTOFILL_ANCESTOR))
}

/** The parent chain of [view], nearest first, excluding [view] itself. */
internal fun ancestorTargets(view: View): List<AutofillTarget> =
    generateSequence(view.parent) { it.parent }.filterIsInstance<View>().map(::ViewAutofillTarget).toList()

/**
 * Sets [TextInputPrivacy.AUTOFILL_MODE] on each distinct target and returns a function that puts every
 * previous value back. Restoring runs in reverse order so a target listed twice still ends up original.
 */
internal fun optOutOfAutofill(targets: List<AutofillTarget>): () -> Unit {
    val previous = targets.map { it.importance }
    targets.forEach { it.importance = TextInputPrivacy.AUTOFILL_MODE }
    return { targets.indices.reversed().forEach { targets[it].importance = previous[it] } }
}

/**
 * Excludes the ancestors of the current compose view from autofill while composed (works in the activity and
 * inside a Dialog); restores every previous value on dispose. Throws if there is no ancestor to flag.
 */
@Composable
internal fun ExcludeFromAutofill() {
    val view = LocalView.current
    DisposableEffect(view) {
        val restore = optOutOfAutofill(autofillTargetsFor(ancestorTargets(view)))
        onDispose(restore)
    }
}

/** [ExcludeFromAutofill] for dialog content; on dispose also asks the framework to drop any pending autofill context. */
@Composable
internal fun ExcludeDialogFromAutofill() {
    val view = LocalView.current
    val autofillManager = LocalAutofillManager.current
    DisposableEffect(view) {
        val restore = optOutOfAutofill(autofillTargetsFor(ancestorTargets(view)))
        onDispose {
            restore()
            autofillManager?.cancel()
        }
    }
}
