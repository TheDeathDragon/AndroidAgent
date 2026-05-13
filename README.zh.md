# AndroidAgent

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/release/TheDeathDragon/AndroidAgent)](https://github.com/TheDeathDragon/AndroidAgent/releases)

[English](README.md) | [简体中文](README.zh.md)

设备端 Android 自动化 HTTP 服务。通过 `app_process` 启动，无需安装 APK，
权限不超出 `shell` uid。

## 特性

- `/screenshot` 走 `UiAutomation.takeScreenshot()` —— 比 `screencap` 快约 3×
- `/hierarchy` 走 `AccessibilityNodeInfo` 遍历 —— 比 `uiautomator dump` 快约 10×
- `/packages`、`/icon`、`/signature`、`/device-info` 由 `PackageManager` + `dumpsys` 提供
- 仅监听 127.0.0.1，必须通过 `adb forward` 才能访问
- 零外部依赖，R8 压缩后 ~92 KB

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

| Method | Path                          | 返回                                 |
|--------|-------------------------------|--------------------------------------|
| GET    | `/health`                     | 文本 `ok`                            |
| GET    | `/hierarchy?compressed=true`  | `application/xml`                    |
| GET    | `/screenshot?quality=100`     | `image/png`                          |
| GET    | `/packages?user_only=false`   | JSON 数组                             |
| GET    | `/icon?pkg=<name>`            | `image/png`                          |
| GET    | `/signature?pkg=<name>`       | 文本 —— V1/V2/V3/V4 签名方案          |
| GET    | `/device-info`                | JSON —— 14 段设备画像                 |
| GET    | `/info`                       | JSON —— brand/model/sdk/version      |
| GET    | `/stop`                       | 文本 `stopping` —— 优雅关停           |

## 注意

- `UiAutomation` 是设备级单例。同一台设备上跑第二份 agent 会报
  `UiAutomationService ... already registered`。
- 默认仅 loopback；`adb forward` 通过 adbd 在设备端以 127.0.0.1 连接，
  同 Wi-Fi 的其他设备无法直接访问。

## 开源协议

[Apache 2.0](LICENSE)
