import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import run_evals


class FoundationTests(unittest.TestCase):
    def test_schema_has_six_diataxis_scenarios(self):
        data = run_evals.load_scenarios()
        self.assertEqual(data["skill_name"], "docs")
        self.assertEqual(len(data["evals"]), 6)
        self.assertTrue(all(item["id"] and item["prompt"] for item in data["evals"]))

    def test_fixture_dimensions_are_exact(self):
        with tempfile.TemporaryDirectory() as d:
            path = run_evals.build_fixture(Path(d))
            raw = path.read_bytes()
            self.assertEqual(len(raw), 290542)
            self.assertEqual(len(raw.splitlines()), 2041)

    def test_attempt_workspace_is_clean_and_confined(self):
        with tempfile.TemporaryDirectory() as d:
            attempt = run_evals.prepare_attempt(Path(d), "minimal-init")
            self.assertEqual((attempt / ".git").is_dir(), True)
            self.assertTrue(run_evals.is_confined(attempt, Path(d)))

    def test_redaction_and_timeout_are_recorded(self):
        text = run_evals.redact("/parent/repo token=sk-abcdefghijklmnopqrstuvwxyz0123456789")
        self.assertNotIn("/parent/repo", text)
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz0123456789", text)
        result = run_evals.run_command([sys.executable, "-c", "import time; time.sleep(1)"], Path.cwd(), 0.01)
        self.assertTrue(result["timed_out"])
        self.assertIn("exit_status", result)

    def test_attempt_records_are_immutable(self):
        with tempfile.TemporaryDirectory() as d:
            record = run_evals.record_attempt(Path(d), "x", "prompt", {"exit_status": 0})
            with self.assertRaises(FileExistsError):
                run_evals.record_attempt(Path(d), "x", "prompt", {"exit_status": 1})
            self.assertEqual(json.loads(record.read_text())["attempt_id"], "x")

    def test_dry_run_does_not_invoke_command(self):
        result = run_evals.execute("minimal-init", dry_run=True, root=Path(tempfile.mkdtemp()))
        self.assertTrue(result["dry_run"])
        self.assertIn("command", result)


if __name__ == "__main__":
    unittest.main()
