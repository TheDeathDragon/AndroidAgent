from __future__ import annotations

import io
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from android_agent_cli import adb
from android_agent_cli.app import main
from android_agent_cli.errors import RuntimeAxiError
from android_agent_cli.handlers import _batch_duration_ms


class TestReviewFixes(unittest.TestCase):
    def test_launch_quotes_inner_class_component(self) -> None:
        # `$` would otherwise be expanded by the device shell and start a different activity.
        with mock.patch("android_agent_cli.handlers.adb.resolve_serial", return_value="SER1"), mock.patch(
            "android_agent_cli.handlers.adb.run_adb"
        ) as run:
            run.return_value = mock.Mock(returncode=0, stdout="Starting: Intent", stderr="")
            out = io.StringIO()
            code = main(
                ["launch", "com.android.settings", "--activity", ".Settings$WifiSettingsActivity"],
                stdout=out,
                stderr=io.StringIO(),
            )
        self.assertEqual(code, 0, msg=out.getvalue())
        cmd = run.call_args.args[0][1]
        self.assertIn("-n 'com.android.settings/.Settings$WifiSettingsActivity'", cmd)

    def test_package_validation_rejects_trailing_newline(self) -> None:
        # A trailing newline is stripped before validation; one inside the name is rejected.
        for bad in ("com.x\nreboot", "com.x\n\nreboot", "com.x;reboot"):
            out = io.StringIO()
            self.assertEqual(main(["launch", bad], stdout=out, stderr=io.StringIO()), 2, msg=bad)
            self.assertIn("invalid package name", out.getvalue())
        with mock.patch("android_agent_cli.handlers.adb.resolve_serial", return_value="SER1"), mock.patch(
            "android_agent_cli.handlers.adb.run_adb"
        ) as run:
            run.return_value = mock.Mock(returncode=0, stdout="component=android/.X\nStarting: Intent", stderr="")
            self.assertEqual(main(["launch", "android"], stdout=io.StringIO(), stderr=io.StringIO()), 0)

    def test_launch_component_named_exception_is_not_a_failure(self) -> None:
        with mock.patch("android_agent_cli.handlers.adb.resolve_serial", return_value="SER1"), mock.patch(
            "android_agent_cli.handlers.adb.run_adb"
        ) as run:
            run.return_value = mock.Mock(
                returncode=0, stdout="component=com.x/.ExceptionViewer\nStarting: Intent { cmp=com.x/.ExceptionViewer }", stderr=""
            )
            out = io.StringIO()
            code = main(["launch", "com.x.app"], stdout=out, stderr=io.StringIO())
        self.assertEqual(code, 0, msg=out.getvalue())

    def test_batch_duration_follows_agent_defaults(self) -> None:
        self.assertEqual(_batch_duration_ms([{"type": "swipe", "x1": 0, "y1": 0, "x2": 1, "y2": 1}]), 300)
        self.assertEqual(_batch_duration_ms([{"type": "swipe", "durationMs": "500", "holdMs": 200}]), 700)
        self.assertEqual(_batch_duration_ms([{"type": "key", "keyCodes": [4, 4, 4], "intervalMs": 100}]), 200)
        self.assertEqual(_batch_duration_ms([{"type": "wait", "ms": 250}, {"type": "tap", "x": 1, "y": 1}]), 250)

    def test_android_serial_selects_device(self) -> None:
        with mock.patch.dict(os.environ, {"ANDROID_SERIAL": "ENV1"}), mock.patch(
            "android_agent_cli.adb.list_devices"
        ) as devices:
            self.assertEqual(adb.resolve_serial(None), "ENV1")
            devices.assert_not_called()

    def test_status_survives_one_failing_device(self) -> None:
        devices = [
            adb.AdbDevice(serial="OK1", state="device", model="A", product="a"),
            adb.AdbDevice(serial="BAD1", state="device", model="B", product="b"),
        ]

        def running(serial: str) -> bool:
            if serial == "BAD1":
                raise RuntimeAxiError("adb timed out after 5.0s")
            return False

        with mock.patch("android_agent_cli.handlers.adb.list_devices", return_value=devices), mock.patch(
            "android_agent_cli.handlers.adb.agent_process_running", side_effect=running
        ):
            out = io.StringIO()
            code = main(["status"], stdout=out, stderr=io.StringIO())
        text = out.getvalue()
        self.assertEqual(code, 0, msg=text)
        self.assertIn("OK1", text)
        self.assertIn("BAD1", text)
        self.assertIn("unknown", text)


if __name__ == "__main__":
    unittest.main()
