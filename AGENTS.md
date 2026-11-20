# AndroidAgent agent guide

## Build requirements

This project calls Android hidden APIs, including `ActivityThread`, `UiAutomationConnection`, and `DisplayManagerGlobal`. The stock `android.jar` from `sdkmanager` strips those APIs and fails compilation.

Before running `build.bat` or `gradlew`, replace the SDK's `android.jar` with the unstripped build from `https://github.com/Reginer/aosp-android-jar`.

1. Ensure `sdk.dir` is set in `local.properties` or `ANDROID_HOME` points to your Android SDK directory.
2. Download `android-36/android.jar` from `Reginer/aosp-android-jar` and overwrite `$ANDROID_HOME/platforms/android-36/android.jar`.

```sh
curl -fL --retry 3 -o "$ANDROID_HOME/platforms/android-36/android.jar" \
  "https://raw.githubusercontent.com/Reginer/aosp-android-jar/main/android-36/android.jar"
```

3. Build the release JAR with `build.bat` (or `build.bat debug` for an unminified build).

The release output is written to `build/dist/agent-server.jar`. When `../tools/` exists, `build.bat` also copies the JAR to `../tools/agent-server.jar`.

## Project constraints

- `build.gradle.kts` targets `minSdk = 29` and `compileSdk = 36` with Java 11 and zero external dependencies. Keep `dependencies {}` empty so the artifact stays a small standalone DEX JAR for `app_process`.
- `Server.kt` runs under `shell` uid (2000) with no installed APK and no root requirement.
- `SimpleHttpServer.kt` binds to `127.0.0.1` only. Reach it from the host through `adb forward`.
- `UiAutomation` is a per-device singleton. Another `UiAutomation` client on the same device fails to connect while this server is active.
- Hidden APIs that compile against `android-36/android.jar` can still crash with `NoSuchMethodError` or `NoClassDefFoundError` on Android 10 to 17. Guard version-sensitive private calls or prefer public APIs when available.
