# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions
are `YYMM.NNNN` (year-month + zero-padded `git rev-list --count HEAD`).

## [Unreleased]

### Added
- HTTP/1.1 Keep-Alive transport with clean shutdown.
- `/input/tap`, `/input/swipe`, `/input/key`, `/input/text`, `/input/global`, `/input/batch` with a direct `InputManager` fast path and `UiAutomation` fallback.
- `/screenshot` formats (PNG, JPEG, RGBA), `scale`, `crop` and a `fast=true` VirtualDisplay mirror (SurfaceControl on Android 10-13; minSdk 29).
- `/input/swipe` `easing=linear` (constant velocity like `adb shell input swipe`, so lists fling); the default `ease` slows to a stop.
- `/info` reports the current-rotation `width` / `height` / `rotation`, `jarSha256` of the loaded jar and a `features` list (`swipe-easing`).
- `/locales` and `/set-locale` (from `agent-fixes`); `/set-locale` rejects languages missing from the device's assets.
- One atrace section per request (`agent /path`), visible with `atrace_apps: "app_process"`.
- Python AXI CLI (`cli/`) and the generated `android-agent` skill.

### Changed
- PNG screenshots use a fast lossless encoder (~200 ms instead of 430-640 ms per 720x1640 frame), always RGB.
- The `fast` mirror is released 5 s after the last fast capture; a live mirror doubled SurfaceFlinger CPU and raised visible jank 5 -> 52 frames while scrolling.
- CLI: each device is reached through its own adb-assigned forward (never a fixed host port); `status` and the session hook report per device; `--host` connects directly; `swipe` defaults to `--easing linear`; `screen` defaults to `--no-fast`; `launch` resolves the launcher activity on the device; an `ok: false` injection exits 1; gesture timeouts follow the gesture length.

### Fixed
- `/signature` returns 404 for a package that is not installed instead of an empty 200.
- One malformed package no longer empties the whole `/packages` list.
- `/hierarchy` keeps emoji and other surrogate pairs instead of replacing each half with `.`.
- Mirror teardown runs on the mirror thread, so closing the `ImageReader` cannot race a frame read.
- CLI `launch` validates package and component names (`com.x;reboot` reached the shell), quotes components containing `$`, and no longer misreads a component named like `.ExceptionViewer` as a failure.

## [2605.0001] - 2026-05-13

### Added
- Initial release.
