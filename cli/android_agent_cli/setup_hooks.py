from __future__ import annotations

import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import RuntimeAxiError

MANAGED_MARKER = "// Managed by android-agent setup"


@dataclass(frozen=True)
class HookCommand:
    shell_cmd: str
    argv: tuple[str, ...]


def _quote_arg(value: str) -> str:
    if not value or any(ch in value for ch in (' ', '\t', '"', "'")):
        escaped = value.replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _same_executable(path_a: str, path_b: str) -> bool:
    try:
        ra = os.path.normcase(os.path.realpath(path_a))
        rb = os.path.normcase(os.path.realpath(path_b))
    except OSError:
        return False
    if ra == rb:
        return True
    if os.name == "nt":
        if ra.endswith(".exe") and ra[:-4] == rb:
            return True
        if rb.endswith(".exe") and rb[:-4] == ra:
            return True
    return False


def resolve_hook_command(
    which_bin: str | None = None,
    argv0: str | None = None,
    python_exe: str | None = None,
) -> HookCommand:
    resolved_which = shutil.which("android-agent") if which_bin is None else which_bin
    current_argv0 = sys.argv[0] if argv0 is None else argv0
    py_exe = sys.executable if python_exe is None else python_exe

    if resolved_which and current_argv0 and _same_executable(resolved_which, current_argv0):
        return HookCommand(shell_cmd="android-agent --hook", argv=("android-agent", "--hook"))

    if current_argv0:
        abs_argv0 = os.path.abspath(current_argv0)
        if abs_argv0.lower().endswith(".py") and os.path.isfile(abs_argv0):
            abs_py = os.path.abspath(py_exe)
            cmd = f"{_quote_arg(abs_py)} {_quote_arg(abs_argv0)} --hook"
            return HookCommand(shell_cmd=cmd, argv=(abs_py, abs_argv0, "--hook"))
        if os.path.isfile(abs_argv0):
            cmd = f"{_quote_arg(abs_argv0)} --hook"
            return HookCommand(shell_cmd=cmd, argv=(abs_argv0, "--hook"))

    script_fallback = str((Path(__file__).resolve().parent.parent / "android_agent.py").resolve())
    abs_py = os.path.abspath(py_exe)
    cmd = f"{_quote_arg(abs_py)} {_quote_arg(script_fallback)} --hook"
    return HookCommand(shell_cmd=cmd, argv=(abs_py, script_fallback, "--hook"))


def _is_android_agent_hook_command(command: str) -> bool:
    lower = command.lower()
    return "--hook" in lower and ("android-agent" in lower or "android_agent.py" in lower)


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeAxiError(
            f"cannot parse JSON in {path}: {exc.msg}",
            help_hints=[f"Fix invalid JSON in {path} and re-run `android-agent setup`"],
        ) from exc
    if not isinstance(data, dict):
        raise RuntimeAxiError(f"expected JSON object in {path}")
    return data


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    with open(tmp_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)
    os.replace(tmp_path, path)


def _update_json_session_hook(
    path: Path,
    hook_cmd: HookCommand,
    *,
    dry_run: bool,
    remove: bool,
) -> tuple[str, str]:
    data = _read_json_object(path)
    hooks = data.get("hooks")
    if hooks is not None and not isinstance(hooks, dict):
        raise RuntimeAxiError(f"unexpected `hooks` shape in {path}")

    session_start = hooks.get("SessionStart") if isinstance(hooks, dict) else None
    if session_start is not None and not isinstance(session_start, list):
        raise RuntimeAxiError(f"unexpected `hooks.SessionStart` shape in {path}")

    existing_list: list[Any] = list(session_start) if isinstance(session_start, list) else []

    if remove:
        removed_any = False
        new_list: list[Any] = []
        for entry in existing_list:
            if isinstance(entry, dict) and isinstance(entry.get("hooks"), list):
                kept_inner = [
                    h
                    for h in entry["hooks"]
                    if not (
                        isinstance(h, dict)
                        and isinstance(h.get("command"), str)
                        and _is_android_agent_hook_command(h["command"])
                    )
                ]
                if len(kept_inner) != len(entry["hooks"]):
                    removed_any = True
                if kept_inner:
                    updated_entry = dict(entry)
                    updated_entry["hooks"] = kept_inner
                    new_list.append(updated_entry)
            elif isinstance(entry, dict) and isinstance(entry.get("command"), str) and _is_android_agent_hook_command(entry["command"]):
                removed_any = True
            else:
                new_list.append(entry)

        if not removed_any:
            return ("not installed (no-op)", "no android-agent hook present")
        if not dry_run:
            updated = dict(data)
            updated_hooks = dict(hooks or {})
            if new_list:
                updated_hooks["SessionStart"] = new_list
            else:
                updated_hooks.pop("SessionStart", None)
            if updated_hooks:
                updated["hooks"] = updated_hooks
            else:
                updated.pop("hooks", None)
            _write_text_atomic(path, json.dumps(updated, indent=2) + "\n")
        return ("would remove" if dry_run else "removed", "removed SessionStart hook")

    found_same = False
    repaired = False
    new_list = []
    for entry in existing_list:
        if isinstance(entry, dict) and isinstance(entry.get("hooks"), list):
            new_inner = []
            for h in entry["hooks"]:
                if isinstance(h, dict) and isinstance(h.get("command"), str) and _is_android_agent_hook_command(h["command"]):
                    if h["command"] == hook_cmd.shell_cmd:
                        found_same = True
                        new_inner.append(h)
                    else:
                        repaired = True
                        updated_h = dict(h)
                        updated_h["command"] = hook_cmd.shell_cmd
                        new_inner.append(updated_h)
                else:
                    new_inner.append(h)
            updated_entry = dict(entry)
            updated_entry["hooks"] = new_inner
            new_list.append(updated_entry)
        elif isinstance(entry, dict) and isinstance(entry.get("command"), str) and _is_android_agent_hook_command(entry["command"]):
            if entry["command"] == hook_cmd.shell_cmd:
                found_same = True
                new_list.append(entry)
            else:
                repaired = True
                updated_entry = dict(entry)
                updated_entry["command"] = hook_cmd.shell_cmd
                new_list.append(updated_entry)
        else:
            new_list.append(entry)

    if found_same and not repaired:
        return ("unchanged (no-op)", hook_cmd.shell_cmd)

    if not found_same and not repaired:
        new_list.append(
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": hook_cmd.shell_cmd,
                    }
                ]
            }
        )
        status = "would install" if dry_run else "installed"
    else:
        status = "would repair" if dry_run else "repaired"

    if not dry_run:
        updated = dict(data)
        updated_hooks = dict(hooks or {})
        updated_hooks["SessionStart"] = new_list
        updated["hooks"] = updated_hooks
        _write_text_atomic(path, json.dumps(updated, indent=2) + "\n")
    return (status, hook_cmd.shell_cmd)


_SECTION_HEADER_RE = re.compile(r"^\s*\[([^\]]+)\]\s*(?:#.*)?$")
_HOOKS_KV_RE = re.compile(r"^(\s*hooks\s*=\s*)(\S+)(.*)$")


def ensure_codex_features_hooks(toml_text: str) -> tuple[str, bool]:
    lines = toml_text.splitlines()
    in_features = False
    features_found = False
    insert_at: int | None = None

    for idx, line in enumerate(lines):
        sec_match = _SECTION_HEADER_RE.match(line)
        if sec_match:
            sec_name = sec_match.group(1).strip()
            if in_features and insert_at is None:
                insert_at = idx
            in_features = sec_name == "features"
            if in_features:
                features_found = True
            continue
        if in_features:
            kv_match = _HOOKS_KV_RE.match(line)
            if kv_match:
                current_val = kv_match.group(2).strip()
                if current_val == "true":
                    return (toml_text if toml_text.endswith("\n") else toml_text + "\n"), False
                lines[idx] = f"{kv_match.group(1)}true{kv_match.group(3)}"
                return "\n".join(lines) + "\n", True

    if features_found:
        pos = len(lines) if insert_at is None else insert_at
        while pos > 0 and not lines[pos - 1].strip():
            pos -= 1
        lines.insert(pos, "hooks = true")
        return "\n".join(lines) + "\n", True

    if not lines or (len(lines) == 1 and not lines[0].strip()):
        return "[features]\nhooks = true\n", True
    if lines[-1].strip():
        lines.append("")
    lines.extend(["[features]", "hooks = true"])
    return "\n".join(lines) + "\n", True


def _render_opencode_plugin(hook_cmd: HookCommand) -> str:
    bin_json = json.dumps(hook_cmd.argv[0])
    args_json = json.dumps(list(hook_cmd.argv[1:]))
    return (
        f"{MANAGED_MARKER}\n"
        'import { execFileSync } from "node:child_process";\n\n'
        "export const AndroidAgentPlugin = async () => ({\n"
        '  "experimental.chat.system.transform": async (_input, output) => {\n'
        "    try {\n"
        f"      const ctx = execFileSync({bin_json}, {args_json}, {{\n"
        '        encoding: "utf8",\n'
        "        timeout: 2500,\n"
        '        stdio: ["ignore", "pipe", "ignore"],\n'
        "      }).trim();\n"
        "      if (ctx) {\n"
        "        output.system.push(ctx);\n"
        "      }\n"
        "    } catch {\n"
        "      // Session context hook must never break chat startup.\n"
        "    }\n"
        "  },\n"
        "});\n"
    )


def _setup_opencode(
    path: Path,
    hook_cmd: HookCommand,
    *,
    dry_run: bool,
    remove: bool,
) -> tuple[str, str]:
    if remove:
        if not path.exists():
            return ("not installed (no-op)", "plugin file absent")
        if not dry_run:
            path.unlink()
        return ("would remove" if dry_run else "removed", "removed plugin file")

    desired = _render_opencode_plugin(hook_cmd)
    if path.exists():
        current = path.read_text(encoding="utf-8")
        if current == desired:
            return ("unchanged (no-op)", hook_cmd.shell_cmd)
        status = "would repair" if dry_run else "repaired"
    else:
        status = "would install" if dry_run else "installed"
    if not dry_run:
        _write_text_atomic(path, desired)
    return (status, hook_cmd.shell_cmd)


def run_setup(
    *,
    target: str = "all",
    scope: str = "user",
    dry_run: bool = False,
    remove: bool = False,
    home_dir: Path | None = None,
    project_dir: Path | None = None,
    hook_cmd: HookCommand | None = None,
) -> dict[str, Any]:
    home = Path.home() if home_dir is None else home_dir
    proj = Path.cwd() if project_dir is None else project_dir
    cmd = resolve_hook_command() if hook_cmd is None else hook_cmd

    targets = ("claude", "codex", "opencode") if target == "all" else (target,)
    results: list[dict[str, str]] = []

    for app in targets:
        if app == "claude":
            cfg_path = (home / ".claude" / "settings.json") if scope == "user" else (proj / ".claude" / "settings.json")
            status, detail = _update_json_session_hook(cfg_path, cmd, dry_run=dry_run, remove=remove)
            results.append({"target": "claude", "path": str(cfg_path), "status": status, "detail": detail})
        elif app == "codex":
            base = (home / ".codex") if scope == "user" else (proj / ".codex")
            hooks_path = base / "hooks.json"
            toml_path = base / "config.toml"
            status, detail = _update_json_session_hook(hooks_path, cmd, dry_run=dry_run, remove=remove)
            results.append({"target": "codex", "path": str(hooks_path), "status": status, "detail": detail})
            if not remove:
                existing_toml = toml_path.read_text(encoding="utf-8") if toml_path.exists() else ""
                updated_toml, changed = ensure_codex_features_hooks(existing_toml)
                if changed:
                    if not dry_run:
                        _write_text_atomic(toml_path, updated_toml)
                    t_status = "would update" if dry_run else "updated"
                else:
                    t_status = "unchanged (no-op)"
                results.append(
                    {
                        "target": "codex-config",
                        "path": str(toml_path),
                        "status": t_status,
                        "detail": "[features] hooks = true",
                    }
                )
        elif app == "opencode":
            plugin_path = (
                (home / ".config" / "opencode" / "plugins" / "android-agent.js")
                if scope == "user"
                else (proj / ".opencode" / "plugins" / "android-agent.js")
            )
            status, detail = _setup_opencode(plugin_path, cmd, dry_run=dry_run, remove=remove)
            results.append({"target": "opencode", "path": str(plugin_path), "status": status, "detail": detail})

    return {
        "scope": scope,
        "dry_run": dry_run,
        "command": cmd.shell_cmd,
        "results": results,
    }
