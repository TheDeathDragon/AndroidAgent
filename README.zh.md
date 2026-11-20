# AndroidAgent

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/release/TheDeathDragon/AndroidAgent)](https://github.com/TheDeathDragon/AndroidAgent/releases)

[English](README.md) | [简体中文](README.zh.md)

设备端 Android 自动化 HTTP 服务。通过 `app_process` 启动，无需安装 APK，
权限不超出 `shell` uid。

## 特性

- `/screenshot` 支持 `UiAutomation.takeScreenshot()` 与常驻 `VirtualDisplay` 镜像 (`fast=true`), 支持 PNG/JPEG/RGBA 输出与缩放
- `/input/*` 通过 `UiAutomation.injectInputEvent()` 实现低延迟点击、滑动、按键、全局操作与批量手势注入
- `/hierarchy` 走 `AccessibilityNodeInfo` 遍历 (比 `uiautomator dump` 快约 10x)
- `/packages`、`/icon`、`/signature`、`/device-info` 由 `PackageManager` + `dumpsys` 提供
- 支持 HTTP/1.1 Keep-Alive, 仅监听 `127.0.0.1`, 必须通过 `adb forward` 访问
- 零外部依赖, 独立 DEX JAR, 供 `app_process` 直接运行

## 构建

需要 Android SDK 与 JDK 11。在 `local.properties` 设 `sdk.dir=` 或导出
`ANDROID_HOME` 环境变量。

agent 用到 hidden API（`ActivityThread`、`UiAutomationConnection`、
`DisplayManagerGlobal`）。`sdkmanager` 默认下发的 `android.jar` 阉割掉了它们，
编译需要把 `$ANDROID_HOME/platforms/android-36/android.jar` 替换为带 hidden
API 的版本，例如 [Reginer/aosp-android-jar](https://github.com/Reginer/aosp-android-jar)
仓库的 `android-36/android.jar`。CI 已通过 `curl` 自动处理。

```
build.bat            # release（默认）
build.bat debug
```

输出：

- `build/dist/agent-server.jar` —— 规范输出位置
- `../tools/agent-server.jar` —— 当父目录有 `tools/` 时也会写入
  （供把本仓库作为 submodule 的上层项目自动取用）

## 运行

```
adb push build/dist/agent-server.jar /data/local/tmp/agent-server.jar
adb shell CLASSPATH=/data/local/tmp/agent-server.jar app_process / la.shiro.agent.Server --port 9500
adb forward tcp:9500 tcp:9500
curl http://127.0.0.1:9500/health
```

## 接口

所有接口均支持 HTTP/1.1 Keep-Alive (单连接最多 1000 次请求, 空闲超时 15 秒)。

| Method   | Path                          | 返回 / 说明                                                       |
|----------|-------------------------------|-------------------------------------------------------------------|
| GET      | `/health`                     | 文本 `ok`                                                         |
| GET      | `/hierarchy?compressed=true`  | `application/xml`                                                 |
| GET      | `/screenshot`                 | `image/png`、`image/jpeg` 或 `application/octet-stream` (原始 RGBA) |
| GET/POST | `/input/tap`                  | JSON `{"ok":true}` (查询参数或 JSON: `x`, `y`, `durationMs`, `sync`) |
| GET/POST | `/input/swipe`                | JSON `{"ok":true}` (查询参数或 JSON: `x1`, `y1`, `x2`, `y2`, `durationMs`, `steps`, `holdMs`, `sync`) |
| GET/POST | `/input/key`                  | JSON `{"ok":true}` (查询参数或 JSON: `keyCode`/`keyCodes`, `metaState`, `intervalMs`, `sync`) |
| GET/POST | `/input/global`               | JSON `{"ok":true}` (`action`: `back`, `home`, `recents`, `notifications`, `quick_settings`, `power_dialog`, `lock_screen`, `screenshot` 或数字 ID) |
| POST     | `/input/batch`                | JSON `{"ok":true}` (JSON 请求体 `{"actions":[...],"sync":true}` 或 `[...]`; 支持 `down`/`move`/`up`/`tap`/`swipe`/`wait`/`key`/`global`) |
| GET      | `/packages?user_only=false`   | JSON 数组                                                         |
| GET      | `/icon?pkg=<name>`            | `image/png`                                                       |
| GET      | `/signature?pkg=<name>`       | 文本 (V1/V2/V3/V4 签名方案)                                       |
| GET      | `/device-info`                | JSON (14 段设备画像)                                              |
| GET      | `/info`                       | JSON (`brand`/`model`/`sdk`/`version`)                            |
| GET      | `/stop`                       | 文本 `stopping` (优雅关停)                                        |

### `/screenshot` 参数与响应头

- `format`: `png` (默认, 质量 100)、`jpeg`/`jpg` (默认质量 80) 或 `rgba` (紧密排列的 `RGBA_8888` 原始字节)
- `quality`: 整数 `1..100`
- `scale`: 浮点数 `0.1..1.0` (默认 `1.0`)
- `fast`: `true` 或 `false` (默认 `false`); 开启后优先走常驻 `VirtualDisplay` 镜像, 不支持或首帧未就绪时自动回退至 `UiAutomation.takeScreenshot()`
- 响应头: `X-Width`、`X-Height`、`X-Format`、`X-Capture-Source` (`virtual-display` 或 `uiautomation`)

## AXI 命令行工具与 Agent Skill

`cli/` 提供了零外部依赖的 Python 3.10+ 命令行工具 (`android-agent`)，以 TOON 输出、HTTP/1.1 Keep-Alive 连接复用、严格参数校验和幂等启停封装了所有 AndroidAgent 接口（`start`、`stop`、`screen`、`ui`、`tap`、`swipe`、`key`、`global`、`batch`、`packages`、`package`、`signature`、`icon`、`device`）。

```sh
pip install -e ./cli        # 将 `android-agent` 安装到 PATH
android-agent start
android-agent ui
android-agent tap --text Settings
```

也可无需安装直接运行：

```sh
python cli/android_agent.py status
```

### 接入 Coding Agent（二选一即可）

以下两种接入方式任选其一即可（也可同时安装以兼得会话启动实时状态与按需加载）：

1. **Session Hook（推荐）**：在 Claude Code、Codex 与 OpenCode 会话启动时自动注入精简设备/服务状态。
   ```sh
   android-agent setup
   ```
2. **可安装 Agent Skill**：无常驻会话 token 开销，支持任意兼容 Agent Skills 格式的客户端按需加载 [`skills/android-agent/SKILL.md`](skills/android-agent/SKILL.md)。
   ```sh
   npx skills add TheDeathDragon/AndroidAgent --skill android-agent
   ```

## 注意

- `UiAutomation` 是设备级单例。同一台设备上跑第二份 agent 会报
  `UiAutomationService ... already registered`。
- 默认仅 loopback；`adb forward` 通过 adbd 在设备端以 127.0.0.1 连接，
  同 Wi-Fi 的其他设备无法直接访问。

## 开源协议

[Apache 2.0](LICENSE)
