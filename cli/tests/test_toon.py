from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from android_agent_cli.toon import encode, truncate_text


class TestToon(unittest.TestCase):
    def test_primitives_and_bool_before_int(self) -> None:
        out = encode(
            {
                "nil": None,
                "yes": True,
                "no": False,
                "count": 42,
                "neg_zero": -0.0,
                "whole_float": 5.0,
                "ratio": 0.5,
                "nan": math.nan,
                "inf": math.inf,
            }
        )
        expected = (
            "nil: null\n"
            "yes: true\n"
            "no: false\n"
            "count: 42\n"
            "neg_zero: 0\n"
            "whole_float: 5\n"
            "ratio: 0.5\n"
            "nan: null\n"
            "inf: null\n"
        )
        self.assertEqual(out, expected)

    def test_string_quoting_rules(self) -> None:
        out = encode(
            {
                "plain": "hello world",
                "empty": "",
                "lead_ws": " abc",
                "trail_ws": "abc ",
                "kw_true": "true",
                "kw_false": "false",
                "kw_null": "null",
                "num_int": "123",
                "num_neg": "-4.5",
                "num_sci": "1e5",
                "lead_zero": "01",
                "dash_start": "-flag",
                "comma": "a,b",
                "colon": "k:v",
                "quote": 'say "hi"',
                "slash": r"C:\tmp",
                "brackets": "[0,0]",
                "braces": "{a}",
                "ctrl": "line1\nline2\t\r",
            }
        )
        lines = dict(line.split(": ", 1) for line in out.splitlines())
        self.assertEqual(lines["plain"], "hello world")
        self.assertEqual(lines["empty"], '""')
        self.assertEqual(lines["lead_ws"], '" abc"')
        self.assertEqual(lines["trail_ws"], '"abc "')
        self.assertEqual(lines["kw_true"], '"true"')
        self.assertEqual(lines["kw_false"], '"false"')
        self.assertEqual(lines["kw_null"], '"null"')
        self.assertEqual(lines["num_int"], '"123"')
        self.assertEqual(lines["num_neg"], '"-4.5"')
        self.assertEqual(lines["num_sci"], '"1e5"')
        self.assertEqual(lines["lead_zero"], '"01"')
        self.assertEqual(lines["dash_start"], '"-flag"')
        self.assertEqual(lines["comma"], '"a,b"')
        self.assertEqual(lines["colon"], '"k:v"')
        self.assertEqual(lines["quote"], r'"say \"hi\""')
        self.assertEqual(lines["slash"], r'"C:\\tmp"')
        self.assertEqual(lines["brackets"], '"[0,0]"')
        self.assertEqual(lines["braces"], '"{a}"')
        self.assertEqual(lines["ctrl"], r'"line1\nline2\t\r"')

    def test_key_quoting(self) -> None:
        out = encode({"valid_key.1-a": 1, "1bad": 2, "has space": 3, "a:b": 4})
        self.assertEqual(
            out,
            'valid_key.1-a: 1\n"1bad": 2\n"has space": 3\n"a:b": 4\n',
        )

    def test_arrays_empty_inline_tabular_and_mixed(self) -> None:
        out = encode(
            {
                "empty": [],
                "tags": ["a", "b,c", 3, True],
                "rows": [
                    {"id": "1", "name": "Wi-Fi", "ok": True},
                    {"id": "2", "name": "BT", "ok": False},
                ],
                "mixed": [
                    {"a": 1, "sub": {"x": 2}},
                    "raw",
                    {},
                ],
            }
        )
        expected = (
            "empty[0]:\n"
            'tags[4]: a,"b,c",3,true\n'
            "rows[2]{id,name,ok}:\n"
            '  "1",Wi-Fi,true\n'
            '  "2",BT,false\n'
            "mixed[3]:\n"
            "  - a: 1\n"
            "    sub:\n"
            "      x: 2\n"
            "  - raw\n"
            "  -\n"
        )
        self.assertEqual(out, expected)
        for line in out.splitlines():
            self.assertFalse(line.endswith(" "), f"trailing space in: {line!r}")
        self.assertTrue(out.endswith("\n"))
        self.assertFalse(out.endswith("\n\n"))

    def test_non_uniform_dicts_fall_back_to_expanded_list(self) -> None:
        out = encode({"items": [{"a": 1, "b": 2}, {"b": 2, "a": 1}]})
        self.assertIn("items[2]:\n  - a: 1\n", out)

    def test_truncate_text(self) -> None:
        short, trunc, total = truncate_text("hello", 10)
        self.assertEqual((short, trunc, total), ("hello", False, 5))

        preview, trunc, total = truncate_text("abcdefghij", 4)
        self.assertTrue(trunc)
        self.assertEqual(total, 10)
        self.assertEqual(preview, "abcd... (truncated, 10 chars total)")


if __name__ == "__main__":
    unittest.main()
