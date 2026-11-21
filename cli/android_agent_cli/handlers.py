from __future__ import annotations

import json
import math
import os
import re
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from . import adb
from .client import AgentClient
from .errors import RuntimeAxiError, UsageError
from .parser import ParsedInvocation
from .setup_hooks import run_setup
from .spec import (
    DEVICE_SECTIONS,
    GLOBAL_ACTIONS,
    KEY_ALIASES,
    PACKAGE_ALL_FIELDS,
    PACKAGE_DEFAULT_FIELDS,
    SWIPE_DIRECTIONS,
    TOOL_DESCRIPTION,
    TOOL_NAME,
    UI_ALL_FIELDS,
    UI_DEFAULT_FIELDS,
    render_skill_md,
)
from .toon import truncate_text
from .ui_parser import UiNode, filter_ui_nodes, node_matches, parse_hierarchy_xml, resolve_tap_targets

_KEYCODE_NAME_RE = re.compile(r"KEYCODE_[A-Z0-9_]+")
_XML_TRUNCATE_LIMIT = 2000


def _cli_prefix(globals_dict: dict[str, Any]) -> str:
    parts = [TOOL_NAME]
    serial = globals_dict.get("serial")
    port = globals_dict.get("port", 9500)
    host = globals_dict.get("host")
    if serial:
        parts.extend(["-s", str(serial)])
    if port != 9500:
        parts.extend(["-p", str(port)])
    if host:
        parts.extend(["--host", str(host)])
    return " ".join(parts)


def _endpoint(globals_dict: dict[str, Any], create: bool = True) -> tuple[str, int | None, str | None]:
    """(host, host_port, serial) to reach the agent; host_port is None when no forward exists and create=False."""
    host = globals_dict.get("host")
    if host:
        return host, globals_dict["port"], globals_dict.get("serial")
    cached = globals_dict.get("_endpoint")
    if cached is not None and (cached[1] is not None or not create):
        return cached
    serial = adb.resolve_serial(globals_dict.get("serial"))
    device_port: int = globals_dict["port"]
    local = adb.ensure_forward(serial, device_port) if create else adb.find_forward(serial, device_port)
    resolved = ("127.0.0.1", local, serial)
    globals_dict["_endpoint"] = resolved
    return resolved


def _endpoint_label(globals_dict: dict[str, Any]) -> str:
    host, local, serial = _endpoint(globals_dict, create=False)
    if globals_dict.get("host"):
        return f"http://{host}:{local}"
    device_port = globals_dict["port"]
    via = f"127.0.0.1:{local} -> " if local is not None else ""
    return f"{via}{serial} tcp:{device_port}"


def _collapse_home(path_str: str) -> str:
    try:
        resolved = Path(path_str).resolve()
        home = Path.home().resolve()
        rel = resolved.relative_to(home)
        return "~/" + rel.as_posix()
    except Exception:
        return Path(path_str).as_posix()


def _gesture_timeout(gesture_ms: int) -> float:
    # The request must outlive the gesture: timing out while the agent still holds the
    # pointer reports failure for a gesture that lands, inviting a duplicate retry.
    return max(15.0, gesture_ms / 1000.0 + 10.0)


def _batch_duration_ms(actions: list[dict[str, Any]]) -> int:
    # Mirrors the agent's defaults (InputController: swipe 300 ms, key interval applied
    # between codes) so the request outlives the batch.
    def num(action: dict[str, Any], keys: tuple[str, ...], default: int) -> int:
        for key in keys:
            value = action.get(key)
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                return int(value)
            if isinstance(value, str):
                try:
                    return int(float(value))
                except ValueError:
                    continue
        return default

    total = 0
    for action in actions:
        kind = str(action.get("type", "")).lower()
        if kind == "swipe":
            total += num(action, ("durationMs", "duration"), 300) + num(action, ("holdMs", "hold"), 0)
        elif kind in ("tap", "wait"):
            total += num(action, ("durationMs", "duration", "ms"), 0)
        elif kind == "key":
            codes = action.get("keyCodes") or action.get("keycodes") or action.get("codes") or [None]
            count = len(codes) if isinstance(codes, list) else 1
            total += num(action, ("intervalMs", "interval"), 0) * max(0, count - 1)
    return total


def _make_client(globals_dict: dict[str, Any], timeout: float = 15.0) -> AgentClient:
    host, port, serial = _endpoint(globals_dict)
    return AgentClient(
        host=host,
        port=port if port is not None else globals_dict["port"],
        timeout=timeout,
        serial=serial,
        cli_prefix=_cli_prefix(globals_dict),
    )


def _parse_fields(raw_csv: str | None, default_fields: tuple[str, ...], allowed_fields: tuple[str, ...]) -> tuple[str, ...]:
    if not raw_csv:
        return default_fields
    requested = [part.strip() for part in raw_csv.split(",") if part.strip()]
    if not requested:
        raise UsageError(
            "--fields cannot be empty",
            help_hints=[f"Allowed fields: {', '.join(allowed_fields)}"],
        )
    allowed_set = set(allowed_fields)
    invalid = [f for f in requested if f not in allowed_set]
    if invalid:
        raise UsageError(
            f"unknown field(s): {', '.join(invalid)}",
            help_hints=[f"Allowed fields: {', '.join(allowed_fields)}"],
        )
    return tuple(requested)


def _temp_output_dir() -> Path:
    out_dir = Path(tempfile.gettempdir()) / TOOL_NAME
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def handle_home(inv: ParsedInvocation) -> dict[str, Any]:
    is_hook = bool(inv.flags.get("hook", False))
    prefix = _cli_prefix(inv.globals)
    bin_path = _collapse_home(sys.argv[0] if sys.argv else TOOL_NAME)

    devices: list[adb.AdbDevice] = []
    adb_error: str | None = None
    try:
        devices = adb.list_devices(timeout=1.5)
    except RuntimeAxiError as exc:
        adb_error = exc.message

    # Per device, from existing forwards only: a session-start hook must neither create
    # forwards nor report whichever phone a shared 127.0.0.1:9500 happened to reach.
    shown = devices[: (4 if is_hook else 16)]
    rows = [_device_agent_row(d, inv.globals["port"]) for d in shown]
    agent_running = any(r.get("agent") == "running" for r in rows)

    payload: dict[str, Any] = {
        "bin": bin_path,
        "description": TOOL_DESCRIPTION,
    }
    if inv.globals.get("host"):
        with _make_client(inv.globals, timeout=0.3) as client:
            agent_running = client.probe_health(timeout=0.3)
        payload["agent"] = f"{'running' if agent_running else 'stopped'} ({_endpoint_label(inv.globals)})"

    if adb_error:
        payload["adb"] = adb_error
    elif devices:
        payload["devices"] = rows
    else:
        payload["devices"] = "0 ADB devices connected"

    hints: list[str] = []
    if agent_running:
        hints.append(f"Run `{prefix} screen` or `{prefix} ui` to inspect the screen")
        hints.append(f"Run `{prefix} tap --text \"<label>\"` or `{prefix} swipe up` to interact")
        if not is_hook:
            hints.append(f"Run `{prefix} device` or `{prefix} packages --user-only` for device and app data")
    elif any(d.state == "device" for d in devices):
        hints.append(f"Run `{prefix} start` to launch AndroidAgent on the connected device")
    elif devices:
        states = ", ".join(sorted({d.state for d in devices}))
        hints.append(f"No device is ready ({states}); run `{prefix} devices`, then authorize or reconnect it")
        if not is_hook:
            hints.append(f"Run `{prefix} devices` to inspect connected devices")
    else:
        hints.append(f"Connect an Android device with USB debugging enabled, then run `{prefix} start`")

    payload["help"] = hints
    return payload


def handle_devices(inv: ParsedInvocation) -> dict[str, Any]:
    prefix = _cli_prefix(inv.globals)
    devices = adb.list_devices()
    if not devices:
        return {
            "devices": "0 ADB devices connected",
            "help": f"Connect an Android device with USB debugging enabled, then run `{prefix} devices`",
        }
    return {
        "count": f"{len(devices)} connected",
        "devices": [d.to_row() for d in devices],
        "help": f"Run `{prefix} start` to launch AndroidAgent",
    }


def handle_start(inv: ParsedInvocation) -> dict[str, Any]:
    prefix = _cli_prefix(inv.globals)
    timeout: float = inv.flags["timeout"]
    restart: bool = inv.flags["restart"]
    jar_override: str | None = inv.flags["jar"]

    # Probe the target device's own forward: the old global 127.0.0.1:9500 could belong to
    # another phone, which made `-s B start` report "already running" for device A.
    with _make_client(inv.globals, timeout=1.0) as client:
        already_up = client.probe_health(timeout=0.5)
        if already_up and not restart:
            info = client.get_info()
            return {
                "status": "already running (no-op)",
                "endpoint": _endpoint_label(inv.globals),
                "device": f"{info.get('brand', '')} {info.get('model', '')}".strip(),
                "android": f"{info.get('release', '')} (SDK {info.get('sdk', '')})",
                "agentVersion": info.get("agentVersion", ""),
            }
        if already_up and restart:
            try:
                client.stop_server()
            except RuntimeAxiError:
                pass
            time.sleep(0.3)

    if inv.globals.get("host"):
        raise RuntimeAxiError(
            f"no agent answers at {_endpoint_label(inv.globals)}",
            help_hints=["`start` deploys over adb; drop --host to let it push and forward the agent"],
        )
    _, _, serial = _endpoint(inv.globals)
    assert serial is not None
    jar_path = adb.discover_jar(jar_override)
    pushed = adb.push_jar(serial, jar_path)
    adb.launch_server_detached(serial, port=inv.globals["port"])

    deadline = time.monotonic() + timeout
    with _make_client(inv.globals, timeout=1.0) as client:
        while time.monotonic() < deadline:
            if client.probe_health(timeout=0.4):
                info = client.get_info()
                return {
                    "status": "restarted" if restart else "started",
                    "serial": serial,
                    "endpoint": _endpoint_label(inv.globals),
                    "jar": str(jar_path),
                    "jar_pushed": pushed,
                    "device": f"{info.get('brand', '')} {info.get('model', '')}".strip(),
                    "android": f"{info.get('release', '')} (SDK {info.get('sdk', '')})",
                    "agentVersion": info.get("agentVersion", ""),
                    "help": [
                        f"Run `{prefix} screen` to capture a screenshot",
                        f"Run `{prefix} ui` to inspect interactive UI elements",
                    ],
                }
            time.sleep(0.25)

    log_text = adb.read_remote_log(serial)
    raise adb.translate_startup_log_error(log_text, prefix)


def handle_stop(inv: ParsedInvocation) -> dict[str, Any]:
    stopped = False
    with _make_client(inv.globals, timeout=1.5) as client:
        if client.probe_health(timeout=0.4):
            try:
                client.stop_server()
            except RuntimeAxiError:
                pass
            stopped = True
    label = _endpoint_label(inv.globals)

    if not inv.globals.get("host"):
        _, local, serial = _endpoint(inv.globals, create=False)
        if local is not None:
            try:
                adb.remove_forward(serial, local)
            except RuntimeAxiError:
                pass

    return {
        "status": "stopped" if stopped else "already stopped (no-op)",
        "endpoint": label,
    }


def _device_agent_row(device: adb.AdbDevice, device_port: int) -> dict[str, Any]:
    # One flaky device must not hide the others' rows or fail the session hook.
    row = device.to_row()
    if device.state != "device":
        return row
    try:
        running = adb.agent_process_running(device.serial)
        row["agent"] = "running" if running else "stopped"
        if running:
            local = adb.find_forward(device.serial, device_port)
            if local is not None:
                with AgentClient(host="127.0.0.1", port=local, timeout=1.0, serial=device.serial) as client:
                    if client.probe_health(timeout=0.5):
                        row["agentVersion"] = client.get_info().get("agentVersion", "")
    except RuntimeAxiError as exc:
        row["agent"] = f"unknown ({exc.message})"
    return row


def handle_status(inv: ParsedInvocation) -> dict[str, Any]:
    prefix = _cli_prefix(inv.globals)

    if inv.globals.get("host"):
        payload: dict[str, Any] = {"endpoint": _endpoint_label(inv.globals)}
        with _make_client(inv.globals, timeout=1.5) as client:
            if client.probe_health(timeout=0.5):
                info = client.get_info()
                payload.update(
                    status="running",
                    brand=info.get("brand", ""),
                    model=info.get("model", ""),
                    sdk=info.get("sdk", 0),
                    release=info.get("release", ""),
                    agentVersion=info.get("agentVersion", ""),
                )
            else:
                payload["status"] = "stopped"
        return payload

    # Per device: one shared 127.0.0.1:9500 used to report whichever phone it pointed at.
    # Looks only at existing forwards, so a session-start hook never creates any.
    try:
        devices = adb.list_devices(timeout=2.0)
    except RuntimeAxiError as exc:
        return {"adb": exc.message}
    if not devices:
        return {"devices": "0 ADB devices connected"}
    serial_filter = inv.globals.get("serial")
    rows = [_device_agent_row(d, inv.globals["port"]) for d in devices if not serial_filter or d.serial == serial_filter]
    payload = {"devices": rows}
    if any(r.get("agent") == "stopped" for r in rows):
        payload["help"] = f"Run `{prefix} start` (add -s SERIAL when several devices are connected) to launch AndroidAgent"
    return payload


def _parse_crop_arg(raw: str | None) -> str | None:
    if raw is None:
        return None
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != 4 or any(not p for p in parts):
        raise UsageError(
            f"invalid --crop format: {raw}",
            help_hints=["Use `--crop X,Y,W,H` with 4 integers (x>=0, y>=0, w>0, h>0)"],
        )
    try:
        x, y, w, h = (int(p) for p in parts)
    except ValueError as exc:
        raise UsageError(
            f"invalid --crop integers: {raw}",
            help_hints=["Use `--crop X,Y,W,H` with 4 integers (x>=0, y>=0, w>0, h>0)"],
        ) from exc
    if x < 0 or y < 0 or w <= 0 or h <= 0:
        raise UsageError(
            f"invalid --crop bounds: {raw} (require x>=0, y>=0, w>0, h>0)",
            help_hints=["Use `--crop X,Y,W,H` with non-negative x,y and positive w,h"],
        )
    return f"{x},{y},{w},{h}"


def handle_screen(inv: ParsedInvocation) -> dict[str, Any]:
    fmt = inv.flags["format"]
    if fmt == "jpg":
        fmt = "jpeg"
    quality = inv.flags["quality"]
    if quality is None:
        quality = 80 if fmt == "jpeg" else 100
    scale: float = inv.flags["scale"]
    fast: bool = inv.flags["fast"]
    crop = _parse_crop_arg(inv.flags["crop"])
    out_flag: str | None = inv.flags["out"]

    ext = "jpg" if fmt == "jpeg" else fmt
    if out_flag:
        out_path = Path(out_flag).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
        out_path = _temp_output_dir() / f"screen-{ts}.{ext}"

    with _make_client(inv.globals, timeout=20.0) as client:
        res = client.get_screenshot(fmt=fmt, quality=quality, scale=scale, fast=fast, crop=crop)

    out_path.write_bytes(res.body)
    width = int(res.headers.get("x-width", "0") or 0)
    height = int(res.headers.get("x-height", "0") or 0)
    resp_fmt = res.headers.get("x-format", fmt)
    source = res.headers.get("x-capture-source", "unknown")
    cache_hit = res.headers.get("x-cache-hit", "").strip().lower() == "true"

    payload: dict[str, Any] = {
        "path": str(out_path),
        "width": width,
        "height": height,
        "scale": scale,
        "format": resp_fmt,
        "source": source,
        "bytes": len(res.body),
    }
    if crop is not None:
        payload["crop"] = crop
    if cache_hit:
        payload["cache_hit"] = True
    if crop is not None:
        crop_x, crop_y = (int(v) for v in crop.split(",")[:2])
        if scale < 1.0:
            payload["coords_note"] = (
                f"Image cropped at ({crop_x},{crop_y}) after scaling by {scale:g}; "
                f"convert pixel (px,py) to native via ((px+{crop_x})/{scale:g}, (py+{crop_y})/{scale:g})"
            )
        else:
            payload["coords_note"] = (
                f"Image cropped at ({crop_x},{crop_y}); "
                f"add ({crop_x},{crop_y}) to pixel coordinates for native tap/swipe"
            )
    elif scale < 1.0:
        native_hint = ""
        if width > 0 and height > 0:
            est_w = round(width / scale)
            est_h = round(height / scale)
            native_hint = f" (approx {est_w}x{est_h} native)"
        payload["coords_note"] = (
            f"Image scaled by {scale:g}; tap/swipe coordinates must be in native screen pixels{native_hint}"
        )
    return payload


def handle_ui(inv: ParsedInvocation) -> dict[str, Any]:
    prefix = _cli_prefix(inv.globals)
    fields = _parse_fields(inv.flags["fields"], UI_DEFAULT_FIELDS, UI_ALL_FIELDS)
    compressed = not inv.flags["uncompressed"]
    as_xml: bool = inv.flags["xml"]
    full: bool = inv.flags["full"]
    limit: int = inv.flags["limit"]

    with _make_client(inv.globals, timeout=15.0) as client:
        xml_text = client.get_hierarchy(compressed=compressed)

    if as_xml:
        if full:
            return {"xml": xml_text, "chars": len(xml_text)}
        preview, was_truncated, total = truncate_text(xml_text, _XML_TRUNCATE_LIMIT)
        payload: dict[str, Any] = {"xml": preview, "chars": total}
        if was_truncated:
            payload["help"] = f"Run `{prefix} ui --xml --full` to see complete XML ({total} chars)"
        return payload

    snapshot = parse_hierarchy_xml(xml_text)
    total_nodes = len(snapshot.nodes)
    interactive_count = len(snapshot.interactive_nodes())

    matched = filter_ui_nodes(
        snapshot,
        query=inv.flags["query"],
        text=inv.flags["text"],
        res_id=inv.flags["id"],
        clickable=inv.flags["clickable"],
        scrollable=inv.flags["scrollable"],
        include_all=inv.flags["all"],
    )

    if not matched:
        filter_parts = []
        if inv.flags["query"]:
            filter_parts.append(f"query='{inv.flags['query']}'")
        if inv.flags["text"]:
            filter_parts.append(f"text='{inv.flags['text']}'")
        if inv.flags["id"]:
            filter_parts.append(f"id='{inv.flags['id']}'")
        if inv.flags["clickable"]:
            filter_parts.append("clickable")
        if inv.flags["scrollable"]:
            filter_parts.append("scrollable")
        ctx = f" matching {', '.join(filter_parts)}" if filter_parts else ""
        return {
            "nodes": f"0 UI elements found{ctx} ({interactive_count} interactive, {total_nodes} total)",
            "help": f"Run `{prefix} ui --all` or `{prefix} screen` to inspect the current screen",
        }

    shown = matched if full else matched[:limit]
    truncated = len(shown) < len(matched)
    rows = [node.to_row(idx, fields) for idx, node in enumerate(shown)]

    result: dict[str, Any] = {
        "rotation": snapshot.rotation,
        "count": f"{len(shown)} of {len(matched)} matched ({interactive_count} interactive, {total_nodes} total)"
        if any(inv.flags[k] for k in ("query", "text", "id", "clickable", "scrollable", "all"))
        else f"{len(shown)} of {interactive_count} interactive ({total_nodes} total)",
        "nodes": rows,
    }
    hints: list[str] = []
    if truncated:
        hints.append(f"Run `{prefix} ui --full` to list all {len(matched)} matching elements")
    hints.append(f"Run `{prefix} tap <cx> <cy>` or `{prefix} tap --text \"<label>\"` to tap an element")
    result["help"] = hints
    return result


def _parse_finite_float(raw: str, name: str) -> float:
    try:
        val = float(raw)
    except ValueError as exc:
        raise UsageError(f"invalid number for {name}: {raw}") from exc
    if not math.isfinite(val):
        raise UsageError(f"non-finite number for {name}: {raw}")
    return val


def handle_tap(inv: ParsedInvocation) -> dict[str, Any]:
    prefix = _cli_prefix(inv.globals)
    pos = inv.positionals
    text_sel: str | None = inv.flags["text"]
    id_sel: str | None = inv.flags["id"]
    query_sel: str | None = inv.flags["query"]
    exact: bool = inv.flags["exact"]
    index: int | None = inv.flags["index"]
    duration: int = inv.flags["duration"]
    sync: bool = not inv.flags["no_sync"]

    has_selector = any(v is not None for v in (text_sel, id_sel, query_sel))
    if pos and has_selector:
        raise UsageError(
            "cannot combine positional coordinates with --text/--id/--query",
            help_hints=[
                f"Use `{prefix} tap <x> <y>` OR `{prefix} tap --text \"<label>\"`",
            ],
        )
    if not pos and not has_selector:
        raise UsageError(
            "tap requires <x> <y> coordinates or a selector (--text, --id, --query)",
            help_hints=[
                f"Use `{prefix} tap 540 1200` or `{prefix} tap --text \"Settings\"`",
            ],
        )
    if pos and len(pos) != 2:
        raise UsageError(
            f"tap coordinates require 2 numbers (<x> <y>), got {len(pos)}",
            help_hints=[f"Use `{prefix} tap <x> <y>`"],
        )

    if pos:
        if exact or index is not None:
            raise UsageError(
                "--exact and --index are only valid with --text/--id/--query",
                help_hints=[f"Use `{prefix} tap <x> <y>` without selector flags"],
            )
        x = _parse_finite_float(pos[0], "x")
        y = _parse_finite_float(pos[1], "y")
        with _make_client(inv.globals, timeout=_gesture_timeout(duration)) as client:
            resp = client.post_tap(x, y, duration_ms=duration, sync=sync)
        payload_xy: dict[str, Any] = {
            "ok": bool(resp.get("ok", False)),
            "x": x,
            "y": y,
            "durationMs": duration,
        }
        if resp.get("source"):
            payload_xy["source"] = resp["source"]
        return payload_xy

    with _make_client(inv.globals, timeout=_gesture_timeout(duration)) as client:
        xml_text = client.get_hierarchy(compressed=True)
        snapshot = parse_hierarchy_xml(xml_text)
        targets = resolve_tap_targets(
            snapshot,
            query=query_sel,
            text=text_sel,
            res_id=id_sel,
            exact=exact,
        )
        if not targets:
            desc = _selector_desc(text_sel, id_sel, query_sel)
            raise RuntimeAxiError(
                f"no UI element matched {desc}",
                help_hints=[f"Run `{prefix} ui` to inspect visible interactive elements"],
            )
        if len(targets) > 1 and index is None:
            candidates = [_candidate_row(i, node) for i, node in enumerate(targets)]
            desc = _selector_desc(text_sel, id_sel, query_sel)
            raise RuntimeAxiError(
                f"{len(targets)} UI elements matched {desc}; pass --index <N>",
                details={"candidates": candidates},
                help_hints=[f"Re-run with `--index 0` .. `--index {len(targets) - 1}` or `--exact`"],
            )
        chosen_idx = 0 if index is None else index
        if chosen_idx < 0 or chosen_idx >= len(targets):
            candidates = [_candidate_row(i, node) for i, node in enumerate(targets)]
            raise RuntimeAxiError(
                f"--index {chosen_idx} out of range (0..{len(targets) - 1})",
                details={"candidates": candidates},
                help_hints=[f"Pass `--index` between 0 and {len(targets) - 1}"],
            )
        chosen = targets[chosen_idx]
        resp = client.post_tap(float(chosen.cx), float(chosen.cy), duration_ms=duration, sync=sync)

    payload_sel: dict[str, Any] = {
        "ok": bool(resp.get("ok", False)),
        "x": chosen.cx,
        "y": chosen.cy,
        "label": chosen.label or "-",
        "res_id": chosen.res_id or "-",
        "durationMs": duration,
    }
    if resp.get("source"):
        payload_sel["source"] = resp["source"]
    return payload_sel


def _selector_desc(text: str | None, res_id: str | None, query: str | None) -> str:
    parts = []
    if text is not None:
        parts.append(f"--text {text!r}")
    if res_id is not None:
        parts.append(f"--id {res_id!r}")
    if query is not None:
        parts.append(f"--query {query!r}")
    return " ".join(parts)


def _candidate_row(index: int, node: UiNode) -> dict[str, Any]:
    return {
        "index": index,
        "label": node.label or "-",
        "res_id": node.res_id or "-",
        "cx": node.cx,
        "cy": node.cy,
    }


def handle_swipe(inv: ParsedInvocation) -> dict[str, Any]:
    prefix = _cli_prefix(inv.globals)
    pos = inv.positionals
    distance: float = inv.flags["distance"]
    duration: int = inv.flags["duration"]
    steps: int | None = inv.flags["steps"]
    hold: int = inv.flags["hold"]
    easing: str = inv.flags["easing"]
    sync: bool = not inv.flags["no_sync"]

    direction: str | None = None
    coords: tuple[float, float, float, float] | None = None

    if len(pos) == 1:
        cand = pos[0].strip().lower()
        if cand not in SWIPE_DIRECTIONS:
            raise UsageError(
                f"invalid swipe direction: {pos[0]}",
                help_hints=[
                    f"Use one of: {', '.join(SWIPE_DIRECTIONS)}, or pass 4 coordinates `<x1> <y1> <x2> <y2>`",
                ],
            )
        direction = cand
    elif len(pos) == 4:
        coords = (
            _parse_finite_float(pos[0], "x1"),
            _parse_finite_float(pos[1], "y1"),
            _parse_finite_float(pos[2], "x2"),
            _parse_finite_float(pos[3], "y2"),
        )
    else:
        raise UsageError(
            f"swipe expects 1 direction ({'|'.join(SWIPE_DIRECTIONS)}) or 4 coordinates, got {len(pos)} arguments",
            help_hints=[f"Use `{prefix} swipe up` or `{prefix} swipe 500 1800 500 400`"],
        )

    with _make_client(inv.globals, timeout=_gesture_timeout(duration + hold)) as client:
        if direction is not None:
            # /info carries the current-rotation size for ~1 ms on the device; a hierarchy
            # dump walks the whole tree and slows further while a list is still flinging.
            size: tuple[int, int] | None = None
            try:
                info = client.get_info()
                if int(info.get("width") or 0) > 0 and int(info.get("height") or 0) > 0:
                    size = (int(info["width"]), int(info["height"]))
            except (RuntimeAxiError, TypeError, ValueError):
                size = None
            if size is None:
                try:
                    xml_text = client.get_hierarchy(compressed=True)
                    size = parse_hierarchy_xml(xml_text).screen_size()
                except RuntimeAxiError:
                    size = None
            if size is None:
                # fast=False: the VirtualDisplay mirror would otherwise stay up on the shared agent.
                shot = client.get_screenshot(fmt="jpeg", quality=50, scale=1.0, fast=False)
                w = int(shot.headers.get("x-width", "0") or 0)
                h = int(shot.headers.get("x-height", "0") or 0)
                if w > 0 and h > 0:
                    size = (w, h)
            if size is None:
                raise RuntimeAxiError(
                    "could not determine screen dimensions for directional swipe",
                    help_hints=[f"Pass explicit coordinates `{prefix} swipe <x1> <y1> <x2> <y2>`"],
                )
            w, h = size
            cx = w / 2.0
            cy = h / 2.0
            half_x = (w * distance) / 2.0
            half_y = (h * distance) / 2.0
            if direction == "up":
                coords = (cx, cy + half_y, cx, cy - half_y)
            elif direction == "down":
                coords = (cx, cy - half_y, cx, cy + half_y)
            elif direction == "left":
                coords = (cx + half_x, cy, cx - half_x, cy)
            else:
                coords = (cx - half_x, cy, cx + half_x, cy)

        assert coords is not None
        x1, y1, x2, y2 = (round(v, 1) for v in coords)
        resp = client.post_swipe(
            x1,
            y1,
            x2,
            y2,
            duration_ms=duration,
            steps=steps,
            hold_ms=hold,
            sync=sync,
            easing=easing,
        )

    result: dict[str, Any] = {
        "ok": bool(resp.get("ok", False)),
        "x1": x1,
        "y1": y1,
        "x2": x2,
        "y2": y2,
        "durationMs": duration,
    }
    if direction is not None:
        result["direction"] = direction
    if resp.get("source"):
        result["source"] = resp["source"]
    return result


def parse_key_codes_arg(raw: str) -> list[int | str]:
    parts = [p.strip() for p in raw.split(",")]
    if not parts or any(not p for p in parts):
        raise UsageError(
            f"invalid key code list: {raw}",
            help_hints=["Provide friendly names (enter, back, home), KEYCODE_*, or positive integers separated by commas"],
        )
    if len(parts) > 64:
        raise UsageError(
            f"too many key codes ({len(parts)}); maximum is 64",
            help_hints=["Split key injection into batches of at most 64 key codes"],
        )
    resolved: list[int | str] = []
    for token in parts:
        lower = token.lower()
        if lower in KEY_ALIASES:
            resolved.append(KEY_ALIASES[lower])
            continue
        try:
            num = int(token)
        except ValueError:
            num = None
        if num is not None:
            if num <= 0:
                raise UsageError(
                    f"key code integer must be > 0 (got {num})",
                    help_hints=["Pass a positive Android KeyEvent code or friendly name like `enter` or `back`"],
                )
            resolved.append(num)
            continue
        upper = token.upper()
        if _KEYCODE_NAME_RE.fullmatch(upper):
            resolved.append(upper)
            continue
        sample = ", ".join(sorted(KEY_ALIASES.keys())[:12])
        raise UsageError(
            f"unknown key code: {token}",
            help_hints=[f"Use a friendly name ({sample}, ...), KEYCODE_*, or positive integer"],
        )
    return resolved


def handle_key(inv: ParsedInvocation) -> dict[str, Any]:
    codes = parse_key_codes_arg(inv.positionals[0])
    meta: int = inv.flags["meta"]
    interval: int = inv.flags["interval"]
    sync: bool = not inv.flags["no_sync"]

    with _make_client(inv.globals) as client:
        resp = client.post_key(codes, meta_state=meta, interval_ms=interval, sync=sync)
    result: dict[str, Any] = {
        "ok": bool(resp.get("ok", False)),
        "keyCodes": codes,
        "count": len(codes),
    }
    if resp.get("source"):
        result["source"] = resp["source"]
    return result


def handle_type(inv: ParsedInvocation) -> dict[str, Any]:
    prefix = _cli_prefix(inv.globals)
    pos = inv.positionals
    clear: bool = inv.flags["clear"]
    submit: bool = inv.flags["submit"]
    sync: bool = not inv.flags["no_sync"]
    text_sel: str | None = inv.flags["text"]
    id_sel: str | None = inv.flags["id"]
    query_sel: str | None = inv.flags["query"]
    exact: bool = inv.flags["exact"]
    index: int | None = inv.flags["index"]

    text_val = pos[0] if pos else ""
    if not text_val and not clear:
        raise UsageError(
            "type requires a <text> argument unless --clear is set",
            help_hints=[
                f'Use `{prefix} type "hello"` or `{prefix} type --clear`',
            ],
        )
    if len(text_val) > 4096:
        raise UsageError("text length exceeds maximum 4096 characters")

    has_selector = any(v is not None for v in (text_sel, id_sel, query_sel))
    if not has_selector and (exact or index is not None):
        raise UsageError(
            "--exact and --index are only valid with --text/--id/--query",
            help_hints=[f'Use `{prefix} type "{text_val}" --id <res_id>`'],
        )

    tapped_node: UiNode | None = None
    with _make_client(inv.globals) as client:
        if has_selector:
            xml_text = client.get_hierarchy(compressed=True)
            snapshot = parse_hierarchy_xml(xml_text)
            targets = resolve_tap_targets(
                snapshot,
                query=query_sel,
                text=text_sel,
                res_id=id_sel,
                exact=exact,
            )
            if not targets:
                desc = _selector_desc(text_sel, id_sel, query_sel)
                raise RuntimeAxiError(
                    f"no UI element matched {desc}",
                    help_hints=[f"Run `{prefix} ui` to inspect visible interactive elements"],
                )
            if len(targets) > 1 and index is None:
                candidates = [_candidate_row(i, node) for i, node in enumerate(targets)]
                desc = _selector_desc(text_sel, id_sel, query_sel)
                raise RuntimeAxiError(
                    f"{len(targets)} UI elements matched {desc}; pass --index <N>",
                    details={"candidates": candidates},
                    help_hints=[f"Re-run with `--index 0` .. `--index {len(targets) - 1}` or `--exact`"],
                )
            chosen_idx = 0 if index is None else index
            if chosen_idx < 0 or chosen_idx >= len(targets):
                candidates = [_candidate_row(i, node) for i, node in enumerate(targets)]
                raise RuntimeAxiError(
                    f"--index {chosen_idx} out of range (0..{len(targets) - 1})",
                    details={"candidates": candidates},
                    help_hints=[f"Pass `--index` between 0 and {len(targets) - 1}"],
                )
            tapped_node = targets[chosen_idx]
            client.post_tap(float(tapped_node.cx), float(tapped_node.cy), duration_ms=0, sync=True)

        resp = client.post_text(text_val, clear=clear, sync=sync and not submit)
        ok = bool(resp.get("ok", False))
        source = resp.get("source")
        if submit and ok:
            sub_resp = client.post_key([66], sync=sync)
            ok = ok and bool(sub_resp.get("ok", False))

    result: dict[str, Any] = {
        "ok": ok,
        "text": text_val,
        "clear": clear,
    }
    if submit:
        result["submit"] = True
    if tapped_node is not None:
        result["x"] = tapped_node.cx
        result["y"] = tapped_node.cy
        result["target"] = tapped_node.label or tapped_node.res_id or "-"
    if source:
        result["source"] = source
    return result


def handle_wait(inv: ParsedInvocation) -> dict[str, Any]:
    prefix = _cli_prefix(inv.globals)
    text_sel: str | None = inv.flags["text"]
    id_sel: str | None = inv.flags["id"]
    query_sel: str | None = inv.flags["query"]
    exact: bool = inv.flags["exact"]
    gone: bool = inv.flags["gone"]
    include_all: bool = inv.flags["all"]
    timeout_s: float = inv.flags["timeout"]
    interval_ms: int = inv.flags["interval"]

    if not any(v is not None for v in (text_sel, id_sel, query_sel)):
        raise UsageError(
            "wait requires at least one selector (--text, --id, or --query)",
            help_hints=[
                f'Use `{prefix} wait --text "Wi-Fi"` or `{prefix} wait --id loading --gone`',
            ],
        )

    desc = _selector_desc(text_sel, id_sel, query_sel)
    start_t = time.monotonic()
    deadline = start_t + timeout_s
    polls = 0

    with _make_client(inv.globals, timeout=max(10.0, timeout_s + 5.0)) as client:
        while True:
            polls += 1
            xml_text = client.get_hierarchy(compressed=True)
            snapshot = parse_hierarchy_xml(xml_text)
            candidates = snapshot.nodes if include_all else [n for n in snapshot.nodes if n.has_area]
            matched = [
                n
                for n in candidates
                if node_matches(n, query=query_sel, text=text_sel, res_id=id_sel, exact=exact)
            ]
            elapsed_ms = round((time.monotonic() - start_t) * 1000)

            if gone:
                if not matched:
                    return {
                        "status": "gone",
                        "selector": desc,
                        "elapsedMs": elapsed_ms,
                        "polls": polls,
                    }
            else:
                if matched:
                    rows = [node.to_row(idx, UI_DEFAULT_FIELDS) for idx, node in enumerate(matched[:10])]
                    return {
                        "status": "matched",
                        "selector": desc,
                        "elapsedMs": elapsed_ms,
                        "polls": polls,
                        "count": len(matched),
                        "nodes": rows,
                    }

            now = time.monotonic()
            if now >= deadline:
                state_word = "disappear" if gone else "appear"
                raise RuntimeAxiError(
                    f"timed out after {timeout_s:g}s ({polls} polls) waiting for {desc} to {state_word}",
                    help_hints=[
                        f"Run `{prefix} ui` or `{prefix} screen` to inspect the current screen",
                    ],
                )
            sleep_s = min(interval_ms / 1000.0, max(0.01, deadline - now))
            time.sleep(sleep_s)


# Used with fullmatch: re.match with `$` also accepts a trailing newline, which a shell
# reads as the end of one command and the start of the next. Single-segment names are legal.
_PACKAGE_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)*")
_COMPONENT_RE = re.compile(r"[A-Za-z0-9_.$]+/[A-Za-z0-9_.$]+")
_LAUNCHER_INTENT = "-a android.intent.action.MAIN -c android.intent.category.LAUNCHER"


def handle_launch(inv: ParsedInvocation) -> dict[str, Any]:
    prefix = _cli_prefix(inv.globals)
    pkg_name = inv.positionals[0].strip()
    # Package and component go into `am` shell commands. The package grammar has no shell
    # metacharacters; a component may contain `$` (inner-class activities such as
    # .Settings$WifiSettingsActivity), so it is also single-quoted (the regex rules out `'`).
    if not _PACKAGE_RE.fullmatch(pkg_name):
        raise UsageError(f"invalid package name: {inv.positionals[0]}")
    activity_override: str | None = inv.flags["activity"]
    stop_first: bool = inv.flags["stop"]
    wait_launch: bool = inv.flags["wait"]

    component: str | None = None
    if activity_override:
        act = activity_override.strip()
        component = act if "/" in act else f"{pkg_name}/{act}"
        if not _COMPONENT_RE.fullmatch(component):
            raise UsageError(f"invalid activity: {activity_override}")

    serial = adb.resolve_serial(inv.globals["serial"])
    if stop_first:
        adb.run_adb(["shell", f"am force-stop {pkg_name}"], serial=serial, timeout=10.0)

    wait_flag = "-W " if wait_launch else ""
    if component:
        start_cmd = f"am start {wait_flag}-n '{component}'"
    else:
        # Resolve the launcher activity on the device and start it with the launcher's
        # NEW_TASK | RESET_TASK_IF_NEEDED, so a running app resumes like a home-screen tap.
        # Replaces a full /packages listing (~0.5 s) and the `monkey` fallback (~3 s).
        start_cmd = (
            f"c=$(cmd package query-activities --brief {_LAUNCHER_INTENT} {pkg_name} | grep / | head -n 1 | tr -d ' '); "
            f'if [ -z "$c" ]; then echo "Error: no launcher activity in {pkg_name}"; exit 1; fi; '
            f'echo "component=$c"; am start {wait_flag}{_LAUNCHER_INTENT} -f 0x10200000 -n "$c"'
        )
    proc = adb.run_adb(["shell", start_cmd], serial=serial, timeout=20.0, check=False)
    out_text = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    for line in out_text.splitlines():
        if line.startswith("component="):
            component = line[len("component="):].strip()
    # Only am's own failure lines count: "Starting: Intent { cmp=... }" echoes the component,
    # which may itself be named like ".ExceptionViewer".
    failed = any(
        line.startswith(("Error", "Exception")) or "Exception:" in line
        for line in (raw.strip() for raw in out_text.splitlines())
    )
    if proc.returncode != 0 or failed:
        no_launcher = "no launcher activity" in out_text
        raise RuntimeAxiError(
            f"package {pkg_name} has no launchable activity" if no_launcher else f"failed to launch {component or pkg_name}: {out_text}",
            help_hints=[f"Pass `--activity <COMPONENT>` or inspect `{prefix} package {pkg_name}`"],
        )
    return {
        "status": "launched",
        "package": pkg_name,
        "component": component or "launcher-intent",
        "stopped_first": stop_first,
    }


def _validate_global_action(raw: str) -> str | int:
    trimmed = raw.strip()
    lower = trimmed.lower()
    if lower in GLOBAL_ACTIONS:
        return lower
    try:
        num = int(trimmed)
    except ValueError:
        num = None
    if num is not None and num > 0:
        return num
    raise UsageError(
        f"unknown global action: {raw}",
        help_hints=[f"Valid global actions: {', '.join(GLOBAL_ACTIONS)}, or a positive integer id"],
    )


def handle_global(inv: ParsedInvocation) -> dict[str, Any]:
    action = _validate_global_action(inv.positionals[0])
    with _make_client(inv.globals) as client:
        resp = client.post_global(action)
    return {
        "ok": bool(resp.get("ok", False)),
        "action": action,
    }


def _require_batch_float(obj: dict[str, Any], key: str, idx: int) -> float:
    if key not in obj or obj[key] is None:
        raise UsageError(f"batch action[{idx}] missing required field `{key}`")
    raw = obj[key]
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        raise UsageError(f"batch action[{idx}] invalid number for `{key}`")
    return _parse_finite_float(str(raw), f"action[{idx}].{key}")


def _optional_batch_int(
    obj: dict[str, Any],
    keys: tuple[str, ...],
    idx: int,
    min_v: int,
    max_v: int,
) -> int | None:
    for key in keys:
        if key in obj and obj[key] is not None:
            raw = obj[key]
            if isinstance(raw, bool):
                raise UsageError(f"batch action[{idx}] invalid integer for `{key}`")
            try:
                val = int(raw)
                if isinstance(raw, float) and not raw.is_integer():
                    raise ValueError
            except (ValueError, TypeError) as exc:
                raise UsageError(f"batch action[{idx}] invalid integer for `{key}`: {raw}") from exc
            if val < min_v or val > max_v:
                raise UsageError(f"batch action[{idx}] `{key}` out of range {min_v}..{max_v}: {val}")
            return val
    return None


def validate_batch_payload(raw_text: str, sync_override: bool | None = None) -> tuple[list[dict[str, Any]], bool]:
    if not raw_text.strip():
        raise UsageError(
            "batch JSON payload is empty",
            help_hints=['Pass a JSON array `[{"type":"tap","x":540,"y":1200}]` or `{"actions":[...]}`'],
        )
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise UsageError(
            f"invalid batch JSON: {exc.msg}",
            help_hints=["Ensure the batch payload is valid JSON"],
        ) from exc

    sync = True
    if isinstance(parsed, list):
        actions = parsed
    elif isinstance(parsed, dict):
        if "actions" not in parsed or not isinstance(parsed["actions"], list):
            raise UsageError(
                "batch JSON object must contain an `actions` array",
                help_hints=['Use `{"actions":[{"type":"tap","x":540,"y":1200}]}`'],
            )
        actions = parsed["actions"]
        if "sync" in parsed and isinstance(parsed["sync"], bool):
            sync = parsed["sync"]
    else:
        raise UsageError("batch payload must be a JSON array or an object with `actions`")

    if sync_override is not None:
        sync = sync_override

    if not (1 <= len(actions) <= 256):
        raise UsageError(f"batch actions count must be in 1..256 (got {len(actions)})")

    valid_types = ("down", "move", "up", "tap", "swipe", "wait", "key", "text", "global")
    touch_down = False
    for idx, item in enumerate(actions):
        if not isinstance(item, dict):
            raise UsageError(f"batch action[{idx}] must be a JSON object")
        raw_type = item.get("type")
        if not isinstance(raw_type, str) or not raw_type.strip():
            raise UsageError(f"batch action[{idx}] missing `type`")
        act_type = raw_type.strip().lower()
        if act_type not in valid_types:
            raise UsageError(
                f"batch action[{idx}] unknown type `{raw_type}`",
                help_hints=[f"Valid action types: {', '.join(valid_types)}"],
            )
        if act_type == "down":
            _require_batch_float(item, "x", idx)
            _require_batch_float(item, "y", idx)
            touch_down = True
        elif act_type == "move":
            if not touch_down:
                raise UsageError(
                    f"batch action[{idx}] `move` requires a preceding `down` action",
                    help_hints=["Emit a `down` action before any `move` or `up` action"],
                )
            _require_batch_float(item, "x", idx)
            _require_batch_float(item, "y", idx)
        elif act_type == "up":
            if not touch_down:
                raise UsageError(
                    f"batch action[{idx}] `up` requires a preceding `down` action",
                    help_hints=["Emit a `down` action before `up`"],
                )
            if "x" in item and item["x"] is not None:
                _require_batch_float(item, "x", idx)
            if "y" in item and item["y"] is not None:
                _require_batch_float(item, "y", idx)
            touch_down = False
        elif act_type == "tap":
            _require_batch_float(item, "x", idx)
            _require_batch_float(item, "y", idx)
            _optional_batch_int(item, ("duration", "durationMs"), idx, 0, 30000)
            touch_down = False
        elif act_type == "swipe":
            for coord in ("x1", "y1", "x2", "y2"):
                _require_batch_float(item, coord, idx)
            _optional_batch_int(item, ("duration", "durationMs"), idx, 0, 30000)
            _optional_batch_int(item, ("steps",), idx, 1, 300)
            _optional_batch_int(item, ("hold", "holdMs"), idx, 0, 10000)
            touch_down = False
        elif act_type == "wait":
            dur = _optional_batch_int(item, ("duration", "durationMs", "ms"), idx, 0, 30000)
            if dur is None:
                raise UsageError(f"batch action[{idx}] `wait` requires `durationMs` (or `duration`/`ms`)")
        elif act_type == "key":
            has_key = any(k in item and item[k] is not None for k in ("keyCodes", "keycodes", "codes", "keyCode", "keycode", "code"))
            if not has_key:
                raise UsageError(f"batch action[{idx}] `key` requires `keyCode` or `keyCodes`")
            _optional_batch_int(item, ("interval", "intervalMs"), idx, 0, 30000)
        elif act_type == "text":
            clear_val = item.get("clear", False)
            if not isinstance(clear_val, bool):
                raise UsageError(f"batch action[{idx}] `clear` must be a boolean")
            raw_text_val = item.get("text")
            if raw_text_val is None:
                if not clear_val:
                    raise UsageError(f"batch action[{idx}] `text` requires `text` field unless `clear` is true")
            elif not isinstance(raw_text_val, str):
                raise UsageError(f"batch action[{idx}] `text` must be a string")
            elif not raw_text_val and not clear_val:
                raise UsageError(f"batch action[{idx}] `text` cannot be empty unless `clear` is true")
            elif len(raw_text_val) > 4096:
                raise UsageError(f"batch action[{idx}] `text` exceeds 4096 characters")
        elif act_type == "global":
            if "action" not in item or item["action"] is None:
                raise UsageError(f"batch action[{idx}] `global` requires `action`")
            _validate_global_action(str(item["action"]))

    return actions, sync


def handle_batch(inv: ParsedInvocation) -> dict[str, Any]:
    prefix = _cli_prefix(inv.globals)
    file_arg: str | None = inv.flags["file"]
    pos = inv.positionals

    if pos and file_arg:
        raise UsageError(
            "cannot pass both inline JSON and --file to `batch`",
            help_hints=[f"Use `{prefix} batch '<json>'` OR `{prefix} batch --file path.json`"],
        )
    if not pos and not file_arg:
        sample_json = '[{"type":"tap","x":540,"y":1200}]'
        raise UsageError(
            "batch requires inline JSON or --file PATH (or `-` for stdin)",
            help_hints=[f"Use `{prefix} batch '{sample_json}'` or `--file gesture.json`"],
        )

    if file_arg == "-":
        raw_text = sys.stdin.read()
    elif file_arg:
        path = Path(file_arg).expanduser()
        if not path.is_file():
            raise UsageError(f"batch file not found: {path}")
        raw_text = path.read_text(encoding="utf-8")
    else:
        raw_text = pos[0]

    sync_override = False if inv.flags["no_sync"] else None
    actions, sync = validate_batch_payload(raw_text, sync_override=sync_override)
    with _make_client(inv.globals, timeout=max(30.0, _gesture_timeout(_batch_duration_ms(actions)))) as client:
        resp = client.post_batch(actions, sync=sync)
    result: dict[str, Any] = {
        "ok": bool(resp.get("ok", False)),
        "actions": len(actions),
        "sync": sync,
    }
    if resp.get("source"):
        result["source"] = resp["source"]
    return result


def handle_packages(inv: ParsedInvocation) -> dict[str, Any]:
    prefix = _cli_prefix(inv.globals)
    fields = _parse_fields(inv.flags["fields"], PACKAGE_DEFAULT_FIELDS, PACKAGE_ALL_FIELDS)
    user_only: bool = inv.flags["user_only"]
    query: str | None = inv.flags["query"]
    limit: int = inv.flags["limit"]
    full: bool = inv.flags["full"]

    with _make_client(inv.globals, timeout=20.0) as client:
        pkgs = client.get_packages(user_only=user_only)

    total = len(pkgs)
    user_count = sum(1 for p in pkgs if not p.get("system", False))
    sys_count = total - user_count

    filtered = pkgs
    if query:
        q = query.lower()
        filtered = [
            p
            for p in pkgs
            if q in str(p.get("packageName", "")).lower() or q in str(p.get("label", "")).lower()
        ]

    if not filtered:
        scope = "user packages" if user_only else "packages"
        q_note = f" matching '{query}'" if query else ""
        return {
            "packages": f"0 {scope} found{q_note} ({total} total)",
            "help": f"Run `{prefix} packages` without filters to list all installed packages",
        }

    shown = filtered if full else filtered[:limit]
    rows = [{f: p.get(f) for f in fields} for p in shown]
    count_label = f"{len(shown)} of {len(filtered)} packages ({user_count} user, {sys_count} system)"
    if query:
        count_label = f"{len(shown)} of {len(filtered)} matched ({total} total: {user_count} user, {sys_count} system)"

    hints: list[str] = []
    if len(shown) < len(filtered):
        hints.append(f"Run `{prefix} packages --full` to show all {len(filtered)} packages")
    hints.append(f"Run `{prefix} package <packageName>` for full details and signing schemes")
    return {
        "count": count_label,
        "packages": rows,
        "help": hints,
    }


def handle_package(inv: ParsedInvocation) -> dict[str, Any]:
    prefix = _cli_prefix(inv.globals)
    pkg_name = inv.positionals[0]

    with _make_client(inv.globals, timeout=20.0) as client:
        pkgs = client.get_packages(user_only=False)
        found = next((p for p in pkgs if p.get("packageName") == pkg_name), None)
        if found is None:
            raise RuntimeAxiError(
                f"package not found: {pkg_name}",
                help_hints=[f"Run `{prefix} packages --query {pkg_name}` to search installed packages"],
            )
        schemes = client.get_signature(pkg_name)

    detail = dict(found)
    detail["signingSchemes"] = schemes or "none"
    return detail


def handle_signature(inv: ParsedInvocation) -> dict[str, Any]:
    pkg_name = inv.positionals[0]
    with _make_client(inv.globals) as client:
        schemes = client.get_signature(pkg_name)
    return {
        "package": pkg_name,
        "schemes": schemes or "none",
    }


def handle_icon(inv: ParsedInvocation) -> dict[str, Any]:
    pkg_name = inv.positionals[0]
    out_flag: str | None = inv.flags["out"]

    if out_flag:
        out_path = Path(out_flag).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        safe_pkg = re.sub(r"[^A-Za-z0-9_.-]+", "_", pkg_name)
        out_path = _temp_output_dir() / f"icon-{safe_pkg}.png"

    with _make_client(inv.globals) as client:
        png_bytes = client.get_icon(pkg_name)

    out_path.write_bytes(png_bytes)
    return {
        "package": pkg_name,
        "path": str(out_path),
        "bytes": len(png_bytes),
    }


def _mb(raw: Any) -> int | None:
    if isinstance(raw, (int, float)) and raw >= 0:
        return round(raw / (1024 * 1024))
    return None


def _gb(raw: Any) -> float | None:
    if isinstance(raw, (int, float)) and raw >= 0:
        return round(raw / (1024 * 1024 * 1024), 1)
    return None


def _summarize_device_info(info: dict[str, Any], prefix: str) -> dict[str, Any]:
    dev = info.get("device") if isinstance(info.get("device"), dict) else {}
    bld = info.get("build") if isinstance(info.get("build"), dict) else {}
    ram = info.get("ram") if isinstance(info.get("ram"), dict) else {}
    cpu = info.get("cpu") if isinstance(info.get("cpu"), dict) else {}
    gpu = info.get("gpu") if isinstance(info.get("gpu"), dict) else {}
    net = info.get("network") if isinstance(info.get("network"), dict) else {}
    displays = info.get("display") if isinstance(info.get("display"), list) else []
    storages = info.get("storage") if isinstance(info.get("storage"), list) else []

    primary_display = displays[0] if displays and isinstance(displays[0], dict) else {}
    display_summary = "-"
    if primary_display:
        w = primary_display.get("widthPx", 0)
        h = primary_display.get("heightPx", 0)
        hz = primary_display.get("refreshRate", 0)
        dpi = primary_display.get("densityDpi", 0)
        display_summary = f"{w}x{h} @ {round(hz, 1) if isinstance(hz, float) else hz}Hz ({dpi} dpi)"

    primary_storage = next((s for s in storages if isinstance(s, dict) and s.get("isPrimary")), None)
    if primary_storage is None and storages and isinstance(storages[0], dict):
        primary_storage = storages[0]
    storage_summary = "-"
    if primary_storage:
        avail = _gb(primary_storage.get("availableBytes"))
        total = _gb(primary_storage.get("totalBytes"))
        if avail is not None and total is not None:
            storage_summary = f"{avail} GB free / {total} GB total"

    abis = dev.get("supportedAbis")
    abis_str = ",".join(str(a) for a in abis) if isinstance(abis, list) else "-"
    transports = net.get("transports")
    transports_str = ",".join(str(t) for t in transports) if isinstance(transports, list) else "-"

    section_counts: dict[str, int] = {}
    for sec in DEVICE_SECTIONS:
        val = info.get(sec)
        if isinstance(val, list):
            section_counts[sec] = len(val)
        elif isinstance(val, dict):
            section_counts[sec] = len(val)
        else:
            section_counts[sec] = 0

    return {
        "device": {
            "manufacturer": dev.get("manufacturer", ""),
            "brand": dev.get("brand", ""),
            "model": dev.get("model", ""),
            "codename": dev.get("device", ""),
            "soc": dev.get("socModel") or dev.get("hardware", ""),
            "abis": abis_str,
        },
        "build": {
            "release": bld.get("release", ""),
            "sdkInt": bld.get("sdkInt", 0),
            "securityPatch": bld.get("securityPatch", ""),
            "id": dev.get("id", ""),
        },
        "hardware": {
            "ramMb": f"{_mb(ram.get('availMem'))} free / {_mb(ram.get('totalMem'))} total"
            if ram.get("totalMem") is not None
            else "-",
            "storage": storage_summary,
            "display": display_summary,
            "cpuCores": cpu.get("cores", 0),
            "gpu": gpu.get("renderer") or gpu.get("vendor") or "-",
            "network": f"{net.get('activeInterface') or 'none'} ({transports_str})",
        },
        "sections": section_counts,
        "help": f"Run `{prefix} device --section <name>` to inspect a section ({', '.join(DEVICE_SECTIONS)})",
    }


def _flatten_row_for_table(item: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in item.items():
        if isinstance(v, list):
            if all(isinstance(x, (str, int, float, bool)) or x is None for x in v):
                out[k] = ",".join(str(x) for x in v)
            else:
                out[k] = f"[{len(v)} items]"
        elif isinstance(v, dict):
            out[k] = f"{{{len(v)} keys}}"
        else:
            out[k] = v
    return out


def handle_device(inv: ParsedInvocation) -> dict[str, Any]:
    prefix = _cli_prefix(inv.globals)
    section: str | None = inv.flags["section"]
    query: str | None = inv.flags["query"]
    limit: int = inv.flags["limit"]
    full: bool = inv.flags["full"]

    with _make_client(inv.globals, timeout=20.0) as client:
        info = client.get_device_info()

    if section is None:
        return _summarize_device_info(info, prefix)

    data = info.get(section)
    if data is None:
        return {"section": section, "items": f"section `{section}` unavailable on this device"}

    if isinstance(data, list):
        total = len(data)
        filtered = data
        if query:
            q = query.lower()
            filtered = [item for item in data if q in json.dumps(item, ensure_ascii=False).lower()]
        if not filtered:
            q_note = f" matching '{query}'" if query else ""
            return {"section": section, "items": f"0 entries found in `{section}`{q_note} ({total} total)"}
        shown = filtered if full else filtered[:limit]
        normalized = [_flatten_row_for_table(x) if isinstance(x, dict) else x for x in shown]
        # Align keys across dicts so TOON renders tabular rows cleanly when objects omit optional fields.
        if normalized and all(isinstance(x, dict) for x in normalized):
            all_keys: list[str] = []
            seen_keys: set[str] = set()
            for row in normalized:
                for k in row:
                    if k not in seen_keys:
                        seen_keys.add(k)
                        all_keys.append(k)
            normalized = [{k: row.get(k) for k in all_keys} for row in normalized]
        result: dict[str, Any] = {
            "section": section,
            "count": f"{len(shown)} of {len(filtered)} entries" + (f" ({total} total)" if query else ""),
            "items": normalized,
        }
        if len(shown) < len(filtered):
            result["help"] = f"Run `{prefix} device --section {section} --full` to see all {len(filtered)} entries"
        return result

    if isinstance(data, dict):
        if section == "properties":
            items_kv = [{"key": k, "value": v} for k, v in sorted(data.items())]
            total = len(items_kv)
            if query:
                q = query.lower()
                items_kv = [r for r in items_kv if q in r["key"].lower() or q in str(r["value"]).lower()]
            if not items_kv:
                q_note = f" matching '{query}'" if query else ""
                return {"section": section, "properties": f"0 properties found{q_note} ({total} total)"}
            shown = items_kv if full else items_kv[:limit]
            result = {
                "section": section,
                "count": f"{len(shown)} of {len(items_kv)} properties" + (f" ({total} total)" if query else ""),
                "properties": shown,
            }
            if len(shown) < len(items_kv):
                q_flag = f" --query {query}" if query else ""
                result["help"] = f"Run `{prefix} device --section properties{q_flag} --full` for all {len(items_kv)} properties"
            return result

        if query:
            q = query.lower()
            filtered_dict = {
                k: v
                for k, v in data.items()
                if q in k.lower() or q in json.dumps(v, ensure_ascii=False).lower()
            }
            if not filtered_dict:
                return {"section": section, "data": f"0 keys matched '{query}' in `{section}`"}
            return {"section": section, "data": filtered_dict}
        return {"section": section, "data": data}

    return {"section": section, "data": data}


def handle_setup(inv: ParsedInvocation) -> dict[str, Any]:
    return run_setup(
        target=inv.flags["target"],
        scope=inv.flags["scope"],
        dry_run=inv.flags["dry_run"],
        remove=inv.flags["remove"],
    )


def default_skill_path() -> Path:
    return adb.repo_root() / "skills" / TOOL_NAME / "SKILL.md"


def handle_skill(inv: ParsedInvocation) -> dict[str, Any] | str:
    write_flag: bool = inv.flags["write"]
    check_flag: bool = inv.flags["check"]
    out_flag: str | None = inv.flags["out"]

    if write_flag and check_flag:
        raise UsageError(
            "cannot pass both --write and --check to `skill`",
            help_hints=["Use `android-agent skill --write` OR `android-agent skill --check`"],
        )

    rendered = render_skill_md()
    if not write_flag and not check_flag:
        return rendered

    target_path = Path(out_flag).expanduser().resolve() if out_flag else default_skill_path()

    if check_flag:
        if not target_path.is_file():
            raise RuntimeAxiError(
                f"SKILL.md is missing at {target_path}",
                help_hints=["Run `android-agent skill --write`"],
            )
        existing = target_path.read_text(encoding="utf-8").replace("\r\n", "\n")
        if existing != rendered:
            raise RuntimeAxiError(
                f"SKILL.md is out of date at {target_path}",
                help_hints=["Run `android-agent skill --write`"],
            )
        return {
            "status": "up to date",
            "path": str(target_path),
            "bytes": len(rendered.encode("utf-8")),
        }

    if target_path.is_file():
        existing = target_path.read_text(encoding="utf-8").replace("\r\n", "\n")
        if existing == rendered:
            return {
                "status": "unchanged (no-op)",
                "path": str(target_path),
                "bytes": len(rendered.encode("utf-8")),
            }

    target_path.parent.mkdir(parents=True, exist_ok=True)
    with open(target_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(rendered)
    return {
        "status": "written",
        "path": str(target_path),
        "bytes": len(rendered.encode("utf-8")),
    }


DISPATCH_TABLE = {
    None: handle_home,
    "devices": handle_devices,
    "start": handle_start,
    "stop": handle_stop,
    "status": handle_status,
    "screen": handle_screen,
    "ui": handle_ui,
    "tap": handle_tap,
    "swipe": handle_swipe,
    "key": handle_key,
    "type": handle_type,
    "wait": handle_wait,
    "launch": handle_launch,
    "global": handle_global,
    "batch": handle_batch,
    "packages": handle_packages,
    "package": handle_package,
    "signature": handle_signature,
    "icon": handle_icon,
    "device": handle_device,
    "setup": handle_setup,
    "skill": handle_skill,
}
