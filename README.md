# AndroidAgent

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/release/TheDeathDragon/AndroidAgent)](https://github.com/TheDeathDragon/AndroidAgent/releases)

[English](README.md) | [简体中文](README.zh.md)

On-device HTTP server for Android automation. Runs via `app_process`, no APK
install, no permissions beyond `shell` uid.

## Features

- `/screenshot` via `UiAutomation.takeScreenshot()` — ~3× faster than `screencap`
- `/hierarchy` via `AccessibilityNodeInfo` walk — ~10× faster than `uiautomator dump`
- `/packages`, `/icon`, `/signature`, `/device-info` from `PackageManager` + `dumpsys`
- Binds 127.0.0.1 only; reachable through `adb forward`
- Zero external dependencies, ~92 KB after R8

## Build

Requires Android SDK + JDK 11. Set `sdk.dir=` in `local.properties` or export
`ANDROID_HOME`.

The agent calls hidden APIs (`ActivityThread`, `UiAutomationConnection`,
`DisplayManagerGlobal`). The stock `android.jar` shipped by `sdkmanager`
strips them, so compilation requires an unstripped replacement at
`$ANDROID_HOME/platforms/android-36/android.jar`. Drop in
[Reginer/aosp-android-jar](https://github.com/Reginer/aosp-android-jar)'s
`android-36/android.jar`. CI does this automatically via `curl`.

```
build.bat            # release (default)
build.bat debug
```

Output:

- `build/dist/agent-server.jar` — canonical
- `../tools/agent-server.jar` — also written when the parent directory has a
  `tools/` folder (so host tools that vendor this as a submodule pick it up)

## Run

```
adb push build/dist/agent-server.jar /data/local/tmp/agent-server.jar
adb shell CLASSPATH=/data/local/tmp/agent-server.jar app_process / la.shiro.agent.Server --port 9500
adb forward tcp:9500 tcp:9500
curl http://127.0.0.1:9500/health
```

## Endpoints

| Method | Path                          | Returns                              |
|--------|-------------------------------|--------------------------------------|
| GET    | `/health`                     | text `ok`                            |
| GET    | `/hierarchy?compressed=true`  | `application/xml`                    |
| GET    | `/screenshot?quality=100`     | `image/png`                          |
| GET    | `/packages?user_only=false`   | JSON array                           |
| GET    | `/icon?pkg=<name>`            | `image/png`                          |
| GET    | `/signature?pkg=<name>`       | text — V1/V2/V3/V4 schemes           |
| GET    | `/device-info`                | JSON — 14-section profile            |
| GET    | `/info`                       | JSON — brand/model/sdk/version       |
| GET    | `/stop`                       | text `stopping` — clean shutdown     |

## Notes

- `UiAutomation` is a per-device singleton. A second agent on the same device
  fails with `UiAutomationService ... already registered`.
- Loopback-only by design; `adb forward` tunnels through adbd which connects
  on-device via 127.0.0.1.

## License

[Apache 2.0](LICENSE)
