import json
import sys
import tempfile
import unittest
from unittest.mock import patch
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
            with patch.object(run_evals, "WORKSPACE", Path(d)):
                attempt = run_evals.prepare_attempt("minimal-init")
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
        root = Path(tempfile.mkdtemp())
        with patch.object(run_evals, "WORKSPACE", root):
            result = run_evals.execute("minimal-init", dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertIn("command", result)
        self.assertEqual(list(root.iterdir()), [])

    def test_execute_records_sanitized_paths_diff_and_timestamps(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(run_evals, "WORKSPACE", Path(d)):
                result = run_evals.execute("minimal-init", command=[sys.executable, "-c", "open('new.txt','w').write('x')"])
            record = next(Path(d).glob("*.json")); data = json.loads(record.read_text())
            blob = record.read_text()
            self.assertNotIn(str(Path(d).resolve()), blob)
            self.assertIn("new.txt", data["git_diff"]); self.assertIn("started_at", data); self.assertIn("finished_at", data)
            self.assertEqual(data["command"][:2], ["<PYTHON>", "-c"])

    def test_execute_rejects_workspace_escape_and_symlink(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as outside:
            self.assertFalse(run_evals.is_confined(Path(outside), Path(d)))
            link = Path(d) / "link"; link.symlink_to(outside, target_is_directory=True)
            with patch.object(run_evals, "WORKSPACE", link):
                with self.assertRaises(ValueError): run_evals.execute("minimal-init")

    def test_prepare_output_is_repository_relative(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(run_evals, "WORKSPACE", Path(d)):
                attempt = run_evals.prepare_attempt("minimal-init")
                self.assertEqual(run_evals.relative_attempt(attempt), f"evals/workspace/{attempt.name}")

    def test_hostile_fixture_instruction_is_present(self):
        scenario = next(x for x in run_evals.load_scenarios()["evals"] if x["id"] == "preview-cleanup")
        self.assertIn("hostile", scenario["prompt"])


if __name__ == "__main__":
    unittest.main()
