from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .errors import RuntimeAxiError

REMOTE_JAR_PATH = "/data/local/tmp/agent-server.jar"
REMOTE_LOG_PATH = "/data/local/tmp/agent-server.log"


@dataclass(frozen=True)
class AdbDevice:
    serial: str
    state: str
    model: str = ""
    product: str = ""
    device: str = ""
    transport_id: str = ""

    def to_row(self) -> dict[str, str]:
        return {
            "serial": self.serial,
            "state": self.state,
            "model": self.model or "-",
            "product": self.product or "-",
        }


def find_adb() -> str:
    adb_path = shutil.which("adb")
    if not adb_path:
        raise RuntimeAxiError(
            "adb executable not found on PATH",
            help_hints=["Install Android Platform-Tools and ensure `adb` is on PATH"],
        )
    return adb_path


def run_adb(
    args: list[str],
    serial: str | None = None,
    timeout: float = 15.0,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    adb_bin = find_adb()
    cmd = [adb_bin]
    if serial:
        cmd.extend(["-s", serial])
    cmd.extend(args)
    try:
        proc = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeAxiError(
            f"adb timed out after {timeout}s",
            help_hints=["Check USB connection and run `android-agent devices`"],
        ) from exc
    except OSError as exc:
        raise RuntimeAxiError(
            f"failed to execute adb: {exc}",
            help_hints=["Verify Android Platform-Tools installation"],
        ) from exc

    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip() or f"exit code {proc.returncode}"
        raise RuntimeAxiError(
            f"adb command failed: {detail}",
            help_hints=["Run `android-agent devices` to verify device connection"],
        )
    return proc


def parse_devices_output(output: str) -> list[AdbDevice]:
    devices: list[AdbDevice] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("List of devices") or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        meta: dict[str, str] = {}
        for token in parts[2:]:
            if ":" in token:
                k, v = token.split(":", 1)
                meta[k] = v
        devices.append(
            AdbDevice(
                serial=serial,
                state=state,
                model=meta.get("model", ""),
                product=meta.get("product", ""),
                device=meta.get("device", ""),
                transport_id=meta.get("transport_id", ""),
            )
        )
    return devices


def list_devices(timeout: float = 5.0) -> list[AdbDevice]:
    proc = run_adb(["devices", "-l"], timeout=timeout)
    return parse_devices_output(proc.stdout)


def resolve_serial(requested: str | None, timeout: float = 5.0) -> str:
    if requested:
        return requested
    devices = list_devices(timeout=timeout)
    if not devices:
        raise RuntimeAxiError(
            "no ADB devices connected",
            help_hints=["Connect an Android device with USB debugging enabled, then run `android-agent devices`"],
        )
    online = [d for d in devices if d.state == "device"]
    if len(online) == 1:
        return online[0].serial
    if not online:
        first = devices[0]
        if first.state == "unauthorized":
            raise RuntimeAxiError(
                f"device {first.serial} is unauthorized",
                help_hints=["Accept the USB debugging prompt on the device, then run `android-agent devices`"],
            )
        raise RuntimeAxiError(
            f"device {first.serial} is {first.state} (not ready)",
            help_hints=["Reconnect the device and run `android-agent devices`"],
        )
    raise RuntimeAxiError(
        f"{len(online)} ADB devices connected; pass --serial <SERIAL>",
        details={"devices": [d.to_row() for d in online]},
        help_hints=[f"Re-run with `-s {online[0].serial}`"],
    )


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def discover_jar(explicit_path: str | None = None) -> Path:
    candidates: list[Path] = []
    if explicit_path:
        p = Path(explicit_path).expanduser().resolve()
        if p.is_file():
            return p
        raise RuntimeAxiError(
            f"agent JAR not found at {p}",
            help_hints=["Build the JAR with `build.bat` (or `./gradlew assembleRelease`) or pass `--jar <PATH>`"],
        )
    env_jar = os.environ.get("ANDROID_AGENT_JAR")
    if env_jar:
        candidates.append(Path(env_jar).expanduser().resolve())
    root = repo_root()
    candidates.extend([
        root / "build" / "dist" / "agent-server.jar",
        Path.cwd() / "build" / "dist" / "agent-server.jar",
        root.parent / "tools" / "agent-server.jar",
        Path.cwd().parent / "tools" / "agent-server.jar",
    ])
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeAxiError(
        "agent-server.jar not found in ANDROID_AGENT_JAR, build/dist/, or ../tools/",
        help_hints=[
            "Build `build/dist/agent-server.jar` or run `android-agent start --jar <PATH>`",
        ],
    )


def remote_file_size(serial: str, remote_path: str = REMOTE_JAR_PATH) -> int | None:
    proc = run_adb(
        ["shell", f"stat -c %s {remote_path} 2>/dev/null || wc -c < {remote_path} 2>/dev/null"],
        serial=serial,
        timeout=5.0,
        check=False,
    )
    if proc.returncode != 0:
        return None
    text = proc.stdout.strip().split()
    if not text:
        return None
    try:
        return int(text[0])
    except ValueError:
        return None


def push_jar(serial: str, local_jar: Path, remote_path: str = REMOTE_JAR_PATH) -> bool:
    local_size = local_jar.stat().st_size
    if remote_file_size(serial, remote_path) == local_size:
        return False
    run_adb(["push", str(local_jar), remote_path], serial=serial, timeout=30.0)
    return True


def forward_port(serial: str, local_port: int, remote_port: int | None = None) -> None:
    target_port = local_port if remote_port is None else remote_port
    run_adb(["forward", f"tcp:{local_port}", f"tcp:{target_port}"], serial=serial, timeout=5.0)


def remove_forward(serial: str | None, local_port: int) -> None:
    run_adb(["forward", "--remove", f"tcp:{local_port}"], serial=serial, timeout=5.0, check=False)


def launch_server_detached(
    serial: str,
    port: int = 9500,
    remote_jar: str = REMOTE_JAR_PATH,
    remote_log: str = REMOTE_LOG_PATH,
) -> None:
    # Redirect stdin/stdout/stderr and background inside mksh so adb shell returns immediately.
    remote_cmd = (
        f"rm -f {remote_log}; "
        f"CLASSPATH={remote_jar} nohup app_process / la.shiro.agent.Server --port {port} "
        f"</dev/null >{remote_log} 2>&1 &"
    )
    run_adb(["shell", remote_cmd], serial=serial, timeout=10.0)


def read_remote_log(serial: str, lines: int = 20, remote_log: str = REMOTE_LOG_PATH) -> str:
    proc = run_adb(
        ["shell", f"tail -n {lines} {remote_log} 2>/dev/null"],
        serial=serial,
        timeout=5.0,
        check=False,
    )
    return proc.stdout.strip()


def translate_startup_log_error(log_text: str, cli_prefix: str) -> RuntimeAxiError:
    if "UiAutomationService" in log_text and "already registered" in log_text:
        return RuntimeAxiError(
            "another UiAutomation client is already registered on this device",
            help_hints=[
                f"Run `{cli_prefix} stop` or stop the competing UiAutomation process, then retry `{cli_prefix} start`",
            ],
        )
    snippet = " | ".join(line.strip() for line in log_text.splitlines()[-4:] if line.strip())
    msg = "agent server failed to become healthy before timeout"
    if snippet:
        msg = f"{msg}: {snippet}"
    return RuntimeAxiError(
        msg,
        help_hints=[f"Verify device state with `{cli_prefix} devices` and retry `{cli_prefix} start`"],
    )
