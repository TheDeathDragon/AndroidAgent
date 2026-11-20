from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from android_agent_cli.setup_hooks import HookCommand, ensure_codex_features_hooks, run_setup


class TestSetupHooks(unittest.TestCase):
    def test_install_idempotent_repair_dry_run_and_remove_all_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            proj = Path(tmp) / "proj"
            home.mkdir()
            proj.mkdir()

            # Pre-seed an unrelated Claude hook and a commented Codex config.toml.
            claude_path = home / ".claude" / "settings.json"
            claude_path.parent.mkdir(parents=True)
            claude_path.write_text(
                json.dumps(
                    {
                        "theme": "dark",
                        "hooks": {
                            "SessionStart": [
                                {"hooks": [{"type": "command", "command": "other-tool --hook"}]}
                            ]
                        },
                    }
                ),
                encoding="utf-8",
            )

            codex_toml = home / ".codex" / "config.toml"
            codex_toml.parent.mkdir(parents=True)
            codex_toml.write_text(
                "# user header comment\n"
                'model = "o3"\n\n'
                "[features]\n"
                "# keep this comment\n"
                "shell = true\n"
                "hooks = false\n\n"
                "[other]\n"
                "enabled = true\n",
                encoding="utf-8",
            )

            cmd_v1 = HookCommand("android-agent --hook", ("android-agent", "--hook"))

            # Dry run makes no filesystem changes.
            dry = run_setup(target="all", scope="user", dry_run=True, home_dir=home, project_dir=proj, hook_cmd=cmd_v1)
            statuses = {r["target"]: r["status"] for r in dry["results"]}
            self.assertEqual(statuses["claude"], "would install")
            self.assertEqual(statuses["codex"], "would install")
            self.assertEqual(statuses["codex-config"], "would update")
            self.assertEqual(statuses["opencode"], "would install")
            self.assertFalse((home / ".codex" / "hooks.json").exists())
            self.assertFalse((home / ".config" / "opencode" / "plugins" / "android-agent.js").exists())

            # Real install.
            inst = run_setup(target="all", scope="user", home_dir=home, project_dir=proj, hook_cmd=cmd_v1)
            statuses = {r["target"]: r["status"] for r in inst["results"]}
            self.assertEqual(statuses["claude"], "installed")
            self.assertEqual(statuses["codex"], "installed")
            self.assertEqual(statuses["codex-config"], "updated")
            self.assertEqual(statuses["opencode"], "installed")

            # Verify Claude preserved unrelated settings and hooks.
            claude_data = json.loads(claude_path.read_text(encoding="utf-8"))
            self.assertEqual(claude_data["theme"], "dark")
            self.assertEqual(len(claude_data["hooks"]["SessionStart"]), 2)

            # Verify Codex TOML preserved comments and unrelated sections.
            toml_text = codex_toml.read_text(encoding="utf-8")
            self.assertIn("# user header comment", toml_text)
            self.assertIn("# keep this comment", toml_text)
            self.assertIn("shell = true", toml_text)
            self.assertIn("hooks = true", toml_text)
            self.assertNotIn("hooks = false", toml_text)
            self.assertIn("[other]\nenabled = true", toml_text)

            # Second run with same command is an idempotent no-op.
            noop = run_setup(target="all", scope="user", home_dir=home, project_dir=proj, hook_cmd=cmd_v1)
            for row in noop["results"]:
                self.assertEqual(row["status"], "unchanged (no-op)")

            # Path repair when executable relocates.
            cmd_v2 = HookCommand('"/opt/bin/android-agent" --hook', ("/opt/bin/android-agent", "--hook"))
            repaired = run_setup(target="all", scope="user", home_dir=home, project_dir=proj, hook_cmd=cmd_v2)
            r_statuses = {r["target"]: r["status"] for r in repaired["results"]}
            self.assertEqual(r_statuses["claude"], "repaired")
            self.assertEqual(r_statuses["codex"], "repaired")
            self.assertEqual(r_statuses["opencode"], "repaired")
            opencode_js = (home / ".config" / "opencode" / "plugins" / "android-agent.js").read_text(encoding="utf-8")
            self.assertIn("/opt/bin/android-agent", opencode_js)

            # Remove cleans android-agent while preserving unrelated Claude hook.
            rem = run_setup(target="all", scope="user", remove=True, home_dir=home, project_dir=proj, hook_cmd=cmd_v2)
            rem_statuses = {r["target"]: r["status"] for r in rem["results"]}
            self.assertEqual(rem_statuses["claude"], "removed")
            self.assertEqual(rem_statuses["codex"], "removed")
            self.assertEqual(rem_statuses["opencode"], "removed")
            claude_after = json.loads(claude_path.read_text(encoding="utf-8"))
            self.assertEqual(len(claude_after["hooks"]["SessionStart"]), 1)
            self.assertEqual(claude_after["hooks"]["SessionStart"][0]["hooks"][0]["command"], "other-tool --hook")
            self.assertFalse((home / ".config" / "opencode" / "plugins" / "android-agent.js").exists())

            # Second remove is a no-op.
            rem2 = run_setup(target="all", scope="user", remove=True, home_dir=home, project_dir=proj, hook_cmd=cmd_v2)
            for row in rem2["results"]:
                self.assertEqual(row["status"], "not installed (no-op)")

    def test_codex_toml_append_features_section_when_missing(self) -> None:
        updated, changed = ensure_codex_features_hooks('model = "o3"\n')
        self.assertTrue(changed)
        self.assertEqual(updated, 'model = "o3"\n\n[features]\nhooks = true\n')

        updated2, changed2 = ensure_codex_features_hooks(updated)
        self.assertFalse(changed2)
        self.assertEqual(updated2, updated)


if __name__ == "__main__":
    unittest.main()
