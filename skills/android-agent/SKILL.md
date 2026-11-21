---
name: android-agent
description: >
  Control and inspect Android devices over ADB: start or stop AndroidAgent, capture screenshots, inspect or wait on UI hierarchy elements, tap, swipe, type text, inject keys and batched gestures, launch apps, and query installed packages, icons, signing schemes, and device hardware specs.
---

# android-agent

Inspect and automate Android devices over ADB using AndroidAgent.

Output is TOON on stdout (data and errors). Exit codes are 0 for success or no-op, 1 for runtime error, and 2 for usage error.

## Invocation

Run non-interactively with flags only. Use the installed binary when on PATH, or invoke the script directly from a repo checkout:

```sh
android-agent status
python cli/android_agent.py status
```

## Global Flags

| Flag | Description |
|------|-------------|
| `-h/--help` | Show help for this command |
| `-s/--serial SERIAL` | Target ADB device serial |
| `-p/--port PORT` | Agent port on the device (default: 9500); the host side is an adb-assigned forward per device |
| `--host HOST` | Connect straight to an agent at HOST:PORT, bypassing adb forwarding |

## Commands

### `devices`

List connected ADB devices.

Usage: `android-agent devices`

```sh
android-agent devices
```

### `start`

Push JAR if needed, launch on-device AndroidAgent, and forward port (idempotent).

Usage: `android-agent start [--jar PATH] [--timeout 15] [--restart]`

- `--jar PATH`: Explicit path to agent-server.jar
- `--timeout SEC`: Seconds to wait for /health (default: 15)
- `--restart`: Stop an already running agent before starting

```sh
android-agent start
android-agent -s R58N12345 start --restart
android-agent start --jar build/dist/agent-server.jar --timeout 20
```

### `stop`

Stop the running AndroidAgent server and remove ADB port forward (idempotent).

Usage: `android-agent stop`

```sh
android-agent stop
android-agent -s R58N12345 -p 9501 stop
```

### `status` (aliases: `health`)

List connected ADB devices with the agent state and version on each.

Usage: `android-agent status`

```sh
android-agent status
android-agent -p 9501 health
```

### `screen` (aliases: `screenshot`, `shot`)

Capture device screenshot to a file and print TOON metadata.

Usage: `android-agent screen [--out PATH] [--format jpeg|png|rgba] [--quality 1..100] [--scale 0.1..1.0] [--crop X,Y,W,H] [--fast|--no-fast]`

- `--out PATH`: Output file path (default: temp dir)
- `--format FMT`: Image format: jpeg, png, or rgba (default: jpeg)
- `--quality INT`: Compression quality 1..100 (default: 80 for jpeg, 100 for png/rgba)
- `--scale FLOAT`: Downscale factor 0.1..1.0 (default: 0.5)
- `--crop X,Y,W,H`: Crop region in scaled image pixels: x,y,w,h (x>=0, y>=0, w>0, h>0)
- `--fast/--no-fast`: Use a VirtualDisplay mirror (Android 10+): faster for bursts of captures, but while it lives the device composes an extra display every frame and janks; the agent releases it 5 s after the last fast capture (default: --no-fast)

```sh
android-agent screen
android-agent screen --format png --scale 1.0 --out screen.png
android-agent screen --crop 0,0,540,400 --scale 0.5
android-agent screen --fast --scale 0.3
```

### `ui` (aliases: `hierarchy`)

Inspect active window UI hierarchy as compact TOON table or raw XML.

Usage: `android-agent ui [--query STR] [--text STR] [--id STR] [--clickable] [--scrollable] [--all] [--uncompressed] [--fields CSV] [--limit 50] [--full] [--xml]`

- `--query STR`: Case-insensitive match across label, text, desc, and res_id
- `--text STR`: Case-insensitive match on text, content-desc, or hoisted label
- `--id STR`: Case-insensitive match on resource-id
- `--clickable`: Only include clickable nodes
- `--scrollable`: Only include scrollable nodes
- `--all`: Include non-interactive nodes
- `--uncompressed`: Request compressed=false from /hierarchy
- `--fields CSV`: Comma-separated columns to output
- `--limit N`: Maximum rows to show (default: 50)
- `--full`: Disable row limit or XML truncation
- `--xml`: Return raw XML (truncated at 2000 chars unless --full)

```sh
android-agent ui
android-agent ui --query Wi-Fi
android-agent ui --clickable --fields i,label,cx,cy
android-agent ui --xml --full
```

### `tap`

Tap screen coordinates or find and tap a UI element in one command.

Usage: `android-agent tap (<x> <y> | --text STR | --id STR | --query STR) [--exact] [--index N] [--duration 0] [--no-sync]`

- `--text STR`: Find element by text, content-desc, or hoisted label
- `--id STR`: Find element by resource-id
- `--query STR`: Find element by label, text, desc, or resource-id
- `--exact`: Require exact case-insensitive match for selector
- `--index N`: Disambiguate multi-match selector by 0-based index
- `--duration MS`: Hold duration in ms for long-press (0..30000, default: 0)
- `--no-sync`: Inject asynchronously (sync=false, fire-and-forget for lowest latency)

```sh
android-agent tap 540 1200
android-agent tap 540 1200 --no-sync
android-agent tap --text Settings
android-agent tap --id search_bar --duration 600
```

### `swipe`

Swipe between coordinates or in a cardinal direction (up, down, left, right).

Usage: `android-agent swipe (<x1> <y1> <x2> <y2> | up|down|left|right) [--distance 0.6] [--duration 300] [--easing linear|ease] [--steps N] [--hold 0] [--no-sync]`

- `--distance FRAC`: Screen fraction for directional swipe (0.05..0.95, default: 0.6)
- `--duration MS`: Swipe duration in ms (0..30000, default: 300)
- `--steps N`: Interpolation step count (1..300)
- `--hold MS`: Hold at end position before release (0..10000, default: 0)
- `--easing CURVE`: linear: constant velocity like `adb shell input swipe`, lists fling (default); ease: slows to a stop, no fling
- `--no-sync`: Inject asynchronously (sync=false, fire-and-forget for lowest latency)

```sh
android-agent swipe up
android-agent swipe down --distance 0.7 --duration 400
android-agent swipe 500 1800 500 400 --steps 20
```

### `key`

Inject one or more Android key events by friendly name, KEYCODE_*, or integer.

Usage: `android-agent key <codes> [--meta 0] [--interval 0] [--no-sync]`

- `--meta INT`: KeyEvent meta state bitmask (default: 0)
- `--interval MS`: Delay between batched keys in ms (0..30000, default: 0)
- `--no-sync`: Inject asynchronously (sync=false, fire-and-forget for lowest latency)

```sh
android-agent key enter
android-agent key back
android-agent key KEYCODE_DPAD_DOWN,enter --interval 80
```

### `type` (aliases: `text`, `input-text`)

Type or set text in the focused input or a matched UI element.

Usage: `android-agent type [<text>] [--text STR | --id STR | --query STR] [--exact] [--index N] [--clear] [--submit] [--no-sync]`

- `--text STR`: Tap matching element by text, content-desc, or hoisted label before typing
- `--id STR`: Tap matching element by resource-id before typing
- `--query STR`: Tap matching element by label, text, desc, or resource-id before typing
- `--exact`: Require exact case-insensitive match for target selector
- `--index N`: Disambiguate multi-match target selector by 0-based index
- `--clear`: Replace/clear existing field text before typing
- `--submit`: Send KEYCODE_ENTER after typing
- `--no-sync`: Inject asynchronously (sync=false, fire-and-forget for lowest latency)

```sh
android-agent type "hello world"
android-agent type "wifi" --id search_src_text --clear --submit
android-agent type --clear
```

### `wait` (aliases: `wait-for`)

Poll UI hierarchy over Keep-Alive until a matching element appears or disappears.

Usage: `android-agent wait (--text STR | --id STR | --query STR) [--exact] [--gone] [--all] [--timeout 10] [--interval 250]`

- `--text STR`: Match element by text, content-desc, or hoisted label
- `--id STR`: Match element by resource-id
- `--query STR`: Match element by label, text, desc, or resource-id
- `--exact`: Require exact case-insensitive match
- `--gone`: Wait until no matching element remains visible
- `--all`: Include non-interactive nodes when matching
- `--timeout SEC`: Maximum wait time in seconds (0.1..120, default: 10)
- `--interval MS`: Polling interval in ms (50..5000, default: 250)

```sh
android-agent wait --text "Wi-Fi"
android-agent wait --id loading_spinner --gone --timeout 15
android-agent wait --query "Search" --all --interval 200
```

### `launch` (aliases: `open`, `start-app`)

Launch an installed app by package name or explicit activity component.

Usage: `android-agent launch <pkg> [--activity COMPONENT] [--stop] [--wait]`

- `--activity COMPONENT`: Explicit activity component (e.g. com.android.settings/.Settings or .MainActivity)
- `--stop`: Force-stop the package before launching
- `--wait`: Pass -W to am start to wait for launch completion

```sh
android-agent launch com.android.settings
android-agent launch com.android.chrome --stop --wait
```

### `global`

Perform an Android AccessibilityService global action.

Usage: `android-agent global <action>`

```sh
android-agent global home
android-agent global recents
android-agent global notifications
```

### `batch`

Validate and execute a JSON gesture/input action sequence via /input/batch.

Usage: `android-agent batch [<json>] [--file PATH|-] [--no-sync]`

- `--file PATH`: Read JSON payload from file or '-' for stdin
- `--no-sync`: Inject asynchronously (sync=false, fire-and-forget for lowest latency)

```sh
android-agent batch '[{"type":"tap","x":540,"y":1200},{"type":"wait","durationMs":200}]'
android-agent batch --file gesture.json
```

### `packages` (aliases: `pkgs`)

List installed packages with minimal default schema and aggregate counts.

Usage: `android-agent packages [--user-only] [--query STR] [--limit 100] [--fields CSV] [--full]`

- `--user-only`: Only return non-system packages
- `--query STR`: Case-insensitive filter on packageName or label
- `--limit N`: Maximum rows to show (default: 100)
- `--fields CSV`: Comma-separated columns to output
- `--full`: Return all matching rows without limit

```sh
android-agent packages --user-only
android-agent packages --query chrome
android-agent packages --fields packageName,versionCode,apkSize --limit 20
```

### `package` (aliases: `pkg`)

Show full metadata and signing schemes for one installed package.

Usage: `android-agent package <pkg>`

```sh
android-agent package com.android.settings
android-agent pkg com.android.chrome
```

### `signature` (aliases: `sig`)

Inspect APK signing schemes (V1/V2/V3/V3.1/V4) for one package.

Usage: `android-agent signature <pkg>`

```sh
android-agent signature com.android.settings
```

### `icon`

Download an installed package's PNG icon to a local file.

Usage: `android-agent icon <pkg> [--out PATH]`

- `--out PATH`: Output PNG path (default: temp dir)

```sh
android-agent icon com.android.settings
android-agent icon com.android.settings --out settings.png
```

### `device` (aliases: `device-info`)

Show compact device hardware/OS summary or drill down into one /device-info section.

Usage: `android-agent device [--section NAME] [--query STR] [--limit 30] [--full]`

- `--section NAME`: Inspect one section: device,build,ram,storage,display,cpu,gpu,sensors,cameraV1,cameras,network,sim,treble,features,properties
- `--query STR`: Case-insensitive filter within the selected section
- `--limit N`: Maximum rows in section view (default: 30)
- `--full`: Show all entries in section view without limit

```sh
android-agent device
android-agent device --section display
android-agent device --section properties --query ro.build
```

### `setup`

Install, repair, or remove SessionStart hooks/plugins for Claude Code, Codex, and OpenCode.

Usage: `android-agent setup [--target claude|codex|opencode|all] [--scope user|project] [--dry-run] [--remove]`

- `--target APP`: Target harness: claude, codex, opencode, or all (default: all)
- `--scope SCOPE`: Install scope: user or project (default: user)
- `--dry-run`: Preview planned changes without writing files
- `--remove`: Remove installed android-agent hooks/plugins

```sh
android-agent setup
android-agent setup --target claude --scope project
android-agent setup --dry-run
android-agent setup --remove
```

### `skill`

Generate, write, or drift-check skills/android-agent/SKILL.md from CLI spec.

Usage: `android-agent skill [--write | --check] [--out PATH]`

- `--write`: Write generated SKILL.md to disk
- `--check`: Exit 0 if SKILL.md matches spec, else exit 1
- `--out PATH`: Override output/check path (default: skills/android-agent/SKILL.md)

```sh
android-agent skill --write
android-agent skill --check
```

## Notes

- `start` and `stop` are idempotent and exit 0 when already in the target state; `start` verifies the on-device JAR by SHA-256 before skipping push.
- Each device is reached through its own adb-assigned forward to the agent's device port (found again on every run, created once with `adb forward tcp:0`), never a fixed host port: `adb forward` is global, so a fixed port is silently taken over by another tool and commands land on another phone. With several devices pass `-s SERIAL`; `status` lists the agent state per device. `--host` connects straight to `HOST:PORT` without adb.
- `screen` defaults to `--format jpeg --quality 80 --scale 0.5` via UiAutomation. `--fast` uses a VirtualDisplay mirror with a `frameVersion` encoding cache (`cache_hit: true` when the frame is unchanged); it pays off only for bursts, because the device composes the mirror every frame (measured: visible jank 10x while scrolling) until the agent releases it 5 s after the last fast capture. `--crop X,Y,W,H` uses scaled image pixels, while `tap` and `swipe` always use native screen pixels.
- `swipe` defaults to `--easing linear` (constant velocity, like `adb shell input swipe`, so lists fling); `--easing ease` slows to a stop. Directional swipes size themselves from `/info` in the current rotation.
- `launch` resolves the launcher activity on the device and starts it with the launcher's flags, so a running app resumes as from the home screen.
- `tap`, `swipe`, `key`, `type`, and `batch` use direct `InputManager` injection (`source: direct`) with automatic `UiAutomation` fallback (`source: uiautomation`); `type` uses `ACTION_SET_TEXT` on focused editable nodes when `--clear` or Unicode text is given.
- `tap` and `type` accept `--text/--id/--query` to find and tap a UI element over one Keep-Alive connection, walking up to clickable ancestors and returning an inline candidate table on ambiguous matches.
- `wait` polls `/hierarchy` over a single Keep-Alive connection until a selector appears (or disappears with `--gone`), avoiding multi-turn shell polling loops.
- `ui` defaults to interactive nodes with container label hoisting; pass `--all` for every node or `--xml` for raw hierarchy XML.
- Run `android-agent setup` to install ambient `SessionStart` hooks for Claude Code, Codex, and OpenCode.
