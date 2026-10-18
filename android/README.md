# Open Learning for Android

Native Kotlin / Jetpack Compose client, Android 8.0+ (API 26), targeting API 36. Open `android/` in Android Studio, or run `./gradlew assembleDebug testDebugUnitTest lintDebug` with a full JDK 17+ and Android SDK 36 installed. The build was verified with Temurin 21; a JRE alone is insufficient.

The full Gradle build, four unit tests, and lint pass (zero errors, 14 warnings).
A debug APK was produced and its signing checked. Device UI and offline behavior
remain unverified; see [verification](../docs/verification.md).

Connect using your server URL and access token in Connection settings. For an Android emulator and a host backend, use `http://10.0.2.2:8000` and explicitly enable local HTTP. Prefer HTTPS on a real network. The client never follows authenticated redirects.

The five destinations use the real `/api/v1` API: daily insights, saved library, grounded tutor, due recall reviews, and search. Share an article URL from a browser or paste it in Discover. WorkManager persists imports and polls server jobs with network constraints and exponential backoff. Check Recent imports for processing errors and retry failed imports. Refresh the feed when processing finishes.

Room stores source text and explanations in app-private storage. Saved items remain readable when offline. Every cache record is scoped to a SHA-256 digest of the exact normalized server URL and token; switching either never displays the other account's content. Bearer tokens are encrypted with an Android Keystore AES-GCM key, bound to the server URL, and excluded from backup. Tokens never enter WorkManager input data. Background imports for a different account pause until that account is reconnected and the import retried.

Tutor and source content are displayed as plain text. Original source URLs open in your browser. Recall ratings are sent only after the user reveals the explanation. No model keys are stored or requested on the phone.
Source details include coverage, truncation, and model labels so partial material
can be distinguished when reading an insight.

The current server contract accepts article URLs only. Android accepts shared text containing a URL; raw text/Markdown/PDF uploads are not exposed until a matching backend ingestion endpoint exists. Network operations other than ingestion require a live connection; failed save/review/chat requests are reported rather than pretending to have synced.

Dependencies are pinned in the Gradle files; the Gradle wrapper is included. The project uses Room, Retrofit/Gson, WorkManager, coroutines and Flow, and manual dependency injection through `AppContainer`.
