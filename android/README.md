# Open Learning for Android

Native Kotlin / Jetpack Compose client, Android 8.0+ (API 26), targeting API 36. Open `android/` in Android Studio, or run `./gradlew assembleDebug testDebugUnitTest lintDebug` with a full JDK 17+ and Android SDK 36 installed. The build was verified with Temurin 21; a JRE alone is insufficient.

The full Gradle build, four unit tests, and lint pass (zero errors, 15 warnings).
A debug APK was produced and its signing checked. Six instrumentation tests pass
on a physical foldable, covering UI, offline saved reading, account isolation,
and import recovery against controlled HTTP responses; see
[verification and limits](../docs/verification.md#android-device-results).

Connect using your server URL and access token in Connection settings. For an Android emulator and a host backend, use `http://10.0.2.2:8000` and explicitly enable local HTTP. Prefer HTTPS on a real network. The client never follows authenticated redirects.

The five destinations use the real `/api/v1` API: daily insights, saved library, grounded tutor, due recall reviews, and search. Share an article URL from a browser or paste it in Discover. WorkManager persists imports and polls server jobs with network constraints and exponential backoff. Check Recent imports for processing errors and retry failed imports. Refresh the feed when processing finishes.

Room stores source text and explanations in app-private storage. Saved items remain readable when offline. Every cache record is scoped to a SHA-256 digest of the exact normalized server URL and token; switching either never displays the other account's content. Bearer tokens are encrypted with an Android Keystore AES-GCM key, bound to the server URL, and excluded from backup. Tokens never enter WorkManager input data. Background imports for a different account pause until that account is reconnected and the import retried.

Tutor and source content are displayed as plain text. Original source URLs open in your browser. Recall ratings are sent only after the user reveals the explanation. No model keys are stored or requested on the phone.
Source details show coverage and truncation, and generated text is labeled AI
interpretation. Model metadata is retained in cached cards.

The server also exposes `POST /api/v1/ingest/text` for plain text/Markdown notes, but the Android client does not use it yet (client support is the PR-E2 follow-up). Android accepts shared text containing a URL; raw text/Markdown/PDF uploads from the app are not exposed. Network operations other than ingestion require a live connection; failed save/review/chat requests are reported rather than pretending to have synced.

Dependencies are pinned in the Gradle files; the Gradle wrapper is included. The project uses Room, Retrofit/Gson, WorkManager, coroutines and Flow, and manual dependency injection through `AppContainer`.

## Device acceptance tests

The `acceptance` build installs as `org.openlearning.app.acceptance`, separately
from the normal app. Tests use public sample content and a loopback HTTP fixture
server on the device. They exercise Android storage, credentials, networking,
background work, and UI without needing a model or a running backend. These
checks complement the separate [live API workflow](../docs/verification.md#live-model-results).

From `android/`, with an authorized device connected:

```sh
adb devices
./gradlew :app:assembleAcceptance :app:assembleAcceptanceAndroidTest
ANDROID_SERIAL=YOUR_DEVICE_SERIAL ./gradlew :app:connectedAcceptanceAndroidTest
```

Tests reset the acceptance app's own data between cases. They do not change the
phone's network settings; offline checks make only the fixture server unreachable.
Reports are written under `app/build/reports/androidTests/connected/`.
CI compiles both acceptance APKs; connected tests require a device or emulator.

## Opt-in Android UI against the live backend

`LiveLearningLoopTest` is skipped in ordinary connected runs. Its separate runner
uses the production UI, Retrofit, Room, Keystore, and WorkManager with the real
API, worker, PostgreSQL, and model. It pastes a public article, waits for import,
checks an attributed generated insight, saves it, starts tutoring, submits the
learner's explanation, reveals recall, and rates it Remembered. It checks a review
three days ahead, then closes and reopens the Activity to check Library and the
empty due queue. GET requests inspect persisted server state; writes use UI.

Use a **fresh disposable backend database** and a running worker/model, dedicated
to this run. The runner refuses nonempty feed, library, history, or due queues;
this check complements your explicit isolation acknowledgement and does not
prove that every backend table is empty. The runner never resets the backend.
The test resets only the acceptance app's local database and scheduled work.
It makes real model calls and leaves its new learning data in the backend.

From the repository root, after starting that isolated stack and setting its
`OL_API_TOKEN` in your environment (never put the token on a command line):

```sh
(cd android && ./gradlew :app:assembleAcceptance :app:assembleAcceptanceAndroidTest)
adb -s YOUR_DEVICE_SERIAL install -r android/app/build/outputs/apk/acceptance/app-acceptance.apk
adb -s YOUR_DEVICE_SERIAL install -r android/app/build/outputs/apk/androidTest/acceptance/app-acceptance-androidTest.apk
OL_LIVE_ISOLATED=1 OL_SERVER_URL=http://127.0.0.1:18000 \
  python3 scripts/android_live.py --allow-live --serial YOUR_DEVICE_SERIAL
```

`OL_SMOKE_URL` optionally selects another public HTTPS article; the default is
`https://docs.python.org/3/tutorial/introduction.html`. The host API must listen
on localhost at the selected dedicated port. The runner creates an adb reverse
mapping without replacing an existing one and removes only its own mapping.
It sends configuration and credentials through stdin into a private file in the
acceptance app sandbox, reads it once, and removes that file on teardown. Tokens
are excluded from adb arguments and child process environments. The app retains
its acceptance-only encrypted connection and saved data for inspection.

A nonzero exit means a prerequisite, test, or cleanup failed, including when adb
itself returns zero for a failed or skipped test. The private device report is
`.omc/android-live-instrumentation.log` (mode 0600, ignored by Git, token redacted).
The runner neither installs APKs nor retries failed model generations. A new
attempt needs a new isolated backend. Do not run the fixture suite concurrently
with this test, because both intentionally reset the acceptance sandbox.

These checks establish integration and visible/persisted state, not semantic
model correctness. Activity and ViewModel relaunch stays in the same app process;
it does not cover process death, reboot, offline reviews, or fold transitions.
The due queue and UI timestamp establish future scheduling; exact persisted
`interval_days` needs a separate read-only database check because the API does
not list future reviews. Record actual results in
[verification](../docs/verification.md#android-device-results).
