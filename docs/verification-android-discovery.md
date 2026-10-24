# Android discovery verification

The 2026-10-23 Android discovery slice implements the Reading queue, Feeds and
Topics screens from the [design](superpowers/specs/2026-10-23-android-discovery-design.md).
This record is separate from the historical notes-device runs and the
[backend discovery checks](verification-discovery.md). No backend code, Room
schema, dependency or model behavior changes are part of this slice.

## Host verification

The new Python acceptance helpers were written first. The initial unittest run
failed with `ModuleNotFoundError: android_discovery`; after implementation,
`python3 -m unittest discover -s scripts/tests -p test_android_discovery.py`
passed all nine tests. These checks invoke the mock's pure request dispatcher;
they never start adb, a device, or a live backend. They cover newest/relevance
fixtures, pagination, one stale-cursor 409, ingest/dismiss, add/duplicate/remove,
refresh 429 and Retry-After, pin/unpin, isolated empty credential scope,
digest-only evidence, unknown-path redaction, secret-free adb/overlay failures
and complete verdict requirements.

Final host verification on 2026-10-23 used Java 21, the installed Android SDK,
and `GRADLE_USER_HOME=/tmp/openlearning-gradle`:

```sh
cd android
JAVA_HOME=/usr/lib/jvm/java-21 ANDROID_HOME="$HOME/Android/Sdk" \
  GRADLE_USER_HOME=/tmp/openlearning-gradle \
  ./gradlew --no-daemon testDebugUnitTest :app:assembleAcceptanceAndroidTest lintDebug
```

The exact gate passed: **401 JVM tests, zero failures/errors/skips**, including
20 discovery tests; the acceptance androidTest APK assembled; lint reported
zero errors and 16 warnings in existing dependency, manifest and credential
code. The five new instrumented tests were compiled, never executed. Final
`python3 -m unittest discover -s scripts/tests` passed **61 tests**, including
the nine discovery helpers. Existing HTTP mock tests needed loopback socket
access, so the full host suites ran outside the socket-restricted sandbox.
No adb, emulator, device or instrumented-test command ran.

JVM tests were authored before the new logic. Initial compilation failed on
missing implementation symbols; the first runnable selection passed its logic
tests and failed four discovery-job helper tests before those helpers were
implemented. Later regressions cover atomic clear during publication, scope
A-to-B-to-A changes, pending forget privacy, refresh failures, feed deletion,
accepted ingest after screen close, and Retry-After across subsection changes.
This is host logic evidence, not a claim that every regression was observed
failing at runtime before its fix.

The self-adversarial pass and independent source review addressed stale-response
resurrection after background forget callbacks, refetching topics while an
offline forget still waits for server deletion, private terms in lazy-list
saveable keys, raw discovery-job errors/statuses, lost ingest tracking after
leaving the queue, and cooldown bypass through subsection navigation. The
acceptance runner also closes the whole discovery route before reusing dismissed
fixture IDs. The final review reported no remaining actionable source findings.
`git diff --check` passed; all changed files remain under 800 lines. Backend,
Room schemas, dependencies and network permissions are unchanged.

## Prepared device acceptance (not run)

`scripts/android_discovery.py` starts a loopback mock with an owned adb reverse,
clears only `org.openlearning.app.acceptance`, and drives the installed acceptance
APK. It reuses the notes runner's foreground/overlay checks, input pacing,
allowlisted adb text, and bounded visible scrolling. It stops input on an
unexpected overlay. It creates no screenshots, logcat capture or model calls.

The prepared scenarios cover newest and relevance selection, a second queue
page, ranking-change 409 restart, ingest and dismiss, feed add, refresh 429 and
retry, remove Cancel/Confirm, topic pin/unpin, and a credential change that must
clear queue, feeds, derived terms and unsent topic input. The mock records only
method, allowlisted query-free route, status, body length and SHA-256; it never
stores request bodies, credentials or topic query values in its request log.
The private XML evidence can include visible field contents and belongs only in
the git-ignored `.omc/discovery` directory (or an explicitly chosen private
`--out` directory). Console output contains only the fixed final verdict.

For a user-supervised run after installing the acceptance APK:

```sh
python3 scripts/android_discovery.py --serial SERIAL
```

This command was **not executed** during implementation. Its UI sequencing and
mock HTTP transport remain device-unverified, even though the pure helpers pass.

## Still unverified

- Every discovery instrumented test and every adb acceptance scenario on a
  physical device or emulator, including queue job handoff and Retry-After timing.
- Runtime FLAG_SECURE/recents behavior, autofill suppression, keyboard learning
  opt-out, backups, accessibility/TalkBack, 48dp target usability and pull refresh.
- Configuration changes, process death, in-flight scope changes, offline errors
  and forget clearing on a device. JVM tests provide logic evidence only.
- Android interaction with a live discovery backend, real feed providers,
  browser intents, actual article extraction/model completion and sustained
  production scheduling. The mock performs none of these operations.
