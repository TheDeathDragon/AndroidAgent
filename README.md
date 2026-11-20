# AndroidAgent

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/release/TheDeathDragon/AndroidAgent)](https://github.com/TheDeathDragon/AndroidAgent/releases)

[English](README.md) | [简体中文](README.zh.md)

On-device HTTP server for Android automation. Runs via `app_process`, no APK
install, no permissions beyond `shell` uid.

## Features

- `/screenshot` via `UiAutomation.takeScreenshot()` or persistent `VirtualDisplay` mirror (`fast=true`) with PNG, JPEG, and raw RGBA output, downscaling, region cropping (`crop`), and per-frame encoding cache
- `/input/*` low-latency touch, swipe, key, global action, and batched gesture injection via direct `InputManager` fast path with `UiAutomation.injectInputEvent()` fallback
- `/hierarchy` via `AccessibilityNodeInfo` walk (~10x faster than `uiautomator dump`)
- `/packages`, `/icon`, `/signature`, `/device-info` from `PackageManager` + `dumpsys`
- HTTP/1.1 Keep-Alive with loopback-only binding (`127.0.0.1`), reachable through `adb forward`
- Zero external dependencies, standalone DEX JAR for `app_process`

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

All endpoints support HTTP/1.1 Keep-Alive (up to 1000 requests per connection, 15s idle timeout).

| Method   | Path                          | Returns / Notes                                                   |
|----------|-------------------------------|-------------------------------------------------------------------|
| GET      | `/health`                     | text `ok`                                                         |
| GET      | `/hierarchy?compressed=true`  | `application/xml`                                                 |
| GET      | `/screenshot`                 | `image/png`, `image/jpeg`, or `application/octet-stream` (RGBA)   |
| GET/POST | `/input/tap`                  | JSON `{"ok":true}` (params or JSON: `x`, `y`, `durationMs`, `sync`) |
| GET/POST | `/input/swipe`                | JSON `{"ok":true}` (params or JSON: `x1`, `y1`, `x2`, `y2`, `durationMs`, `steps`, `holdMs`, `sync`) |
| GET/POST | `/input/key`                  | JSON `{"ok":true}` (params or JSON: `keyCode`/`keyCodes`, `metaState`, `intervalMs`, `sync`) |
| GET/POST | `/input/global`               | JSON `{"ok":true}` (`action`: `back`, `home`, `recents`, `notifications`, `quick_settings`, `power_dialog`, `lock_screen`, `screenshot`, or numeric id) |
| POST     | `/input/batch`                | JSON `{"ok":true}` (JSON body `{"actions":[...],"sync":true}` or `[...]`; types: `down`, `move`, `up`, `tap`, `swipe`, `wait`, `key`, `global`) |
| GET      | `/packages?user_only=false`   | JSON array                                                        |
| GET      | `/icon?pkg=<name>`            | `image/png`                                                       |
| GET      | `/signature?pkg=<name>`       | text (V1/V2/V3/V4 schemes)                                        |
| GET      | `/device-info`                | JSON (14-section profile)                                         |
| GET      | `/info`                       | JSON (`brand`/`model`/`sdk`/`version`)                            |
| GET      | `/stop`                       | text `stopping` (clean shutdown)                                  |

### `/screenshot` parameters and headers

- `format`: `png` (default, quality 100), `jpeg`/`jpg` (default quality 80), or `rgba` (raw tightly packed `RGBA_8888` bytes)
- `quality`: integer `1..100`
- `scale`: float `0.1..1.0` (default `1.0`)
- `crop`: `x,y,w,h` integers in scaled image coordinates (`x >= 0`, `y >= 0`, `w > 0`, `h > 0`), clamped to image bounds
- `fast`: `true` or `false` (default `false`); uses a persistent `VirtualDisplay` mirror when supported and falls back to `UiAutomation.takeScreenshot()` otherwise
- Response headers: `X-Width`, `X-Height`, `X-Format`, `X-Capture-Source` (`virtual-display` or `uiautomation`), `X-Cache-Hit` (`true` when `fast=true` serves an unchanged encoded frame)

### `/input/*` headers

- Response body is always `{"ok":true}` or `{"ok":false}`
- Response header: `X-Input-Source` (`direct` when injected via `InputManager` / `InputManagerGlobal`, or `uiautomation` on fallback or `/input/global`)

## AXI CLI & Agent Skill

`cli/` ships a zero-dependency Python 3.10+ CLI (`android-agent`) that wraps every AndroidAgent endpoint with TOON output, persistent HTTP/1.1 Keep-Alive, strict validation, and idempotent lifecycle commands (`start`, `stop`, `screen`, `ui`, `tap`, `swipe`, `key`, `global`, `batch`, `packages`, `package`, `signature`, `icon`, `device`).

```sh
pip install -e ./cli        # installs `android-agent` on PATH
android-agent start
android-agent ui
android-agent tap --text Settings
```

You can also run directly without installing:

```sh
python cli/android_agent.py status
```

### Connect to Coding Agents (choose either path)

You only need one of the two integrations below (or install both if you want live session context plus on-demand discovery):

1. **Session hook (recommended)**: injects compact live device/agent state at every session start in Claude Code, Codex, and OpenCode.
   ```sh
   android-agent setup
   ```
2. **Installable Agent Skill**: zero per-session token cost, loads on demand in any Agent Skills-compatible harness from [`skills/android-agent/SKILL.md`](skills/android-agent/SKILL.md).
   ```sh
   npx skills add TheDeathDragon/AndroidAgent --skill android-agent
   ```

## Notes

- `UiAutomation` is a per-device singleton. A second agent on the same device
  fails with `UiAutomationService ... already registered`.
- Loopback-only by design; `adb forward` tunnels through adbd which connects
  on-device via 127.0.0.1.

## License

[Apache 2.0](LICENSE)
