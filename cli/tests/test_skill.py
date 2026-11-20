from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from android_agent_cli.app import main
from android_agent_cli.handlers import default_skill_path
from android_agent_cli.spec import COMMANDS, render_skill_md


class TestSkill(unittest.TestCase):
    def test_committed_skill_passes_check(self) -> None:
        out = io.StringIO()
        err = io.StringIO()
        code = main(["skill", "--check"], stdout=out, stderr=err)
        self.assertEqual(code, 0, msg=out.getvalue())
        self.assertEqual(err.getvalue(), "")
        self.assertIn("status: up to date", out.getvalue())

    def test_skill_check_fails_on_drift_with_actionable_hint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stale = Path(tmp) / "SKILL.md"
            stale.write_text("# stale\n", encoding="utf-8")
            out = io.StringIO()
            err = io.StringIO()
            code = main(["skill", "--check", "--out", str(stale)], stdout=out, stderr=err)
            self.assertEqual(code, 1)
            self.assertEqual(err.getvalue(), "")
            self.assertIn("SKILL.md is out of date", out.getvalue())
            self.assertIn("error:", out.getvalue())
            self.assertIn("android-agent skill --write", out.getvalue())

            out_write = io.StringIO()
            code_w = main(["skill", "--write", "--out", str(stale)], stdout=out_write, stderr=io.StringIO())
            self.assertEqual(code_w, 0)
            self.assertIn("status: written", out_write.getvalue())

            out_noop = io.StringIO()
            code_n = main(["skill", "--write", "--out", str(stale)], stdout=out_noop, stderr=io.StringIO())
            self.assertEqual(code_n, 0)
            self.assertIn("unchanged (no-op)", out_noop.getvalue())

    def test_skill_contains_frontmatter_all_commands_and_no_live_state(self) -> None:
        rendered = render_skill_md()
        self.assertTrue(rendered.startswith("---\nname: android-agent\ndescription:"))
        self.assertIn("python cli/android_agent.py", rendered)
        for cmd in COMMANDS:
            self.assertIn(f"### `{cmd.name}`", rendered)
        self.assertNotIn("127.0.0.1:9500, ", rendered)
        self.assertTrue(default_skill_path().is_file())


if __name__ == "__main__":
    unittest.main()
