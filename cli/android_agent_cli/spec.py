from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

TOOL_NAME = "android-agent"
TOOL_DESCRIPTION = "Inspect and automate Android devices over ADB using AndroidAgent"
SKILL_TRIGGER_DESCRIPTION = (
    "Control and inspect Android devices over ADB: start or stop AndroidAgent, "
    "capture screenshots, inspect UI hierarchy, tap or swipe UI elements, "
    "inject keys and batched gestures, and query installed apps, icons, "
    "signing schemes, and device hardware specs."
)

DEVICE_SECTIONS: tuple[str, ...] = (
    "device",
    "build",
    "ram",
    "storage",
    "display",
    "cpu",
    "gpu",
    "sensors",
    "cameraV1",
    "cameras",
    "network",
    "sim",
    "treble",
    "features",
    "properties",
)

GLOBAL_ACTIONS: tuple[str, ...] = (
    "back",
    "home",
    "recents",
    "notifications",
    "quick_settings",
    "power_dialog",
    "lock_screen",
    "screenshot",
)

SWIPE_DIRECTIONS: tuple[str, ...] = ("up", "down", "left", "right")

KEY_ALIASES: dict[str, int] = {
    "home": 3,
    "back": 4,
    "call": 5,
    "endcall": 6,
    "up": 19,
    "dpad_up": 19,
    "down": 20,
    "dpad_down": 20,
    "left": 21,
    "dpad_left": 21,
    "right": 22,
    "dpad_right": 22,
    "center": 23,
    "dpad_center": 23,
    "volume_up": 24,
    "vol_up": 24,
    "volume_down": 25,
    "vol_down": 25,
    "power": 26,
    "camera": 27,
    "clear": 28,
    "tab": 61,
    "space": 62,
    "enter": 66,
    "return": 66,
    "del": 67,
    "backspace": 67,
    "menu": 82,
    "search": 84,
    "escape": 111,
    "esc": 111,
    "forward_del": 112,
    "delete": 112,
    "app_switch": 187,
    "recents": 187,
}

UI_DEFAULT_FIELDS: tuple[str, ...] = ("i", "label", "res_id", "cx", "cy", "act")
UI_ALL_FIELDS: tuple[str, ...] = (
    "i",
    "label",
    "text",
    "desc",
    "res_id",
    "raw_res_id",
    "class",
    "pkg",
    "bounds",
    "cx",
    "cy",
    "act",
    "clickable",
    "long_clickable",
    "scrollable",
    "checkable",
    "checked",
    "enabled",
    "focusable",
    "focused",
    "selected",
)

PACKAGE_DEFAULT_FIELDS: tuple[str, ...] = ("packageName", "label", "versionName", "enabled")
PACKAGE_ALL_FIELDS: tuple[str, ...] = (
    "packageName",
    "label",
    "versionName",
    "versionCode",
    "apkPath",
    "apkSize",
    "system",
    "enabled",
    "suspended",
    "uid",
    "firstInstallTime",
    "lastUpdateTime",
    "targetSdkVersion",
    "minSdkVersion",
    "mainActivity",
    "signatureSha256",
    "dataDir",
)


@dataclass(frozen=True)
class FlagSpec:
    name: str
    long: str
    short: str | None = None
    negated: str | None = None
    kind: str = "str"
    default: Any = None
    value_name: str = ""
    description: str = ""
    choices: tuple[str, ...] | None = None
    min_val: float | None = None
    max_val: float | None = None

    def display_names(self) -> str:
        parts = []
        if self.short:
            parts.append(self.short)
        parts.append(self.long)
        if self.negated:
            parts.append(self.negated)
        label = "/".join(parts)
        if self.kind != "bool" and self.value_name:
            return f"{label} {self.value_name}"
        return label


@dataclass(frozen=True)
class PosSpec:
    name: str
    required: bool = True
    description: str = ""


@dataclass(frozen=True)
class CommandSpec:
    name: str
    summary: str
    usage: str
    aliases: tuple[str, ...] = ()
    positionals: tuple[PosSpec, ...] = ()
    flags: tuple[FlagSpec, ...] = ()
    renamed_flags: dict[str, tuple[str, str]] = field(default_factory=dict)
    examples: tuple[str, ...] = ()
    min_args: int = 0
    max_args: int | None = 0
    accepts_numeric_positionals: bool = False


GLOBAL_FLAGS: tuple[FlagSpec, ...] = (
    FlagSpec("help", "--help", "-h", kind="bool", default=False, description="Show help for this command"),
    FlagSpec("serial", "--serial", "-s", kind="str", default=None, value_name="SERIAL", description="Target ADB device serial"),
    FlagSpec("port", "--port", "-p", kind="int", default=9500, value_name="PORT", description="Forwarded agent HTTP port (default: 9500)", min_val=1, max_val=65535),
    FlagSpec("host", "--host", kind="str", default="127.0.0.1", value_name="HOST", description="Agent HTTP host (default: 127.0.0.1)"),
)

GLOBAL_RENAMED_FLAGS: dict[str, tuple[str, str]] = {
    "--device": ("--serial", "--device was renamed; use --serial (-s) instead"),
}

HOME_FLAGS: tuple[FlagSpec, ...] = (
    FlagSpec("hook", "--hook", kind="bool", default=False, description="Emit compact ambient session-start view and always exit 0"),
)

COMMANDS: tuple[CommandSpec, ...] = (
    CommandSpec(
        name="devices",
        summary="List connected ADB devices",
        usage="android-agent devices",
        examples=("android-agent devices",),
        min_args=0,
        max_args=0,
    ),
    CommandSpec(
        name="start",
        summary="Push JAR if needed, launch on-device AndroidAgent, and forward port (idempotent)",
        usage="android-agent start [--jar PATH] [--timeout 15] [--restart]",
        flags=(
            FlagSpec("jar", "--jar", kind="str", default=None, value_name="PATH", description="Explicit path to agent-server.jar"),
            FlagSpec("timeout", "--timeout", kind="float", default=15.0, value_name="SEC", description="Seconds to wait for /health (default: 15)", min_val=0.5, max_val=120.0),
            FlagSpec("restart", "--restart", kind="bool", default=False, description="Stop an already running agent before starting"),
        ),
        examples=(
            "android-agent start",
            "android-agent -s R58N12345 start --restart",
            "android-agent start --jar build/dist/agent-server.jar --timeout 20",
        ),
        min_args=0,
        max_args=0,
    ),
    CommandSpec(
        name="stop",
        summary="Stop the running AndroidAgent server and remove ADB port forward (idempotent)",
        usage="android-agent stop",
        examples=(
            "android-agent stop",
            "android-agent -s R58N12345 -p 9501 stop",
        ),
        min_args=0,
        max_args=0,
    ),
    CommandSpec(
        name="status",
        aliases=("health",),
        summary="Check agent /health, /info, and connected ADB devices",
        usage="android-agent status",
        examples=(
            "android-agent status",
            "android-agent -p 9501 health",
        ),
        min_args=0,
        max_args=0,
    ),
    CommandSpec(
        name="screen",
        aliases=("screenshot", "shot"),
        summary="Capture device screenshot to a file and print TOON metadata",
        usage="android-agent screen [--out PATH] [--format jpeg|png|rgba] [--quality 1..100] [--scale 0.1..1.0] [--crop X,Y,W,H] [--fast|--no-fast]",
        flags=(
            FlagSpec("out", "--out", kind="str", default=None, value_name="PATH", description="Output file path (default: temp dir)"),
            FlagSpec("format", "--format", kind="str", default="jpeg", value_name="FMT", description="Image format: jpeg, png, or rgba (default: jpeg)", choices=("jpeg", "jpg", "png", "rgba")),
            FlagSpec("quality", "--quality", kind="int", default=None, value_name="INT", description="Compression quality 1..100 (default: 80 for jpeg, 100 for png/rgba)", min_val=1, max_val=100),
            FlagSpec("scale", "--scale", kind="float", default=0.5, value_name="FLOAT", description="Downscale factor 0.1..1.0 (default: 0.5)", min_val=0.1, max_val=1.0),
            FlagSpec("crop", "--crop", kind="str", default=None, value_name="X,Y,W,H", description="Crop region in scaled image pixels: x,y,w,h (x>=0, y>=0, w>0, h>0)"),
            FlagSpec("fast", "--fast", negated="--no-fast", kind="bool", default=True, description="Use VirtualDisplay mirror when available (default: --fast)"),
        ),
        renamed_flags={
            "--output": ("--out", "--output is not used; use --out PATH instead"),
        },
        examples=(
            "android-agent screen",
            "android-agent screen --format png --scale 1.0 --out screen.png",
            "android-agent screen --crop 0,0,540,400 --scale 0.5",
            "android-agent screen --no-fast --quality 90",
        ),
        min_args=0,
        max_args=0,
    ),
    CommandSpec(
        name="ui",
        aliases=("hierarchy",),
        summary="Inspect active window UI hierarchy as compact TOON table or raw XML",
        usage="android-agent ui [--query STR] [--text STR] [--id STR] [--clickable] [--scrollable] [--all] [--uncompressed] [--fields CSV] [--limit 50] [--full] [--xml]",
        flags=(
            FlagSpec("query", "--query", kind="str", default=None, value_name="STR", description="Case-insensitive match across label, text, desc, and res_id"),
            FlagSpec("text", "--text", kind="str", default=None, value_name="STR", description="Case-insensitive match on text, content-desc, or hoisted label"),
            FlagSpec("id", "--id", kind="str", default=None, value_name="STR", description="Case-insensitive match on resource-id"),
            FlagSpec("clickable", "--clickable", kind="bool", default=False, description="Only include clickable nodes"),
            FlagSpec("scrollable", "--scrollable", kind="bool", default=False, description="Only include scrollable nodes"),
            FlagSpec("all", "--all", kind="bool", default=False, description="Include non-interactive nodes"),
            FlagSpec("uncompressed", "--uncompressed", kind="bool", default=False, description="Request compressed=false from /hierarchy"),
            FlagSpec("fields", "--fields", kind="str", default=None, value_name="CSV", description="Comma-separated columns to output"),
            FlagSpec("limit", "--limit", kind="int", default=50, value_name="N", description="Maximum rows to show (default: 50)", min_val=1),
            FlagSpec("full", "--full", kind="bool", default=False, description="Disable row limit or XML truncation"),
            FlagSpec("xml", "--xml", kind="bool", default=False, description="Return raw XML (truncated at 2000 chars unless --full)"),
        ),
        examples=(
            "android-agent ui",
            "android-agent ui --query Wi-Fi",
            "android-agent ui --clickable --fields i,label,cx,cy",
            "android-agent ui --xml --full",
        ),
        min_args=0,
        max_args=0,
    ),
    CommandSpec(
        name="tap",
        summary="Tap screen coordinates or find and tap a UI element in one command",
        usage="android-agent tap (<x> <y> | --text STR | --id STR | --query STR) [--exact] [--index N] [--duration 0] [--no-sync]",
        positionals=(
            PosSpec("x", required=False, description="X coordinate in native pixels"),
            PosSpec("y", required=False, description="Y coordinate in native pixels"),
        ),
        flags=(
            FlagSpec("text", "--text", kind="str", default=None, value_name="STR", description="Find element by text, content-desc, or hoisted label"),
            FlagSpec("id", "--id", kind="str", default=None, value_name="STR", description="Find element by resource-id"),
            FlagSpec("query", "--query", kind="str", default=None, value_name="STR", description="Find element by label, text, desc, or resource-id"),
            FlagSpec("exact", "--exact", kind="bool", default=False, description="Require exact case-insensitive match for selector"),
            FlagSpec("index", "--index", kind="int", default=None, value_name="N", description="Disambiguate multi-match selector by 0-based index", min_val=0),
            FlagSpec("duration", "--duration", kind="int", default=0, value_name="MS", description="Hold duration in ms for long-press (0..30000, default: 0)", min_val=0, max_val=30000),
            FlagSpec("no_sync", "--no-sync", kind="bool", default=False, description="Inject asynchronously (sync=false)"),
        ),
        renamed_flags={
            "--durationMs": ("--duration", "use --duration MS instead of --durationMs"),
        },
        examples=(
            "android-agent tap 540 1200",
            "android-agent tap --text Settings",
            "android-agent tap --id search_bar --duration 600",
        ),
        min_args=0,
        max_args=2,
        accepts_numeric_positionals=True,
    ),
    CommandSpec(
        name="swipe",
        summary="Swipe between coordinates or in a cardinal direction (up, down, left, right)",
        usage="android-agent swipe (<x1> <y1> <x2> <y2> | up|down|left|right) [--distance 0.6] [--duration 300] [--steps N] [--hold 0] [--no-sync]",
        positionals=(
            PosSpec("coords_or_dir", required=True, description="Four coordinates <x1> <y1> <x2> <y2> or direction up|down|left|right"),
        ),
        flags=(
            FlagSpec("distance", "--distance", kind="float", default=0.6, value_name="FRAC", description="Screen fraction for directional swipe (0.05..0.95, default: 0.6)", min_val=0.05, max_val=0.95),
            FlagSpec("duration", "--duration", kind="int", default=300, value_name="MS", description="Swipe duration in ms (0..30000, default: 300)", min_val=0, max_val=30000),
            FlagSpec("steps", "--steps", kind="int", default=None, value_name="N", description="Interpolation step count (1..300)", min_val=1, max_val=300),
            FlagSpec("hold", "--hold", kind="int", default=0, value_name="MS", description="Hold at end position before release (0..10000, default: 0)", min_val=0, max_val=10000),
            FlagSpec("no_sync", "--no-sync", kind="bool", default=False, description="Inject asynchronously (sync=false)"),
        ),
        renamed_flags={
            "--durationMs": ("--duration", "use --duration MS instead of --durationMs"),
            "--holdMs": ("--hold", "use --hold MS instead of --holdMs"),
        },
        examples=(
            "android-agent swipe up",
            "android-agent swipe down --distance 0.7 --duration 400",
            "android-agent swipe 500 1800 500 400 --steps 20",
        ),
        min_args=1,
        max_args=4,
        accepts_numeric_positionals=True,
    ),
    CommandSpec(
        name="key",
        summary="Inject one or more Android key events by friendly name, KEYCODE_*, or integer",
        usage="android-agent key <codes> [--meta 0] [--interval 0] [--no-sync]",
        positionals=(
            PosSpec("codes", required=True, description="Friendly name, KEYCODE_*, positive int, or comma-separated list"),
        ),
        flags=(
            FlagSpec("meta", "--meta", kind="int", default=0, value_name="INT", description="KeyEvent meta state bitmask (default: 0)", min_val=0),
            FlagSpec("interval", "--interval", kind="int", default=0, value_name="MS", description="Delay between batched keys in ms (0..30000, default: 0)", min_val=0, max_val=30000),
            FlagSpec("no_sync", "--no-sync", kind="bool", default=False, description="Inject asynchronously (sync=false)"),
        ),
        renamed_flags={
            "--metaState": ("--meta", "use --meta INT instead of --metaState"),
            "--intervalMs": ("--interval", "use --interval MS instead of --intervalMs"),
        },
        examples=(
            "android-agent key enter",
            "android-agent key back",
            "android-agent key KEYCODE_DPAD_DOWN,enter --interval 80",
        ),
        min_args=1,
        max_args=1,
    ),
    CommandSpec(
        name="global",
        summary="Perform an Android AccessibilityService global action",
        usage="android-agent global <action>",
        positionals=(
            PosSpec("action", required=True, description="back|home|recents|notifications|quick_settings|power_dialog|lock_screen|screenshot or positive int"),
        ),
        examples=(
            "android-agent global home",
            "android-agent global recents",
            "android-agent global notifications",
        ),
        min_args=1,
        max_args=1,
    ),
    CommandSpec(
        name="batch",
        summary="Validate and execute a JSON gesture/input action sequence via /input/batch",
        usage="android-agent batch [<json>] [--file PATH|-] [--no-sync]",
        positionals=(
            PosSpec("json", required=False, description="Inline JSON array [...] or object {\"actions\":[...]}"),
        ),
        flags=(
            FlagSpec("file", "--file", kind="str", default=None, value_name="PATH", description="Read JSON payload from file or '-' for stdin"),
            FlagSpec("no_sync", "--no-sync", kind="bool", default=False, description="Inject asynchronously (sync=false)"),
        ),
        examples=(
            "android-agent batch '[{\"type\":\"tap\",\"x\":540,\"y\":1200},{\"type\":\"wait\",\"durationMs\":200}]'",
            "android-agent batch --file gesture.json",
        ),
        min_args=0,
        max_args=1,
    ),
    CommandSpec(
        name="packages",
        aliases=("pkgs",),
        summary="List installed packages with minimal default schema and aggregate counts",
        usage="android-agent packages [--user-only] [--query STR] [--limit 100] [--fields CSV] [--full]",
        flags=(
            FlagSpec("user_only", "--user-only", kind="bool", default=False, description="Only return non-system packages"),
            FlagSpec("query", "--query", kind="str", default=None, value_name="STR", description="Case-insensitive filter on packageName or label"),
            FlagSpec("limit", "--limit", kind="int", default=100, value_name="N", description="Maximum rows to show (default: 100)", min_val=1),
            FlagSpec("fields", "--fields", kind="str", default=None, value_name="CSV", description="Comma-separated columns to output"),
            FlagSpec("full", "--full", kind="bool", default=False, description="Return all matching rows without limit"),
        ),
        examples=(
            "android-agent packages --user-only",
            "android-agent packages --query chrome",
            "android-agent packages --fields packageName,versionCode,apkSize --limit 20",
        ),
        min_args=0,
        max_args=0,
    ),
    CommandSpec(
        name="package",
        aliases=("pkg",),
        summary="Show full metadata and signing schemes for one installed package",
        usage="android-agent package <pkg>",
        positionals=(
            PosSpec("pkg", required=True, description="Exact Android package name"),
        ),
        examples=(
            "android-agent package com.android.settings",
            "android-agent pkg com.android.chrome",
        ),
        min_args=1,
        max_args=1,
    ),
    CommandSpec(
        name="signature",
        aliases=("sig",),
        summary="Inspect APK signing schemes (V1/V2/V3/V3.1/V4) for one package",
        usage="android-agent signature <pkg>",
        positionals=(
            PosSpec("pkg", required=True, description="Exact Android package name"),
        ),
        examples=(
            "android-agent signature com.android.settings",
        ),
        min_args=1,
        max_args=1,
    ),
    CommandSpec(
        name="icon",
        summary="Download an installed package's PNG icon to a local file",
        usage="android-agent icon <pkg> [--out PATH]",
        positionals=(
            PosSpec("pkg", required=True, description="Exact Android package name"),
        ),
        flags=(
            FlagSpec("out", "--out", kind="str", default=None, value_name="PATH", description="Output PNG path (default: temp dir)"),
        ),
        renamed_flags={
            "--output": ("--out", "--output is not used; use --out PATH instead"),
        },
        examples=(
            "android-agent icon com.android.settings",
            "android-agent icon com.android.settings --out settings.png",
        ),
        min_args=1,
        max_args=1,
    ),
    CommandSpec(
        name="device",
        aliases=("device-info",),
        summary="Show compact device hardware/OS summary or drill down into one /device-info section",
        usage="android-agent device [--section NAME] [--query STR] [--limit 30] [--full]",
        flags=(
            FlagSpec("section", "--section", kind="str", default=None, value_name="NAME", description="Inspect one section: " + ",".join(DEVICE_SECTIONS), choices=DEVICE_SECTIONS),
            FlagSpec("query", "--query", kind="str", default=None, value_name="STR", description="Case-insensitive filter within the selected section"),
            FlagSpec("limit", "--limit", kind="int", default=30, value_name="N", description="Maximum rows in section view (default: 30)", min_val=1),
            FlagSpec("full", "--full", kind="bool", default=False, description="Show all entries in section view without limit"),
        ),
        examples=(
            "android-agent device",
            "android-agent device --section display",
            "android-agent device --section properties --query ro.build",
        ),
        min_args=0,
        max_args=0,
    ),
    CommandSpec(
        name="setup",
        summary="Install, repair, or remove SessionStart hooks/plugins for Claude Code, Codex, and OpenCode",
        usage="android-agent setup [--target claude|codex|opencode|all] [--scope user|project] [--dry-run] [--remove]",
        flags=(
            FlagSpec("target", "--target", kind="str", default="all", value_name="APP", description="Target harness: claude, codex, opencode, or all (default: all)", choices=("claude", "codex", "opencode", "all")),
            FlagSpec("scope", "--scope", kind="str", default="user", value_name="SCOPE", description="Install scope: user or project (default: user)", choices=("user", "project")),
            FlagSpec("dry_run", "--dry-run", kind="bool", default=False, description="Preview planned changes without writing files"),
            FlagSpec("remove", "--remove", kind="bool", default=False, description="Remove installed android-agent hooks/plugins"),
        ),
        examples=(
            "android-agent setup",
            "android-agent setup --target claude --scope project",
            "android-agent setup --dry-run",
            "android-agent setup --remove",
        ),
        min_args=0,
        max_args=0,
    ),
    CommandSpec(
        name="skill",
        summary="Generate, write, or drift-check skills/android-agent/SKILL.md from CLI spec",
        usage="android-agent skill [--write | --check] [--out PATH]",
        flags=(
            FlagSpec("write", "--write", kind="bool", default=False, description="Write generated SKILL.md to disk"),
            FlagSpec("check", "--check", kind="bool", default=False, description="Exit 0 if SKILL.md matches spec, else exit 1"),
            FlagSpec("out", "--out", kind="str", default=None, value_name="PATH", description="Override output/check path (default: skills/android-agent/SKILL.md)"),
        ),
        examples=(
            "android-agent skill --write",
            "android-agent skill --check",
        ),
        min_args=0,
        max_args=0,
    ),
)

COMMAND_MAP: dict[str, CommandSpec] = {}
for _cmd in COMMANDS:
    COMMAND_MAP[_cmd.name] = _cmd
    for _alias in _cmd.aliases:
        COMMAND_MAP[_alias] = _cmd


def resolve_command(name: str) -> CommandSpec | None:
    return COMMAND_MAP.get(name)


def valid_flags_summary(spec: CommandSpec | None) -> str:
    global_names = ", ".join(f.long for f in GLOBAL_FLAGS)
    if spec is None:
        home_names = ", ".join(f.long for f in HOME_FLAGS)
        return f"{home_names}, --version ({global_names} always allowed)"
    if not spec.flags:
        return f"no command-specific flags ({global_names} always allowed)"
    parts: list[str] = []
    for f in spec.flags:
        parts.append(f.long)
        if f.negated:
            parts.append(f.negated)
    return f"{', '.join(parts)} ({global_names} always allowed)"


def render_command_help(spec: CommandSpec) -> str:
    lines: list[str] = [f"{TOOL_NAME} {spec.name} - {spec.summary}"]
    if spec.aliases:
        lines.append(f"Aliases: {', '.join(spec.aliases)}")
    lines.append(f"Usage: {spec.usage}")
    if spec.positionals:
        lines.append("")
        lines.append("Arguments:")
        for pos in spec.positionals:
            req = "required" if pos.required else "optional"
            lines.append(f"  {pos.name:<16} ({req}) {pos.description}")
    if spec.flags:
        lines.append("")
        lines.append("Flags:")
        for fl in spec.flags:
            lines.append(f"  {fl.display_names():<26} {fl.description}")
    lines.append("")
    lines.append("Global flags:")
    for fl in GLOBAL_FLAGS:
        lines.append(f"  {fl.display_names():<26} {fl.description}")
    if spec.examples:
        lines.append("")
        lines.append("Examples:")
        for ex in spec.examples:
            lines.append(f"  {ex}")
    return "\n".join(lines) + "\n"


def render_top_level_help() -> str:
    lines: list[str] = [
        f"{TOOL_NAME} - {TOOL_DESCRIPTION}",
        "",
        "Usage:",
        f"  {TOOL_NAME} [--hook] [global-flags]",
        f"  {TOOL_NAME} <command> [args] [flags]",
        f"  {TOOL_NAME} -v | -V | --version",
        "",
        "Commands:",
    ]
    for cmd in COMMANDS:
        alias_str = f" ({', '.join(cmd.aliases)})" if cmd.aliases else ""
        label = f"{cmd.name}{alias_str}"
        lines.append(f"  {label:<24} {cmd.summary}")
    lines.append("")
    lines.append("Global flags:")
    for fl in GLOBAL_FLAGS:
        lines.append(f"  {fl.display_names():<26} {fl.description}")
    lines.append("")
    lines.append(f"Run `{TOOL_NAME} <command> --help` for command-specific flags and examples.")
    return "\n".join(lines) + "\n"


def render_skill_md() -> str:
    lines: list[str] = [
        "---",
        f"name: {TOOL_NAME}",
        "description: >",
        f"  {SKILL_TRIGGER_DESCRIPTION}",
        "---",
        "",
        f"# {TOOL_NAME}",
        "",
        f"{TOOL_DESCRIPTION}.",
        "",
        "Output is TOON on stdout (data and errors). Exit codes are 0 for success or no-op, 1 for runtime error, and 2 for usage error.",
        "",
        "## Invocation",
        "",
        "Run non-interactively with flags only. Use the installed binary when on PATH, or invoke the script directly from a repo checkout:",
        "",
        "```sh",
        f"{TOOL_NAME} status",
        "python cli/android_agent.py status",
        "```",
        "",
        "## Global Flags",
        "",
        "| Flag | Description |",
        "|------|-------------|",
    ]
    for fl in GLOBAL_FLAGS:
        lines.append(f"| `{fl.display_names()}` | {fl.description} |")

    lines.extend([
        "",
        "## Commands",
        "",
    ])

    for cmd in COMMANDS:
        alias_suffix = f" (aliases: `{'`, `'.join(cmd.aliases)}`)" if cmd.aliases else ""
        lines.append(f"### `{cmd.name}`{alias_suffix}")
        lines.append("")
        lines.append(f"{cmd.summary}.")
        lines.append("")
        lines.append(f"Usage: `{cmd.usage}`")
        if cmd.flags:
            lines.append("")
            for fl in cmd.flags:
                lines.append(f"- `{fl.display_names()}`: {fl.description}")
        if cmd.examples:
            lines.append("")
            lines.append("```sh")
            for ex in cmd.examples:
                lines.append(ex)
            lines.append("```")
        lines.append("")

    lines.extend([
        "## Notes",
        "",
        "- `start` and `stop` are idempotent and exit 0 when already in the target state.",
        "- `screen` defaults to `--fast --format jpeg --quality 80 --scale 0.5` and saves the image to a file; tap and swipe coordinates always use native screen pixels.",
        "- `tap --text/--id/--query` finds and taps a UI element over one Keep-Alive connection, walking up to clickable ancestors and returning an inline candidate table on ambiguous matches.",
        "- `ui` defaults to interactive nodes with container label hoisting; pass `--all` for every node or `--xml` for raw hierarchy XML.",
        "- Run `android-agent setup` to install ambient `SessionStart` hooks for Claude Code, Codex, and OpenCode.",
    ])
    return "\n".join(lines) + "\n"
