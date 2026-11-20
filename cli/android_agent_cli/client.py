from __future__ import annotations

import http.client
import json
import socket
import urllib.parse
from dataclasses import dataclass
from typing import Any

from .errors import RuntimeAxiError


@dataclass(frozen=True)
class HttpResult:
    status: int
    headers: dict[str, str]
    body: bytes

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        try:
            return json.loads(self.text())
        except json.JSONDecodeError as exc:
            raise RuntimeAxiError(
                f"invalid JSON from agent server: {exc.msg}",
            ) from exc


class AgentClient:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 9500,
        timeout: float = 15.0,
        serial: str | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.serial = serial
        self._conn: http.client.HTTPConnection | None = None

    def __enter__(self) -> AgentClient:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def _cli_prefix(self) -> str:
        parts = ["android-agent"]
        if self.serial:
            parts.extend(["-s", self.serial])
        if self.port != 9500:
            parts.extend(["-p", str(self.port)])
        if self.host != "127.0.0.1":
            parts.extend(["--host", self.host])
        return " ".join(parts)

    def _ensure_conn(self) -> http.client.HTTPConnection:
        if self._conn is None:
            self._conn = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        return self._conn

    def _reset_conn(self) -> None:
        self.close()

    def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_body: Any | None = None,
        raw_body: bytes | None = None,
        retry_on_reset: bool | None = None,
    ) -> HttpResult:
        target = path
        if params:
            filtered = {k: v for k, v in params.items() if v is not None}
            if filtered:
                target = f"{path}?{urllib.parse.urlencode(filtered)}"

        headers: dict[str, str] = {"Connection": "keep-alive"}
        body_bytes: bytes | None = raw_body
        if json_body is not None:
            body_bytes = json.dumps(json_body, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"

        allow_retry = (method.upper() in ("GET", "HEAD")) if retry_on_reset is None else retry_on_reset
        attempts = 2 if allow_retry else 1
        last_exc: Exception | None = None

        for attempt in range(attempts):
            conn = self._ensure_conn()
            try:
                conn.request(method, target, body=body_bytes, headers=headers)
                resp = conn.getresponse()
                data = resp.read()
                resp_headers = {k.lower(): v for k, v in resp.getheaders()}
                status = resp.status
                if resp.will_close or resp_headers.get("connection", "").lower() == "close":
                    self._reset_conn()
                result = HttpResult(status=status, headers=resp_headers, body=data)
                break
            except (
                http.client.RemoteDisconnected,
                http.client.BadStatusLine,
                http.client.CannotSendRequest,
                ConnectionResetError,
                BrokenPipeError,
            ) as exc:
                self._reset_conn()
                last_exc = exc
                if attempt + 1 < attempts:
                    continue
                raise self._translate_connection_error(exc) from exc
            except (ConnectionRefusedError, TimeoutError, socket.timeout, OSError, http.client.HTTPException) as exc:
                self._reset_conn()
                raise self._translate_connection_error(exc) from exc
        else:
            raise self._translate_connection_error(last_exc or OSError("connection failed"))

        if result.status >= 400:
            msg = result.text().strip() or f"HTTP {result.status}"
            raise RuntimeAxiError(
                f"agent rejected {path} ({result.status}): {msg}",
                help_hints=[f"Run `{self._cli_prefix()} status` to verify the agent server state"],
            )
        return result

    def _translate_connection_error(self, exc: Exception) -> RuntimeAxiError:
        prefix = self._cli_prefix()
        if isinstance(exc, (TimeoutError, socket.timeout)):
            return RuntimeAxiError(
                f"agent server at {self.host}:{self.port} timed out after {self.timeout}s",
                help_hints=[f"Run `{prefix} status` or `{prefix} start --restart`"],
            )
        return RuntimeAxiError(
            f"cannot reach agent server at {self.host}:{self.port}",
            help_hints=[f"Run `{prefix} start` to launch the on-device server and port forward"],
        )

    def probe_health(self, timeout: float = 0.3) -> bool:
        prev_timeout = self.timeout
        self.timeout = timeout
        self._reset_conn()
        try:
            res = self.request("GET", "/health", retry_on_reset=False)
            return res.status == 200 and res.text().strip() == "ok"
        except RuntimeAxiError:
            return False
        finally:
            self.timeout = prev_timeout
            self._reset_conn()

    def get_info(self) -> dict[str, Any]:
        return self.request("GET", "/info").json()

    def get_hierarchy(self, compressed: bool = True) -> str:
        val = "true" if compressed else "false"
        return self.request("GET", "/hierarchy", params={"compressed": val}).text()

    def get_screenshot(
        self,
        fmt: str = "jpeg",
        quality: int = 80,
        scale: float = 0.5,
        fast: bool = True,
        crop: str | None = None,
    ) -> HttpResult:
        params: dict[str, Any] = {
            "format": fmt,
            "quality": str(quality),
            "scale": f"{scale:g}",
            "fast": "true" if fast else "false",
        }
        if crop is not None:
            params["crop"] = crop
        return self.request(
            "GET",
            "/screenshot",
            params=params,
        )

    def post_tap(self, x: float, y: float, duration_ms: int = 0, sync: bool = True) -> dict[str, Any]:
        return self.request(
            "POST",
            "/input/tap",
            json_body={"x": x, "y": y, "durationMs": duration_ms, "sync": sync},
            retry_on_reset=False,
        ).json()

    def post_swipe(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        duration_ms: int = 300,
        steps: int | None = None,
        hold_ms: int = 0,
        sync: bool = True,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "x1": x1,
            "y1": y1,
            "x2": x2,
            "y2": y2,
            "durationMs": duration_ms,
            "holdMs": hold_ms,
            "sync": sync,
        }
        if steps is not None:
            payload["steps"] = steps
        return self.request("POST", "/input/swipe", json_body=payload, retry_on_reset=False).json()

    def post_key(
        self,
        key_codes: list[int | str],
        meta_state: int = 0,
        interval_ms: int = 0,
        sync: bool = True,
    ) -> dict[str, Any]:
        return self.request(
            "POST",
            "/input/key",
            json_body={
                "keyCodes": key_codes,
                "metaState": meta_state,
                "intervalMs": interval_ms,
                "sync": sync,
            },
            retry_on_reset=False,
        ).json()

    def post_global(self, action: str | int) -> dict[str, Any]:
        return self.request(
            "POST",
            "/input/global",
            json_body={"action": action},
            retry_on_reset=False,
        ).json()

    def post_batch(self, actions: list[dict[str, Any]], sync: bool = True) -> dict[str, Any]:
        return self.request(
            "POST",
            "/input/batch",
            json_body={"actions": actions, "sync": sync},
            retry_on_reset=False,
        ).json()

    def get_packages(self, user_only: bool = False) -> list[dict[str, Any]]:
        val = "true" if user_only else "false"
        return self.request("GET", "/packages", params={"user_only": val}).json()

    def get_signature(self, pkg: str) -> str:
        return self.request("GET", "/signature", params={"pkg": pkg}).text().strip()

    def get_icon(self, pkg: str) -> bytes:
        return self.request("GET", "/icon", params={"pkg": pkg}).body

    def get_device_info(self) -> dict[str, Any]:
        return self.request("GET", "/device-info").json()

    def stop_server(self) -> str:
        return self.request("GET", "/stop", retry_on_reset=False).text().strip()
