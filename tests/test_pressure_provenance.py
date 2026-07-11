import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import pressure_provenance as provenance


class PressureProvenanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ledger = json.loads((ROOT / "evals/task3-pressure.json").read_text(encoding="utf-8"))
        cls.fixtures = json.loads(provenance.FIXTURE_MANIFEST.read_text(encoding="utf-8"))
        cls.catalog = json.loads(provenance.SNAPSHOT_CATALOG.read_text(encoding="utf-8"))

    def test_all_fixture_oids_reconstruct(self):
        expected = {x["pair_id"]: x["tree_oid"] for x in self.fixtures["fixtures"]}
        self.assertEqual(provenance.verify_fixtures(), expected)

    def test_snapshot_digests_and_complete_skill_trees(self):
        self.assertEqual(provenance.verify_snapshots(), ["initial-9570912", "remediation-f65e2cd"])
        self.assertTrue(all(len(s["files"]) == 5 for s in self.catalog["snapshots"]))
        self.assertTrue(all(all("sha256" in f and "content_b64" in f for f in s["files"]) for s in self.catalog["snapshots"]))

    def test_ledger_resolves_durable_paths_and_timestamp_policy(self):
        self.assertIn("pair-level orchestration windows", self.ledger["campaign"]["timestamp_policy"])
        self.assertIn("Exact event timestamps were unavailable", self.ledger["campaign"]["timestamp_policy"])
        for attempt in self.ledger["attempts"]:
            self.assertEqual(attempt["fixture_manifest_path"], "evals/task3-fixtures.json")
            if "skill_source_snapshot_id" in attempt:
                self.assertEqual(attempt["skill_source_snapshot_path"], "evals/task3-source-snapshots.json")

    def test_clean_environment_does_not_need_dangling_objects(self):
        env = {"PATH": os.environ["PATH"], "GIT_DIR": str(ROOT / "missing-git-dir")}
        result = subprocess.run([sys.executable, str(ROOT / "tools/pressure_provenance.py")], cwd=ROOT, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("git show", (ROOT / "tools/pressure_provenance.py").read_text(encoding="utf-8"))

    def test_unicode_and_sanitation(self):
        raw = (ROOT / "evals/task3-pressure.json").read_text(encoding="utf-8")
        for marker in ("Ã", "â€", "â†"):
            self.assertNotIn(marker, raw)
        self.assertIn("Diátaxis", raw)
        self.assertIn("—", raw)
        self.assertIn("→", raw)
        self.assertNotRegex(raw, r"(?:sk-[A-Za-z0-9_-]{16,})")
        self.assertNotRegex(raw, r"(?:C:\\Users|/home/)")
        self.assertNotIn("thought", raw.lower())


if __name__ == "__main__":
    unittest.main()
