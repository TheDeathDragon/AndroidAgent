from __future__ import annotations

import statistics
import subprocess
import sys
import time
import unittest
from pathlib import Path

CLI_DIR = Path(__file__).resolve().parent.parent
ENTRY_SCRIPT = CLI_DIR / "android_agent.py"

sys.path.insert(0, str(CLI_DIR))
from android_agent_cli._version import VERSION


class TestVersionFastPath(unittest.TestCase):
    def test_version_flags_print_bare_version_and_exit_zero(self) -> None:
        for flag in ("-v", "-V", "--version"):
            proc = subprocess.run(
                [sys.executable, str(ENTRY_SCRIPT), flag],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(proc.returncode, 0, msg=f"flag {flag} stderr={proc.stderr}")
            self.assertEqual(proc.stdout.strip(), VERSION)
            self.assertEqual(proc.stderr, "")

    def test_version_fast_path_skips_heavy_modules(self) -> None:
        probe_code = (
            "import runpy, sys\n"
            f"sys.argv = [{str(ENTRY_SCRIPT)!r}, '--version']\n"
            "try:\n"
            f"    runpy.run_path({str(ENTRY_SCRIPT)!r}, run_name='__main__')\n"
            "except SystemExit as exc:\n"
            "    assert exc.code == 0, exc.code\n"
            "forbidden = [\n"
            "    'http.client', 'xml.etree', 'subprocess', 'json', 'pathlib',\n"
            "    'android_agent_cli.app', 'android_agent_cli.spec',\n"
            "    'android_agent_cli.handlers', 'android_agent_cli.client',\n"
            "]\n"
            "loaded = [m for m in forbidden if m in sys.modules]\n"
            "print('LOADED:' + ','.join(loaded))\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", probe_code],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        self.assertIn(VERSION, lines)
        self.assertIn("LOADED:", lines)

    def test_version_latency_close_to_python_startup_floor(self) -> None:
        def measure_median(cmd: list[str], runs: int = 5) -> float:
            samples: list[float] = []
            for _ in range(runs):
                t0 = time.perf_counter()
                res = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
                self.assertEqual(res.returncode, 0)
                samples.append((time.perf_counter() - t0) * 1000.0)
            return statistics.median(samples)

        # Warm the filesystem cache before timing both paths.
        subprocess.run([sys.executable, "-c", "print(1)"], stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
        subprocess.run([sys.executable, str(ENTRY_SCRIPT), "--version"], stdin=subprocess.DEVNULL, capture_output=True, timeout=10)

        floor_ms = measure_median([sys.executable, "-c", "print(1)"])
        version_ms = measure_median([sys.executable, str(ENTRY_SCRIPT), "--version"])
        # Allow headroom for CI jitter while still catching accidental heavy graph imports.
        budget_ms = max(floor_ms * 2.5, floor_ms + 80.0)
        self.assertLessEqual(
            version_ms,
            budget_ms,
            msg=f"--version median {version_ms:.1f}ms exceeded budget {budget_ms:.1f}ms (floor {floor_ms:.1f}ms)",
        )


if __name__ == "__main__":
    unittest.main()
