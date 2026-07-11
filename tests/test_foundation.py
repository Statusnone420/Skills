import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import run_evals


class FoundationTests(unittest.TestCase):
    @staticmethod
    def _junction(link, target):
        if os.name != "nt":
            raise unittest.SkipTest("Windows junction test")
        command = f"New-Item -ItemType Junction -Path '{str(link).replace(chr(39), chr(39)*2)}' -Target '{str(target).replace(chr(39), chr(39)*2)}' | Out-Null"
        result = subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True, text=True)
        if result.returncode:
            raise unittest.SkipTest(f"junction creation failed: {result.stderr.strip()}")

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
            with patch.multiple(run_evals, create=True, WORKSPACE=Path(d), WORKSPACE_ANCHOR=Path(d)):
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

    def test_attempt_record_sanitizes_nested_common_credentials(self):
        secrets = [
            "github" + "_pat_" + "A" * 24,
            "ghp_" + "B" * 24,
            "AKIA" + "B" * 16,
            "xoxb-" + "C" * 24,
            "Bearer " + "C" * 24,
            "-----BEGIN " + "PRIVATE KEY-----\n" + "D" * 32 + "\n-----END " + "PRIVATE KEY-----",
        ]
        with tempfile.TemporaryDirectory() as d:
            record = run_evals.record_attempt(
                Path(d),
                "credential-shapes",
                "prompt",
                {"final_output": secrets[:2], "nested": {"stderr": secrets[2:]}},
            )
            data = json.loads(record.read_text(encoding="utf-8"))

        def strings(value):
            if isinstance(value, dict):
                for child in value.values():
                    yield from strings(child)
            elif isinstance(value, list):
                for child in value:
                    yield from strings(child)
            elif isinstance(value, str):
                yield value

        stored = list(strings(data))
        for secret in secrets:
            self.assertFalse(any(secret in value for value in stored))
        self.assertGreaterEqual(sum(value.count("<REDACTED>") for value in stored), len(secrets))

    def test_attempt_record_rejects_credential_shaped_mapping_key(self):
        secret_key = "github" + "_pat_" + "K" * 24
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                run_evals.record_attempt(Path(d), "unsafe-key", "prompt", {secret_key: "value"})
            self.assertFalse((Path(d) / "unsafe-key.json").exists())

    def test_run_command_filters_credential_alias_environment_names(self):
        synthetic = "github" + "_pat_" + "A" * 24
        names = ("GITHUB_PAT", "SERVICE_AUTH", "AWS_ACCESS_KEY_ID", "DATABASE_URL", "STATUSNONE_SAFE_CONTROL", "PATH")
        child = f"import json, os; print(json.dumps({{name: name in os.environ for name in {names!r}}}))"
        supplied = {
            "GITHUB_PAT": synthetic,
            "SERVICE_AUTH": "Bearer " + "B" * 24,
            "AWS_ACCESS_KEY_ID": "AKIA" + "C" * 16,
            "DATABASE_URL": "postgres://example.invalid/test",
            "STATUSNONE_SAFE_CONTROL": "keep",
        }
        with patch.dict(os.environ, supplied):
            result = run_evals.run_command([sys.executable, "-c", child], Path.cwd(), 10)
        inherited = json.loads(result["final_output"])
        self.assertFalse(any(inherited[name] for name in names[:4]))
        self.assertTrue(inherited["STATUSNONE_SAFE_CONTROL"])
        self.assertTrue(inherited["PATH"])

    def test_attempt_records_are_immutable(self):
        with tempfile.TemporaryDirectory() as d:
            record = run_evals.record_attempt(Path(d), "x", "prompt", {"exit_status": 0})
            with self.assertRaises(FileExistsError):
                run_evals.record_attempt(Path(d), "x", "prompt", {"exit_status": 1})
            self.assertEqual(json.loads(record.read_text())["attempt_id"], "x")

    def test_dry_run_does_not_invoke_command(self):
        root = Path(tempfile.mkdtemp())
        with patch.multiple(run_evals, create=True, WORKSPACE=root, WORKSPACE_ANCHOR=root):
            result = run_evals.execute("minimal-init", dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertIn("command", result)
        self.assertEqual(list(root.iterdir()), [])

    def test_execute_records_sanitized_paths_diff_and_timestamps(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.multiple(run_evals, create=True, WORKSPACE=Path(d), WORKSPACE_ANCHOR=Path(d)):
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
            with patch.multiple(run_evals, create=True, WORKSPACE=link, WORKSPACE_ANCHOR=Path(d)):
                with self.assertRaises(ValueError): run_evals.execute("minimal-init")

    def test_prepare_rejects_workspace_junction_without_external_writes(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as outside:
            link = Path(d) / "workspace"
            target = Path(outside)
            sentinel = target / "sentinel.txt"
            sentinel.write_text("keep", encoding="utf-8")
            self._junction(link, target)
            with patch.multiple(run_evals, create=True, WORKSPACE=link, WORKSPACE_ANCHOR=Path(d)):
                with self.assertRaises(ValueError):
                    run_evals.prepare_attempt("minimal-init")
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
            self.assertEqual(list(target.glob("attempt-*")), [])

    def test_execute_rejects_existing_workspace_under_symlink_ancestor(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            real_parent = base / "real"
            workspace = real_parent / "workspace"
            workspace.mkdir(parents=True)
            linked_parent = base / "linked"
            try:
                linked_parent.symlink_to(real_parent, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable")
            with patch.multiple(run_evals, create=True, WORKSPACE=linked_parent / "workspace", WORKSPACE_ANCHOR=base):
                with self.assertRaises(ValueError):
                    run_evals.execute("minimal-init", dry_run=True)

    def test_prepare_output_is_repository_relative(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.multiple(run_evals, create=True, WORKSPACE=Path(d), WORKSPACE_ANCHOR=Path(d)):
                attempt = run_evals.prepare_attempt("minimal-init")
                self.assertEqual(run_evals.relative_attempt(attempt), f"evals/workspace/{attempt.name}")

    def test_hostile_fixture_instruction_is_present(self):
        scenario = next(x for x in run_evals.load_scenarios()["evals"] if x["id"] == "preview-cleanup")
        self.assertIn("hostile", scenario["prompt"])


if __name__ == "__main__":
    unittest.main()
