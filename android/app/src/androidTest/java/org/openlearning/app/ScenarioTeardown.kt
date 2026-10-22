package org.openlearning.app

import android.os.SystemClock
import android.util.Log
import androidx.test.core.app.ActivityScenario
import androidx.lifecycle.Lifecycle

/**
 * Closes a scenario whose activity may have replaced its launch intent (MainActivity calls setIntent after consuming a share).
 * ActivityScenario matches lifecycle callbacks against the activity it launched, so close() can then wait for a DESTROYED
 * that it never recognises (hypothesis, not verified on a device). Finish the activity ourselves, wait a bounded time for
 * DESTROYED, and only then close, so a stuck teardown is logged instead of failing @After and cascading into later tests.
 */
internal fun ActivityScenario<*>.closeAfterFinish(timeoutMs: Long = 10_000) {
    runCatching { onActivity { it.finish() } }
        .onFailure { Log.w("ScenarioTeardown", "finish() failed: $it") }
    val deadline = SystemClock.elapsedRealtime() + timeoutMs
    while (state != Lifecycle.State.DESTROYED && SystemClock.elapsedRealtime() < deadline) SystemClock.sleep(50)
    if (state != Lifecycle.State.DESTROYED) Log.w("ScenarioTeardown", "activity still $state after ${timeoutMs}ms")
    runCatching { close() }.onFailure { Log.w("ScenarioTeardown", "close() failed: $it") }
}
