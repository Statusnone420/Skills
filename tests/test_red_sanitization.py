import json
import re
import unittest
from pathlib import Path


SCENARIOS = {"minimal-init", "bounded-context", "diataxis-write", "evidence-update", "preview-cleanup", "audit-repair"}
ORIGINAL_IDS = {"attempt-11a290bae9054e9c84d960dd6380f997", "attempt-1ef7d62e13ba40bdb1d882759ed9427f", "attempt-22919eee802e4937b17f68e97a4c629e", "attempt-23d766473e1042d18adaa3558040ba93", "attempt-27557a8333e74dbe922b7c3a2ee5383f", "attempt-5374a7d1985349d79b7999d110eeb603", "attempt-5ba033eb104b434fa388c53afeba716b", "attempt-6eb0a75796e5499a8655ee7eb79afe2c", "attempt-726d89f540854434b1bb5de9a5eb7f41", "attempt-9ad1b4c442024b1992fb5cd8a03d078b", "attempt-9cacab5b777e42cead03ee915b7f6f88", "attempt-a509ffaf2b8646efb9efd9612dc57731", "attempt-b4bae5c1429f4334af5d714839afa0d8", "attempt-d39e9911e59a4438b9f74e092bcd364e", "attempt-d45bf6187b7d4fcd87e01a8105dc88ae", "attempt-da0ffd5ead1d4f3080caddb9c65e0263", "attempt-e2adb465880a4566906137b65d291086", "attempt-e7a013114abf4cff81390c6befdda610", "attempt-f3b70566fab74610a1358c5996834f7b", "attempt-f745f8b31a074aa9b039deb4db87c057", "attempt-f7ec5e4b916e4e28bd8aeebfe8e35b25", "codex-790fab2d36914e30b1b7d6d521ef9213", "codex-806c640f286b4155a417851dcb804405", "codex-9a41cb101e5e466d8a3c5b11b5a79e2a", "codex-acb45082394c480cbd87fbe1f341d361", "codex-cc4ac1848e964e759cf94ffcf193e633"}
PROVENANCE = {"attempt_id", "harness", "scenario", "outcome_type", "status", "sanitized_workspace", "invocation_method", "safe_command_provenance", "cwd_provenance", "started_at", "finished_at", "duration", "git_status", "git_diff", "usage", "model", "model_version", "cli_version", "visible_final", "unavailable_fields", "fork_turns"}


class RedSanitizationTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((Path(__file__).parents[1] / "evals" / "red-results.json").read_text())
        self.rows = self.data["attempts"]

    def test_exact_campaign_cardinality(self):
        self.assertEqual(len(self.rows), 18)
        for harness in ("codex", "claude", "grok"):
            rows = [r for r in self.rows if r["harness"] == harness]
            self.assertEqual(len(rows), 6)
            self.assertEqual({r["scenario"] for r in rows}, SCENARIOS)

    def test_complete_provenance_and_unavailable_explanations(self):
        for row in self.rows:
            self.assertTrue(PROVENANCE <= row.keys(), row)
            self.assertTrue(row["attempt_id"].startswith(("attempt-", "codex-")))
            self.assertEqual(row["sanitized_workspace"], f"evals/workspace/{row['attempt_id']}")
            self.assertIsInstance(row["unavailable_fields"], dict)
            for field in PROVENANCE:
                if row[field] is None:
                    self.assertIn(field, row["unavailable_fields"], (row["attempt_id"], field))
            if row["harness"] == "codex":
                self.assertEqual(row["invocation_method"], "collaboration.spawn_agent")
                self.assertEqual(row["fork_turns"], "none")
                self.assertIsNone(row["model"])
                self.assertIn("model", row["unavailable_fields"])
            else:
                self.assertIsNone(row["model_version"])
                self.assertIn("model_version", row["unavailable_fields"])
            self.assertIsInstance(row["safe_command_provenance"], dict)
            self.assertIn("executable", row["safe_command_provenance"])
            self.assertTrue(any("PROMPT_SHA256" in a for a in row["safe_command_provenance"]["args"]))

    def test_outcome_counts_and_invalidations(self):
        observed = {}
        for row in self.rows:
            observed.setdefault(row["harness"], {})[row["outcome_type"]] = observed.setdefault(row["harness"], {}).get(row["outcome_type"], 0) + 1
        self.assertEqual(observed, self.data["outcome_counts"])
        self.assertEqual(len(self.data["invalidated_attempts"]), 26)
        self.assertEqual({r["attempt_id"] for r in self.data["invalidated_attempts"]}, ORIGINAL_IDS)
        for row in self.data["invalidated_attempts"]:
            self.assertTrue(row["attempt_id"].startswith(("attempt-", "codex-")))
            self.assertTrue(row["reason"])
            self.assertIsNone(row["payload"])
        probes=[r for r in self.data["invalidated_attempts"] if r["attempt_id"] in {"attempt-5374a7d1985349d79b7999d110eeb603","attempt-6eb0a75796e5499a8655ee7eb79afe2c"}]
        self.assertEqual({r["harness"] for r in probes},{"runner"}); self.assertEqual({r["scenario"] for r in probes},{"probe"})

    def test_recursive_sanitization(self):
        text = json.dumps(self.data)
        for pattern in (r"\x1b", r"[A-Za-z]:[\\/]", r"/Users/", r"/home/", r"\\Users\\", r"thought", r"expected.?answer", r"evals\.json", r"docs/plans"):
            self.assertIsNone(re.search(pattern, text, re.I), pattern)


if __name__ == "__main__":
    unittest.main()
