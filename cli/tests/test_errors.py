from __future__ import annotations

import io
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from android_agent_cli.app import main
from android_agent_cli.errors import RuntimeAxiError


class _BrokenPipeStream(io.StringIO):
    def write(self, s: str) -> int:
        raise BrokenPipeError("pipe closed")


class TestErrors(unittest.TestCase):
    def test_errors_go_to_stdout_as_toon_with_empty_stderr(self) -> None:
        out = io.StringIO()
        err = io.StringIO()
        code = main(["global", "not_a_real_action"], stdout=out, stderr=err)
        self.assertEqual(code, 2)
        self.assertEqual(err.getvalue(), "")
        self.assertIn('error: "unknown global action: not_a_real_action"', out.getvalue())
        self.assertIn("help:", out.getvalue())

    def test_broken_pipe_exits_zero(self) -> None:
        code = main(["--help"], stdout=_BrokenPipeStream(), stderr=io.StringIO())
        self.assertEqual(code, 0)

    def test_connection_error_carries_forward_global_flags(self) -> None:
        out = io.StringIO()
        err = io.StringIO()
        # Use a closed ephemeral port on localhost, reached directly (no adb forwarding).
        code = main(["-s", "DEV99", "--host", "127.0.0.1", "-p", "1", "signature", "com.example.app"], stdout=out, stderr=err)
        self.assertEqual(code, 1)
        self.assertEqual(err.getvalue(), "")
        text = out.getvalue()
        self.assertIn("error:", text)
        self.assertIn("android-agent -s DEV99 -p 1 --host 127.0.0.1 start", text)

    def test_debug_env_emits_traceback_to_stderr_only_when_enabled(self) -> None:
        out = io.StringIO()
        err = io.StringIO()
        with mock.patch.dict(os.environ, {"ANDROID_AGENT_DEBUG": "1"}):
            with mock.patch("android_agent_cli.handlers.adb.list_devices", side_effect=RuntimeAxiError("boom")):
                code = main(["devices"], stdout=out, stderr=err)
        self.assertEqual(code, 1)
        self.assertIn("error: boom", out.getvalue())
        self.assertIn("RuntimeAxiError", err.getvalue())

    def test_hook_mode_always_exits_zero_even_on_runtime_failure(self) -> None:
        out = io.StringIO()
        err = io.StringIO()
        with mock.patch("android_agent_cli.handlers.adb.list_devices", side_effect=RuntimeAxiError("adb missing")):
            with mock.patch("android_agent_cli.handlers.AgentClient.probe_health", return_value=False):
                code = main(["--hook"], stdout=out, stderr=err)
        self.assertEqual(code, 0)
        self.assertEqual(err.getvalue(), "")
        self.assertIn("bin:", out.getvalue())


if __name__ == "__main__":
    unittest.main()
