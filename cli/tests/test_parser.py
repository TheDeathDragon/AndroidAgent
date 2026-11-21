from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from android_agent_cli.app import main
from android_agent_cli.errors import UsageError
from android_agent_cli.parser import parse_argv


class TestParser(unittest.TestCase):
    def test_unknown_flag_exits_2_with_inline_valid_flags(self) -> None:
        out = io.StringIO()
        err = io.StringIO()
        code = main(["ui", "--bogus", "123"], stdout=out, stderr=err)
        self.assertEqual(code, 2)
        self.assertEqual(err.getvalue(), "")
        text = out.getvalue()
        self.assertIn("error: unknown flag --bogus for `ui`", text)
        self.assertIn("--query", text)
        self.assertIn("--help", text)
        self.assertIn("--serial", text)

    def test_renamed_flag_hint_global_and_command(self) -> None:
        out = io.StringIO()
        code = main(["--device", "ABC123", "status"], stdout=out, stderr=io.StringIO())
        self.assertEqual(code, 2)
        self.assertIn("--serial", out.getvalue())

        out2 = io.StringIO()
        code2 = main(["screen", "--output", "a.png"], stdout=out2, stderr=io.StringIO())
        self.assertEqual(code2, 2)
        self.assertIn("--out", out2.getvalue())

    def test_globals_allowed_before_and_after_command(self) -> None:
        inv1 = parse_argv(["-s", "SER1", "-p", "9600", "--host", "127.0.0.1", "ui", "--clickable"])
        self.assertEqual(inv1.command, "ui")
        self.assertEqual(inv1.globals["serial"], "SER1")
        self.assertEqual(inv1.globals["port"], 9600)
        self.assertTrue(inv1.flags["clickable"])

        inv2 = parse_argv(["ui", "--clickable", "--serial=SER2", "-p", "9700"])
        self.assertEqual(inv2.command, "ui")
        self.assertEqual(inv2.globals["serial"], "SER2")
        self.assertEqual(inv2.globals["port"], 9700)

    def test_negative_numeric_positionals_and_double_dash(self) -> None:
        inv = parse_argv(["tap", "-10", "-20"])
        self.assertEqual(inv.positionals, ["-10", "-20"])

        inv2 = parse_argv(["swipe", "-5", "10", "-5", "200"])
        self.assertEqual(inv2.positionals, ["-5", "10", "-5", "200"])

        inv3 = parse_argv(["key", "--", "-1"])
        self.assertEqual(inv3.positionals, ["-1"])

    def test_tap_rejects_mixing_coords_and_selector_exit_2(self) -> None:
        out = io.StringIO()
        code = main(["tap", "100", "200", "--text", "OK"], stdout=out, stderr=io.StringIO())
        self.assertEqual(code, 2)
        self.assertIn("cannot combine", out.getvalue())

        out_empty = io.StringIO()
        code_empty = main(["tap"], stdout=out_empty, stderr=io.StringIO())
        self.assertEqual(code_empty, 2)

    def test_invalid_choices_and_ranges_fail_before_network(self) -> None:
        with self.assertRaises(UsageError):
            parse_argv(["screen", "--scale", "2.5"])
        with self.assertRaises(UsageError):
            parse_argv(["screen", "--format", "webp"])
        with self.assertRaises(UsageError):
            parse_argv(["device", "--section", "nonexistent"])
        with self.assertRaises(UsageError):
            parse_argv(["setup", "--target", "vim"])
        with self.assertRaises(UsageError):
            parse_argv(["unknown-cmd"])

    def test_negated_bool_flag(self) -> None:
        inv = parse_argv(["screen", "--fast"])
        self.assertTrue(inv.flags["fast"])
        self.assertFalse(parse_argv(["screen", "--fast", "--no-fast"]).flags["fast"])
        # Off by default: a live mirror makes the device compose an extra display every frame.
        inv_default = parse_argv(["screen"])
        self.assertFalse(inv_default.flags["fast"])


if __name__ == "__main__":
    unittest.main()
