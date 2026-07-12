import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import trajectory_gate


class TrajectoryGateTests(unittest.TestCase):
    def load(self, name):
        return json.loads((ROOT / "evals" / "trajectory" / name).read_text(encoding="utf-8"))

    def test_accepted_map_receipt_passes_with_host_overhead_separated(self):
        result = trajectory_gate.evaluate(self.load("bulwark-map-accepted.json"))

        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["metrics"]["docs_actions"], 4)
        self.assertEqual(result["metrics"]["external_actions"], 2)
        self.assertEqual(result["metrics"]["checker_runs"], 1)
        self.assertIn("usage.unpaired_host_baseline", result["warnings"])

    def test_regression_receipt_reports_behavior_cost_and_hci_failures(self):
        result = trajectory_gate.evaluate(self.load("bulwark-map-regression.json"))

        self.assertEqual(result["status"], "FAIL")
        self.assertIn("presentation.raw_exit_code", result["errors"])
        self.assertIn("retrieval.docs_action_budget", result["errors"])
        self.assertIn("retrieval.repeated_checker", result["errors"])
        self.assertIn("external.failed_lookup", result["warnings"])
        self.assertNotIn("external.action_budget", result["errors"])

    def test_missing_reader_questions_fail_without_exact_output_snapshot(self):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["outcome"]["answers"].remove("deliberately_unloaded")

        result = trajectory_gate.evaluate(receipt)

        self.assertIn("outcome.missing_answer:deliberately_unloaded", result["errors"])

    def test_map_requires_human_scannable_tree_features(self):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["presentation"]["tree_features"].remove("cold_collapsed")

        result = trajectory_gate.evaluate(receipt)

        self.assertIn("presentation.missing_tree_feature:cold_collapsed", result["errors"])

    def test_non_map_commands_do_not_require_documentation_tree(self):
        for command in ("context", "doctor"):
            with self.subTest(command=command):
                receipt = self.load("bulwark-map-accepted.json")
                receipt["command"] = command
                receipt["retrieval"]["actions"] = receipt["retrieval"]["actions"][: trajectory_gate.MAX_DOCS_ACTIONS[command]]
                receipt["presentation"].pop("tree")
                receipt["presentation"].pop("tree_features")

                result = trajectory_gate.evaluate(receipt)

                self.assertEqual(result["status"], "PASS")
                self.assertNotIn("presentation.missing_tree", result["errors"])

    def test_check_receipts_require_one_checker_run(self):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["command"] = "check"
        actions = receipt["retrieval"]["actions"]
        receipt["retrieval"]["actions"] = [actions[0], actions[1], actions[3]]
        receipt["presentation"].pop("tree")
        receipt["presentation"].pop("tree_features")

        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["metrics"]["checker_runs"], 1)

        receipt["retrieval"]["actions"] = [actions[0], actions[1], actions[2]]
        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "FAIL")
        self.assertIn("retrieval.missing_checker", result["errors"])

    def test_map_and_check_require_successful_checker_status(self):
        for command in ("map", "check"):
            with self.subTest(command=command):
                receipt = self.load("bulwark-map-accepted.json")
                receipt["command"] = command
                receipt["retrieval"]["actions"][3]["status"] = "error"
                if command == "check":
                    actions = receipt["retrieval"]["actions"]
                    receipt["retrieval"]["actions"] = [actions[0], actions[1], actions[3]]
                    receipt["presentation"].pop("tree")
                    receipt["presentation"].pop("tree_features")

                result = trajectory_gate.evaluate(receipt)

                self.assertEqual(result["status"], "FAIL")
                self.assertIn("retrieval.checker_failed", result["errors"])

    def test_check_receipts_do_not_require_map_reader_answers(self):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["command"] = "check"
        receipt["outcome"].pop("answers")
        actions = receipt["retrieval"]["actions"]
        receipt["retrieval"]["actions"] = [actions[0], actions[1], actions[3]]
        receipt["presentation"].pop("tree")
        receipt["presentation"].pop("tree_features")

        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "PASS")
        self.assertFalse(any(error.startswith("outcome.missing_answer:") for error in result["errors"]))

    def test_map_receipts_require_one_checker_run(self):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["retrieval"]["actions"] = receipt["retrieval"]["actions"][:3]

        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "FAIL")
        self.assertIn("retrieval.missing_checker", result["errors"])

    def test_mapped_map_receipts_use_three_action_budget(self):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["retrieval"]["actions"][0]["status"] = "complete"

        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "FAIL")
        self.assertIn("retrieval.docs_action_budget", result["errors"])

    def test_map_rejects_broad_retrieval_actions_within_budget(self):
        for kind in ("repo-wide-search", "inventory", "name-only-inventory"):
            with self.subTest(kind=kind):
                receipt = self.load("bulwark-map-accepted.json")
                actions = receipt["retrieval"]["actions"]
                receipt["retrieval"]["actions"] = [actions[0], actions[1], actions[3]]
                receipt["retrieval"]["actions"][0]["status"] = "complete"
                receipt["retrieval"]["actions"][1]["kind"] = kind

                result = trajectory_gate.evaluate(receipt)

                self.assertEqual(result["status"], "FAIL")
                self.assertIn("retrieval.broad_action", result["errors"])

    def test_host_growth_is_only_attributed_with_a_paired_control(self):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["usage"]["paired_control"] = {
            "responses": 1,
            "cumulative_input_tokens": 30_000,
            "cached_input_tokens": 27_000,
        }
        receipt["usage"]["cumulative_input_tokens"] = 200_000
        receipt["usage"]["responses"] = 4

        result = trajectory_gate.evaluate(receipt)

        self.assertNotIn("usage.unpaired_host_baseline", result["warnings"])
        self.assertEqual(result["metrics"]["input_per_response"], 50_000)
        self.assertEqual(result["metrics"]["paired_host_input_per_response"], 30_000)
        self.assertEqual(result["metrics"]["input_per_response_delta"], 20_000)

    def test_public_receipts_reject_sensitive_or_hidden_material_recursively(self):
        bad_values = [
            ("absolute path", {"note": r"C:\Users\person\repo"}),
            ("UNC path", {"note": r"\\server\share\repo"}),
            ("POSIX workspace path", {"note": "/workspace/Skills"}),
            ("POSIX temporary path", {"note": "/tmp/private"}),
            ("POSIX var path", {"note": "/var/lib/private"}),
            ("secret", {"metadata": {"api_token": "opaque"}}),
            ("hidden reasoning", {"hidden_reasoning": "private"}),
            ("raw session id", {"session_id": "synthetic-private-id"}),
        ]
        for label, addition in bad_values:
            with self.subTest(label=label):
                receipt = self.load("bulwark-map-accepted.json")
                receipt.update(addition)
                with self.assertRaisesRegex(ValueError, "public trajectory receipt"):
                    trajectory_gate.evaluate(receipt)

    def test_public_receipts_reject_absolute_path_keys(self):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["diagnostics"] = {"/workspace/Skills/docs/README.md": "unresolved"}

        with self.assertRaisesRegex(ValueError, "public trajectory receipt"):
            trajectory_gate.evaluate(receipt)

    def test_public_receipts_allow_urls_and_prose_slashes(self):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["presentation"]["visible_diagnostics"] = [
            "See https://docs.example.test/map",
            "links / anchors checked",
        ]

        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "PASS")

    def test_receipt_rejects_malformed_retrieval_actions(self):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["retrieval"]["actions"].append("not-an-action")

        with self.assertRaisesRegex(ValueError, "retrieval.actions entries"):
            trajectory_gate.evaluate(receipt)

    def test_malformed_receipt_arrays_raise_value_error(self):
        mutations = (
            ("outcome.answers", lambda receipt: receipt["outcome"].update(answers=None)),
            ("presentation.tree_features", lambda receipt: receipt["presentation"].update(tree_features=None)),
            ("presentation.visible_diagnostics", lambda receipt: receipt["presentation"].update(visible_diagnostics=None)),
        )
        for name, mutate in mutations:
            with self.subTest(name=name):
                receipt = self.load("bulwark-map-accepted.json")
                mutate(receipt)

                with self.assertRaisesRegex(ValueError, "must be an array"):
                    trajectory_gate.evaluate(receipt)

    def test_cli_emits_json_and_uses_exit_codes_zero_one_two(self):
        cases = [
            ("bulwark-map-accepted.json", 0, "PASS"),
            ("bulwark-map-regression.json", 1, "FAIL"),
        ]
        for filename, code, status in cases:
            with self.subTest(filename=filename):
                result = subprocess.run(
                    [sys.executable, str(ROOT / "tools" / "trajectory_gate.py"), str(ROOT / "evals" / "trajectory" / filename)],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertEqual(json.loads(result.stdout)["status"], status)

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as handle:
            handle.write("not json")
            malformed = Path(handle.name)
        try:
            result = subprocess.run(
                [sys.executable, str(ROOT / "tools" / "trajectory_gate.py"), str(malformed)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 2)
        finally:
            malformed.unlink(missing_ok=True)

    def test_cli_returns_invalid_for_malformed_receipt_arrays(self):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["outcome"]["answers"] = None
        with tempfile.TemporaryDirectory() as td:
            malformed = Path(td) / "malformed-array.json"
            malformed.write_text(json.dumps(receipt), encoding="utf-8")

            result = subprocess.run(
                [sys.executable, str(ROOT / "tools" / "trajectory_gate.py"), str(malformed)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["status"], "INVALID")
        self.assertEqual(result.stderr, "")

    def test_release_campaign_is_capped_and_requires_explicit_approval(self):
        campaign = self.load("release-canary-example.json")
        campaign["approved"] = True
        trajectory_gate.validate_campaign(campaign)

        campaign["max_runs"] = 13
        with self.assertRaisesRegex(ValueError, "maximum of 12"):
            trajectory_gate.validate_campaign(campaign)

        campaign["max_runs"] = 4
        campaign["approved"] = False
        with self.assertRaisesRegex(ValueError, "explicit approval"):
            trajectory_gate.validate_campaign(campaign)

    def test_skill_translates_checker_findings_for_humans(self):
        skill = (ROOT / "skills" / "docs" / "SKILL.md").read_text(encoding="utf-8")
        commands = (ROOT / "skills" / "docs" / "references" / "commands.md").read_text(encoding="utf-8")

        self.assertIn("plain-English finding count", skill)
        self.assertIn("raw exit code only when execution itself fails", skill)
        self.assertIn("has_findings: true", commands)

    def test_public_evaluation_docs_define_layered_gates_and_local_command(self):
        evaluation = (ROOT / "EVALUATION.md").read_text(encoding="utf-8")
        benchmark = (ROOT / "BENCHMARK.md").read_text(encoding="utf-8")

        for phrase in (
            "Deterministic contract gate",
            "Sanitized trajectory gate",
            "Capped live canary",
            "host/external overhead",
            "python tools/trajectory_gate.py",
        ):
            self.assertIn(phrase, evaluation)
        self.assertIn("407,376", benchmark)
        self.assertIn("not attributable to Diátaxis Docs alone", benchmark)

    def test_agent_checker_mode_returns_success_with_structured_findings(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            docs = root / "docs"
            docs.mkdir()
            (docs / "README.md").write_text("# Map\n", encoding="utf-8")
            (docs / "orphan.md").write_text("# Orphan\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "skills" / "docs" / "scripts" / "check.py"),
                    str(root),
                    "--json",
                    "--agent",
                ],
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "findings")
            self.assertTrue(payload["has_findings"])
            self.assertEqual(len(payload["findings"]), 1)

    def test_human_checker_mode_retains_findings_exit_code(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            docs = root / "docs"
            docs.mkdir()
            (docs / "README.md").write_text("# Map\n", encoding="utf-8")
            (docs / "orphan.md").write_text("# Orphan\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "skills" / "docs" / "scripts" / "check.py"),
                    str(root),
                    "--json",
                ],
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "findings")
            self.assertTrue(payload["has_findings"])

    def test_agent_checker_mode_requires_json(self):
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "skills" / "docs" / "scripts" / "check.py"),
                str(ROOT),
                "--agent",
            ],
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("--agent requires --json", result.stdout)

    def test_agent_checker_mode_preserves_real_execution_errors(self):
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "skills" / "docs" / "scripts" / "check.py"),
                "missing-repository",
                "--json",
                "--agent",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["status"], "error")

    def test_agent_playbooks_use_non_failure_findings_mode(self):
        commands = (ROOT / "skills" / "docs" / "references" / "commands.md").read_text(encoding="utf-8")
        doctor = (ROOT / "skills" / "docs" / "references" / "doctor.md").read_text(encoding="utf-8")

        self.assertGreaterEqual(commands.count("--json --agent"), 2)
        self.assertIn("--json --agent", doctor)


if __name__ == "__main__":
    unittest.main()
