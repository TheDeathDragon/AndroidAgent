from __future__ import annotations

import http.server
import io
import json
import sys
import tempfile
import threading
import unittest
import urllib.parse
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from android_agent_cli import adb
from android_agent_cli.app import main

MOCK_HIERARCHY_XML = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node index="0" text="" resource-id="" class="android.widget.FrameLayout" package="com.android.settings" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[0,0][1080,2400]">
    <node index="0" text="" resource-id="com.android.settings:id/row_wifi" class="android.widget.LinearLayout" package="com.android.settings" content-desc="" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[0,200][1080,360]">
      <node index="0" text="Wi-Fi" resource-id="android:id/title" class="android.widget.TextView" package="com.android.settings" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[48,220][300,280]" />
    </node>
    <node index="1" text="Item A" resource-id="com.android.settings:id/item_a" class="android.widget.Button" package="com.android.settings" content-desc="" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[100,400][500,500]" />
    <node index="2" text="Item B" resource-id="com.android.settings:id/item_b" class="android.widget.Button" package="com.android.settings" content-desc="" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[100,600][500,700]" />
  </node>
</hierarchy>
"""

MOCK_PACKAGES = [
    {
        "packageName": "com.example.userapp",
        "label": "UserApp",
        "versionName": "2.1.0",
        "versionCode": 21,
        "apkPath": "/data/app/userapp/base.apk",
        "apkSize": 102400,
        "system": False,
        "enabled": True,
        "suspended": False,
        "uid": 10200,
        "firstInstallTime": 1700000000000,
        "lastUpdateTime": 1700000100000,
        "targetSdkVersion": 34,
        "minSdkVersion": 26,
        "mainActivity": "com.example.userapp/.MainActivity",
        "signatureSha256": "AA:BB:CC",
        "dataDir": "/data/user/0/com.example.userapp",
    },
    {
        "packageName": "com.android.settings",
        "label": "Settings",
        "versionName": "14",
        "versionCode": 34,
        "apkPath": "/system/priv-app/Settings/Settings.apk",
        "apkSize": 204800,
        "system": True,
        "enabled": True,
        "suspended": False,
        "uid": 1000,
        "firstInstallTime": 1600000000000,
        "lastUpdateTime": 1600000000000,
        "targetSdkVersion": 34,
        "minSdkVersion": 34,
        "mainActivity": "com.android.settings/.Settings",
        "signatureSha256": "11:22:33",
        "dataDir": "/data/user/0/com.android.settings",
    },
]

MOCK_DEVICE_INFO: dict[str, Any] = {
    "device": {
        "manufacturer": "OnePlus",
        "brand": "OnePlus",
        "model": "IN2010",
        "device": "OnePlus8",
        "hardware": "qcom",
        "socModel": "SM8250",
        "id": "UKQ1.230924",
        "supportedAbis": ["arm64-v8a", "armeabi-v7a"],
    },
    "build": {"release": "14", "sdkInt": 34, "securityPatch": "2024-05-01"},
    "ram": {"totalMem": 8 * 1024 * 1024 * 1024, "availMem": 4 * 1024 * 1024 * 1024, "lowMemory": False},
    "storage": [{"description": "Internal", "isPrimary": True, "totalBytes": 128 * 1024**3, "availableBytes": 64 * 1024**3}],
    "display": [{"id": 0, "widthPx": 1080, "heightPx": 2400, "refreshRate": 90.0, "densityDpi": 420}],
    "cpu": {"cores": 8, "hardware": "Qualcomm SM8250"},
    "gpu": {"vendor": "Qualcomm", "renderer": "Adreno 650"},
    "sensors": [
        {"name": "Accelerometer", "vendor": "Bosch", "type": 1},
        {"name": "Gyroscope", "vendor": "Bosch", "type": 4},
        {"name": "Light", "vendor": "AMS", "type": 5},
    ],
    "cameraV1": [],
    "cameras": [{"id": "0", "facing": "BACK"}],
    "network": {"activeInterface": "wlan0", "transports": ["WIFI"]},
    "sim": [{"slotIndex": 0, "simState": "ABSENT"}],
    "treble": {"trebleEnabled": "true"},
    "features": [{"name": "android.hardware.wifi"}],
    "properties": {
        "ro.build.display.id": "IN2010_14",
        "ro.build.version.sdk": "34",
        "ro.product.model": "IN2010",
    },
}


class _MockAgentServer(http.server.ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], handler_cls: type[http.server.BaseHTTPRequestHandler]) -> None:
        super().__init__(server_address, handler_cls)
        self.requests: list[tuple[str, str, Any]] = []
        self.connection_count = 0
        self.hierarchy_xml = MOCK_HIERARCHY_XML
        self.stopped = False
        self.screenshot_cache_hit = False
        self.info_extra: dict[str, Any] = {}
        self.input_ok = True


class _MockAgentHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: _MockAgentServer

    def setup(self) -> None:
        super().setup()
        self.server.connection_count += 1

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def _send(self, status: int, body: bytes, content_type: str, extra_headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "keep-alive")
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        query = dict(urllib.parse.parse_qsl(parsed.query))
        self.server.requests.append(("GET", parsed.path, query))

        if parsed.path == "/health":
            if self.server.stopped:
                self._send(503, b"stopped", "text/plain")
                return
            self._send(200, b"ok", "text/plain")
        elif parsed.path == "/info":
            data = {
                "brand": "OnePlus",
                "model": "IN2010",
                "sdk": 34,
                "release": "14",
                "agentVersion": "1.2.0",
                "agentVersionCode": 12,
                **self.server.info_extra,
            }
            self._send(200, json.dumps(data).encode("utf-8"), "application/json")
        elif parsed.path == "/hierarchy":
            self._send(200, self.server.hierarchy_xml.encode("utf-8"), "application/xml")
        elif parsed.path == "/screenshot":
            fmt = query.get("format", "png")
            scale = float(query.get("scale", "1.0"))
            w = round(1080 * scale)
            h = round(2400 * scale)
            if "crop" in query:
                cx, cy, cw, ch = (int(v) for v in query["crop"].split(","))
                w = min(cw, w - cx)
                h = min(ch, h - cy)
            payload = b"\xff\xd8\xff\xe0MOCKJPEG" if fmt == "jpeg" else b"\x89PNG\r\n\x1a\nMOCKPNG"
            self._send(
                200,
                payload,
                "image/jpeg" if fmt == "jpeg" else "image/png",
                {
                    "X-Width": str(w),
                    "X-Height": str(h),
                    "X-Format": fmt,
                    "X-Capture-Source": "virtual-display" if query.get("fast") == "true" else "uiautomation",
                    "X-Cache-Hit": "true" if self.server.screenshot_cache_hit else "false",
                },
            )
        elif parsed.path == "/packages":
            user_only = query.get("user_only") == "true"
            items = [p for p in MOCK_PACKAGES if not (user_only and p["system"])]
            self._send(200, json.dumps(items).encode("utf-8"), "application/json")
        elif parsed.path == "/signature":
            pkg = query.get("pkg", "")
            body = b"V2+V3" if pkg == "com.example.userapp" else b""
            self._send(200, body, "text/plain")
        elif parsed.path == "/icon":
            self._send(200, b"\x89PNG\r\n\x1a\nICON", "image/png")
        elif parsed.path == "/device-info":
            self._send(200, json.dumps(MOCK_DEVICE_INFO).encode("utf-8"), "application/json")
        elif parsed.path == "/stop":
            self.server.stopped = True
            self._send(200, b"stopping", "text/plain")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        payload = json.loads(body.decode("utf-8")) if body else None
        self.server.requests.append(("POST", parsed.path, payload))
        extra = (
            {"X-Input-Source": "direct"}
            if parsed.path in ("/input/tap", "/input/swipe", "/input/key", "/input/text", "/input/batch")
            else None
        )
        ok = self.server.input_ok if extra else True
        self._send(200, b'{"ok":true}' if ok else b'{"ok":false}', "application/json", extra)


class TestCommandsMock(unittest.TestCase):
    def setUp(self) -> None:
        self.srv = _MockAgentServer(("127.0.0.1", 0), _MockAgentHandler)
        self.port = self.srv.server_address[1]
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()

    def _run(self, args: list[str]) -> tuple[int, str, str]:
        out = io.StringIO()
        err = io.StringIO()
        code = main(["--host", "127.0.0.1", "-p", str(self.port), *args], stdout=out, stderr=err)
        return code, out.getvalue(), err.getvalue()

    def test_start_and_stop_idempotency(self) -> None:
        code, out, err = self._run(["start"])
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertIn("status: already running (no-op)", out)

        with mock.patch("android_agent_cli.handlers.adb.remove_forward"):
            code_stop, out_stop, _ = self._run(["stop"])
            self.assertEqual(code_stop, 0)
            self.assertIn("status: stopped", out_stop)

            code_stop2, out_stop2, _ = self._run(["stop"])
            self.assertEqual(code_stop2, 0)
            self.assertIn("status: already stopped (no-op)", out_stop2)

    def test_screen_writes_binary_file_and_reports_coords_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_file = Path(tmp) / "shot.jpg"
            code, out, err = self._run(["screen", "--out", str(out_file)])
            self.assertEqual(code, 0)
            self.assertEqual(err, "")
            self.assertTrue(out_file.is_file())
            self.assertEqual(out_file.read_bytes(), b"\xff\xd8\xff\xe0MOCKJPEG")
            self.assertNotIn("MOCKJPEG", out)
            self.assertIn("width: 540", out)
            self.assertIn("height: 1200", out)
            self.assertIn("source: uiautomation", out)
            self.assertEqual(self.srv.requests[-1][2].get("fast"), "false")
            self.assertNotIn("cache_hit:", out)
            self.assertIn("coords_note:", out)

    def test_screen_crop_validation_forwarding_and_cache_hit(self) -> None:
        req_before = len(self.srv.requests)
        for bad_crop in ("10,20,30", "10,20,30,40,50", "a,0,100,100", "-1,0,100,100", "0,-2,100,100", "0,0,0,100", "0,0,100,-5"):
            code_bad, out_bad, err_bad = self._run(["screen", "--crop", bad_crop])
            self.assertEqual(code_bad, 2, msg=f"expected exit 2 for crop={bad_crop}")
            self.assertEqual(err_bad, "")
            self.assertIn("error:", out_bad)
        self.assertEqual(len(self.srv.requests), req_before)

        self.srv.screenshot_cache_hit = True
        with tempfile.TemporaryDirectory() as tmp:
            out_file = Path(tmp) / "crop.jpg"
            code, out, err = self._run(["screen", "--crop", "10,20,200,300", "--out", str(out_file)])
            self.assertEqual(code, 0)
            self.assertEqual(err, "")
            self.assertTrue(out_file.is_file())
            self.assertIn("width: 200", out)
            self.assertIn("height: 300", out)
            self.assertIn('crop: "10,20,200,300"', out)
            self.assertIn("cache_hit: true", out)
            self.assertIn("(px+10)/0.5", out)
            method, path, query = self.srv.requests[-1]
            self.assertEqual((method, path), ("GET", "/screenshot"))
            self.assertEqual(query.get("crop"), "10,20,200,300")

            out_full = Path(tmp) / "crop_full.jpg"
            code_f, out_f, err_f = self._run(["screen", "--scale", "1.0", "--crop", "50,60,200,300", "--out", str(out_full)])
            self.assertEqual(code_f, 0)
            self.assertEqual(err_f, "")
            self.assertIn('crop: "50,60,200,300"', out_f)
            self.assertIn("add (50,60)", out_f)

    def test_ui_table_xml_truncation_and_empty_state(self) -> None:
        code, out, _ = self._run(["ui"])
        self.assertEqual(code, 0)
        self.assertIn("nodes[3]{i,label,res_id,cx,cy,act}:", out)
        self.assertIn("Wi-Fi", out)

        code_empty, out_empty, _ = self._run(["ui", "--text", "DoesNotExist"])
        self.assertEqual(code_empty, 0)
        self.assertIn("0 UI elements found", out_empty)

        self.srv.hierarchy_xml = "<hierarchy rotation=\"0\">" + ("x" * 2500) + "</hierarchy>"
        code_xml, out_xml, _ = self._run(["ui", "--xml"])
        self.assertEqual(code_xml, 0)
        self.assertIn("truncated", out_xml)
        self.assertIn("--xml --full", out_xml)

        code_full, out_full, _ = self._run(["ui", "--xml", "--full"])
        self.assertEqual(code_full, 0)
        self.assertNotIn("truncated", out_full)

    def test_tap_selector_reuses_keep_alive_and_handles_ambiguity(self) -> None:
        before_conns = self.srv.connection_count
        code, out, err = self._run(["tap", "--text", "Wi-Fi"])
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        # GET /hierarchy and POST /input/tap share a single Keep-Alive TCP socket.
        self.assertEqual(self.srv.connection_count - before_conns, 1)
        self.assertIn("x: 540", out)
        self.assertIn("y: 280", out)
        self.assertIn("source: direct", out)

        code_xy, out_xy, _ = self._run(["tap", "100", "200", "--no-sync"])
        self.assertEqual(code_xy, 0)
        self.assertIn("source: direct", out_xy)
        self.assertEqual(self.srv.requests[-1][2]["sync"], False)

        # Ambiguous selector exits 1 with inline candidates table and does not POST tap.
        req_count_before = len(self.srv.requests)
        code_amb, out_amb, _ = self._run(["tap", "--text", "Item"])
        self.assertEqual(code_amb, 1)
        self.assertIn("candidates[2]{index,label,res_id,cx,cy}:", out_amb)
        self.assertIn("--index", out_amb)
        self.assertEqual(len(self.srv.requests), req_count_before + 1)

        # Disambiguate with --index 1.
        code_idx, out_idx, _ = self._run(["tap", "--text", "Item", "--index", "1"])
        self.assertEqual(code_idx, 0)
        self.assertIn("label: Item B", out_idx)
        self.assertIn("y: 650", out_idx)

    def test_swipe_directional_hierarchy_and_screenshot_fallback(self) -> None:
        code, out, _ = self._run(["swipe", "up", "--distance", "0.5"])
        self.assertEqual(code, 0)
        self.assertIn("direction: up", out)
        self.assertIn("y1: 1800", out)
        self.assertIn("y2: 600", out)

        # Empty hierarchy triggers /screenshot fallback for display bounds.
        self.srv.hierarchy_xml = '<hierarchy rotation="0"/>'
        code_fb, out_fb, _ = self._run(["swipe", "left", "--distance", "0.5"])
        self.assertEqual(code_fb, 0)
        self.assertIn("direction: left", out_fb)
        self.assertIn("x1: 810", out_fb)
        self.assertIn("x2: 270", out_fb)
        shot_queries = [q for m, path, q in self.srv.requests if path == "/screenshot"]
        self.assertTrue(shot_queries)
        # The fallback must not start the agent's persistent VirtualDisplay mirror.
        self.assertEqual(shot_queries[-1].get("fast"), "false")

    def test_refused_injection_exits_nonzero(self) -> None:
        # `ok: false` means nothing was injected; exit 0 would read as success.
        self.srv.input_ok = False
        code, out, _ = self._run(["tap", "10", "20"])
        self.assertEqual(code, 1, msg=out)
        self.assertIn("could not inject", out)
        code_t, out_t, _ = self._run(["type", "中文"])
        self.assertEqual(code_t, 1, msg=out_t)
        self.assertIn("focused editable field", out_t)

    def test_swipe_easing_and_info_display_size(self) -> None:
        # Default easing is linear, matching `adb shell input swipe` so lists fling.
        code, out, _ = self._run(["swipe", "500", "1800", "500", "400"])
        self.assertEqual(code, 0, msg=out)
        self.assertEqual(self.srv.requests[-1][1], "/input/swipe")
        self.assertEqual(self.srv.requests[-1][2]["easing"], "linear")

        code_e, _, _ = self._run(["swipe", "500", "1800", "500", "400", "--easing", "ease"])
        self.assertEqual(code_e, 0)
        self.assertEqual(self.srv.requests[-1][2]["easing"], "ease")

        code_bad, out_bad, _ = self._run(["swipe", "up", "--easing", "bounce"])
        self.assertEqual(code_bad, 2, msg=out_bad)

        # /info's current-rotation size wins over a hierarchy dump.
        self.srv.info_extra = {"width": 720, "height": 1640, "rotation": 0}
        before = len(self.srv.requests)
        code_i, out_i, _ = self._run(["swipe", "up", "--distance", "0.5"])
        self.assertEqual(code_i, 0, msg=out_i)
        self.assertIn("y1: 1230", out_i)
        self.assertIn("y2: 410", out_i)
        self.assertNotIn("/hierarchy", [path for _, path, _ in self.srv.requests[before:]])

    def test_key_global_and_batch_validation(self) -> None:
        code_k, out_k, _ = self._run(["key", "enter,back,KEYCODE_SPACE,66"])
        self.assertEqual(code_k, 0)
        self.assertIn("keyCodes[4]: 66,4,KEYCODE_SPACE,66", out_k)
        self.assertIn("source: direct", out_k)

        code_g, out_g, _ = self._run(["global", "home"])
        self.assertEqual(code_g, 0)
        self.assertIn("action: home", out_g)

        # move before down fails locally with exit 2 and sends zero HTTP requests.
        req_before = len(self.srv.requests)
        code_bad, out_bad, _ = self._run(["batch", '[{"type":"move","x":10,"y":20}]'])
        self.assertEqual(code_bad, 2)
        self.assertIn("requires a preceding `down`", out_bad)
        self.assertEqual(len(self.srv.requests), req_before)

        valid_batch = json.dumps(
            [
                {"type": "down", "x": 100, "y": 200},
                {"type": "move", "x": 150, "y": 250},
                {"type": "up"},
                {"type": "text", "text": "hello", "clear": True},
            ]
        )
        code_ok, out_ok, _ = self._run(["batch", valid_batch])
        self.assertEqual(code_ok, 0)
        self.assertIn("actions: 4", out_ok)
        self.assertIn("source: direct", out_ok)

    def test_type_wait_and_launch_commands(self) -> None:
        # 1. type with selector taps element, types text, and submits over one Keep-Alive socket.
        conns_before = self.srv.connection_count
        code_t, out_t, err_t = self._run(["type", "wifi_pass", "--text", "Wi-Fi", "--clear", "--submit"])
        self.assertEqual(code_t, 0, msg=out_t)
        self.assertEqual(err_t, "")
        self.assertEqual(self.srv.connection_count - conns_before, 1)
        self.assertIn("text: wifi_pass", out_t)
        self.assertIn("clear: true", out_t)
        self.assertIn("submit: true", out_t)
        self.assertIn("target: Wi-Fi", out_t)
        self.assertIn("source: direct", out_t)

        # type without text and without --clear fails with exit 2.
        code_bad_t, out_bad_t, _ = self._run(["type"])
        self.assertEqual(code_bad_t, 2)
        self.assertIn("requires a <text> argument", out_bad_t)

        # 2. wait matches immediately when present, supports --gone, and exits 1 on timeout.
        code_w, out_w, _ = self._run(["wait", "--text", "Wi-Fi", "--timeout", "1"])
        self.assertEqual(code_w, 0)
        self.assertIn("status: matched", out_w)
        self.assertIn("Wi-Fi", out_w)

        code_g, out_g, _ = self._run(["wait", "--text", "NonExistent", "--gone", "--timeout", "1"])
        self.assertEqual(code_g, 0)
        self.assertIn("status: gone", out_g)

        code_to, out_to, _ = self._run(["wait", "--text", "NonExistent", "--timeout", "0.15", "--interval", "50"])
        self.assertEqual(code_to, 1)
        self.assertIn("timed out after 0.15s", out_to)

        # 3. launch resolves the launcher activity on the device and starts it with am start.
        with mock.patch("android_agent_cli.handlers.adb.resolve_serial", return_value="SER1"), mock.patch(
            "android_agent_cli.handlers.adb.run_adb"
        ) as mock_adb:
            mock_adb.return_value = mock.Mock(
                returncode=0, stdout="component=com.example.userapp/.MainActivity\nStarting: Intent", stderr=""
            )
            code_l, out_l, _ = self._run(["launch", "com.example.userapp", "--stop", "--wait"])
            self.assertEqual(code_l, 0, msg=out_l)
            self.assertIn("status: launched", out_l)
            self.assertIn("component: com.example.userapp/.MainActivity", out_l)
            self.assertIn("stopped_first: true", out_l)
            self.assertEqual(mock_adb.call_count, 2)
            start_cmd = mock_adb.call_args_list[1].args[0][1]
            self.assertIn("query-activities", start_cmd)
            self.assertIn("-f 0x10200000", start_cmd)

        # Package names reach a shell command, so anything outside the package grammar is refused.
        code_bad, out_bad, _ = self._run(["launch", "com.example;reboot"])
        self.assertEqual(code_bad, 2)
        self.assertIn("invalid package name", out_bad)

    def test_packages_package_signature_icon_and_device(self) -> None:
        code_p, out_p, _ = self._run(["packages"])
        self.assertEqual(code_p, 0)
        self.assertIn('count: "2 of 2 packages (1 user, 1 system)"', out_p)
        self.assertIn("packages[2]{packageName,label,versionName,enabled}:", out_p)

        # package combines /packages + /signature over one Keep-Alive connection.
        conns_before = self.srv.connection_count
        code_d, out_d, _ = self._run(["package", "com.example.userapp"])
        self.assertEqual(code_d, 0)
        self.assertEqual(self.srv.connection_count - conns_before, 1)
        self.assertIn("signingSchemes: V2+V3", out_d)

        code_s, out_s, _ = self._run(["signature", "com.android.settings"])
        self.assertEqual(code_s, 0)
        self.assertIn("schemes: none", out_s)

        with tempfile.TemporaryDirectory() as tmp:
            icon_out = Path(tmp) / "app.png"
            code_i, out_i, _ = self._run(["icon", "com.example.userapp", "--out", str(icon_out)])
            self.assertEqual(code_i, 0)
            self.assertTrue(icon_out.is_file())
            self.assertIn("bytes: 12", out_i)

        code_dev, out_dev, _ = self._run(["device"])
        self.assertEqual(code_dev, 0)
        self.assertIn("model: IN2010", out_dev)
        self.assertIn("sensors: 3", out_dev)

        code_props, out_props, _ = self._run(["device", "--section", "properties", "--query", "ro.build"])
        self.assertEqual(code_props, 0)
        self.assertIn("count: 2 of 2 properties (3 total)", out_props)

        code_sens, out_sens, _ = self._run(["device", "--section", "sensors", "--limit", "1"])
        self.assertEqual(code_sens, 0)
        self.assertIn("count: 1 of 3 entries", out_sens)
        self.assertIn("--section sensors --full", out_sens)

    def test_status_and_devices_with_mocked_adb(self) -> None:
        fake_devices = [
            adb.AdbDevice(serial="ABC123", state="device", model="IN2010", product="OnePlus8"),
            adb.AdbDevice(serial="XYZ789", state="device", model="Pixel", product="oriole"),
        ]
        # Per device: ABC123 runs an agent reachable through its own forward (the mock server),
        # XYZ789 runs none. No forward may be created by status.
        with mock.patch("android_agent_cli.handlers.adb.list_devices", return_value=fake_devices), mock.patch(
            "android_agent_cli.handlers.adb.agent_process_running", side_effect=lambda s: s == "ABC123"
        ), mock.patch(
            "android_agent_cli.handlers.adb.find_forward", side_effect=lambda s, p: self.port if s == "ABC123" else None
        ), mock.patch("android_agent_cli.handlers.adb.ensure_forward") as ensure:
            out = io.StringIO()
            code = main(["status"], stdout=out, stderr=io.StringIO())
            text = out.getvalue()
            self.assertEqual(code, 0, msg=text)
            self.assertIn("ABC123", text)
            self.assertIn("XYZ789", text)
            self.assertIn("running", text)
            self.assertIn("stopped", text)
            self.assertIn("agentVersion", text)
            ensure.assert_not_called()

            code_d, out_d, _ = self._run(["devices"])
            self.assertEqual(code_d, 0)
            self.assertIn("count: 2 connected", out_d)

    def test_adb_find_sdk_fallback_and_push_jar_sha256(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sdk_root = Path(tmp) / "sdk"
            pt = sdk_root / "platform-tools"
            pt.mkdir(parents=True)
            adb_exe = pt / ("adb.exe" if sys.platform == "win32" else "adb")
            adb_exe.write_bytes(b"stub")

            with mock.patch("android_agent_cli.adb.shutil.which", return_value=None), mock.patch.dict(
                "os.environ", {"ANDROID_HOME": str(sdk_root)}, clear=False
            ):
                self.assertEqual(adb.find_adb(), str(adb_exe))

            jar = Path(tmp) / "agent-server.jar"
            jar.write_bytes(b"same-len-new-jar")
            expected_sha = adb._local_file_sha256(jar)

            with mock.patch("android_agent_cli.adb.remote_file_sha256", return_value=expected_sha), mock.patch(
                "android_agent_cli.adb.run_adb"
            ) as mock_run:
                self.assertFalse(adb.push_jar("SER1", jar))
                mock_run.assert_not_called()

            with mock.patch("android_agent_cli.adb.remote_file_sha256", return_value="0" * 64), mock.patch(
                "android_agent_cli.adb.run_adb"
            ) as mock_run:
                self.assertTrue(adb.push_jar("SER1", jar))
                mock_run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
