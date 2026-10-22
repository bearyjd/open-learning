package org.openlearning.app

import android.view.Window
import android.view.WindowManager
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.ui.platform.LocalView
import java.util.WeakHashMap

/** Counts nested users of FLAG_SECURE: the flag is set by the first and cleared only when the last one leaves. */
class SecureRefCount {
    var count = 0; private set
    /** True when the caller is the first user and must set the flag. */
    fun acquire(): Boolean { count += 1; return count == 1 }
    /** True when the caller was the last user and must clear the flag. An unmatched release does nothing. */
    fun release(): Boolean { if (count == 0) return false; count -= 1; return count == 0 }
}

private val counts = WeakHashMap<Window, SecureRefCount>()

/**
 * FLAG_SECURE while composed (recents thumbnail and screenshots), reference-counted per window so overlapping users
 * (editor and list, or a recomposition that disposes after the next screen composes) never clear each other's flag.
 * Main thread only. Neither screen needs more than that: the list has no inputs, the editor excludes autofill itself.
 */
@Composable
internal fun SecureWindow() {
    val view = LocalView.current
    DisposableEffect(view) {
        val window = view.context.activity()?.window
        val users = window?.let { counts.getOrPut(it) { SecureRefCount() } }
        if (users?.acquire() == true) window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        onDispose { if (users?.release() == true) window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE) }
    }
}
