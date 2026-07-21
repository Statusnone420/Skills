from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools import codex_campaign


CAMPAIGN = {
    "schema_version": 1,
    "campaign_id": "candidate-provenance-test",
    "target": {"repository": "Example", "commit": "1111111111111111111111111111111111111111"},
    "execution": {
        "model": "gpt-5.6-luna",
        "reasoning_effort": "max",
        "repetitions_per_condition": 1,
        "fresh_task_per_run": True,
        "read_only": True,
    },
    "conditions": [{"id": "docs-map-candidate", "prompt": "map"}],
    "decision_rule": {"primary_metrics": ["duration_seconds"]},
}


class ProvenanceFixture:
    def __init__(self, base: Path) -> None:
        self.base = base
        self.repo = base / "repo"
        self.plugin = self.repo / "plugins" / "diataxis-docs"
        for relative, content in {
            "skills/docs-map/SKILL.md": "---\nname: docs-map\n---\n\n# Docs Map\n\ncandidate map contract\n",
            "skills/docs/SKILL.md": "---\nname: docs\n---\n\n# Diátaxis Docs\n\nshared engine\n",
            "skills/docs/scripts/check.py": "print('checker')\n",
        }.items():
            path = self.plugin / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
        (self.repo / "skills").mkdir(exist_ok=True)
        marketplace = self.repo / ".agents" / "plugins" / "marketplace.json"
        marketplace.parent.mkdir(parents=True, exist_ok=True)
        marketplace.write_text(json.dumps({
            "name": "statusnone-skills",
            "plugins": [{
                "name": "diataxis-docs",
                "source": {"source": "local", "path": "./plugins/diataxis-docs"},
            }],
        }), encoding="utf-8")
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "add", "--all"], check=True)
        subprocess.run(
            ["git", "-C", str(self.repo), "-c", "user.email=t@example.com",
             "-c", "user.name=t", "commit", "-qm", "fixture"],
            check=True,
        )
        self.commit = subprocess.run(
            ["git", "-C", str(self.repo), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        self.cache_root = base / "cache" / "statusnone-skills" / "diataxis-docs"
        shutil.copytree(self.plugin, self.cache_root / "0.1.6")
        self.campaign_path = base / "campaign.json"
        self.campaign_path.write_text(json.dumps(CAMPAIGN), encoding="utf-8")
        self.receipt_path = base / "receipt.json"

    def build_receipt(self) -> dict:
        return codex_campaign.build_provenance(
            self.campaign_path,
            self.receipt_path,
            expected_commit=self.commit,
            conditions=["docs-map-candidate"],
            repo_root=self.repo,
            cache_root=self.cache_root,
        )

    def skill_body(self) -> str:
        return (self.plugin / "skills" / "docs-map" / "SKILL.md").read_text(encoding="utf-8")

    def write_session(
        self,
        thread_id: str,
        *,
        body: str | None = None,
        injected: bool = True,
        path_prefix: str = "statusnone-skills/diataxis-docs/0.1.6/",
        started: datetime | None = None,
        tool_events: list[dict] | None = None,
        first_cached_input_tokens: int = 40,
    ) -> Path:
        started = started or (datetime.now(timezone.utc) + timedelta(seconds=5))
        finished = started + timedelta(seconds=90)
        body = self.skill_body() if body is None else body
        injection = (
            "<skill>\n<name>diataxis-docs:docs-map</name>\n"
            f"<path>cache\\{path_prefix.replace('/', chr(92))}skills\\docs-map\\SKILL.md</path>\n"
            f"{body}\n</skill>"
        )
        events = [
            {"type": "session_meta", "timestamp": started.isoformat(), "payload": {
                "id": thread_id, "cwd": str(self.repo),
                "git": {"commit_hash": CAMPAIGN["target"]["commit"]},
            }},
            {"type": "turn_context", "timestamp": started.isoformat(), "payload": {
                "model": "gpt-5.6-luna", "effort": "max",
            }},
        ]
        if injected:
            events.append({"type": "response_item", "timestamp": started.isoformat(), "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": injection}],
            }})
        events.extend(tool_events or [])
        events.extend([
            {"type": "response_item", "timestamp": finished.isoformat(), "payload": {
                "type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": "Documentation map"}],
            }},
            {"type": "event_msg", "timestamp": finished.isoformat(), "payload": {
                "type": "token_count", "info": {"total_token_usage": {
                    "input_tokens": 100, "cached_input_tokens": 40,
                    "output_tokens": 20, "reasoning_output_tokens": 5,
                    "total_tokens": 120,
                }, "last_token_usage": {
                    "input_tokens": 100, "cached_input_tokens": first_cached_input_tokens,
                    "output_tokens": 20, "reasoning_output_tokens": 5,
                    "total_tokens": 120,
                }},
            }},
        ])
        sessions = self.base / "sessions"
        sessions.mkdir(exist_ok=True)
        path = sessions / f"rollout-{thread_id}.jsonl"
        path.write_text(
            "\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8"
        )
        return path


class CodexCampaignTests(unittest.TestCase):
    def test_collect_reports_shell_commands_and_memory_reads_per_wrapper(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = ProvenanceFixture(Path(directory))
            memory_segment = "memory-registry-evidence"
            tool_events = [
                {"type": "response_item", "timestamp": datetime.now(timezone.utc).isoformat(),
                 "payload": {"type": "custom_tool_call", "name": "exec", "call_id": "c1",
                             "input": """const [a, b, memory] = await Promise.all([
tools.shell_command({command: \"Get-Content docs/README.md\"}),
tools.shell_command({command: \"Get-Content docs/STATE.md\"}),
tools.shell_command({command: \"Get-Content C:\\\\Users\\\\A\\\\.codex\\\\memories\\\\MEMORY.md\"})]);
text(JSON.stringify({a, b, memory}));"""}},
                {"type": "response_item", "timestamp": datetime.now(timezone.utc).isoformat(),
                 "payload": {"type": "custom_tool_call_output", "call_id": "c1", "output": [
                     {"type": "input_text", "text": "Script completed\nOutput:\n"},
                     {"type": "input_text", "text": json.dumps({
                         "a": "readme", "b": "state", "memory": memory_segment,
                     })},
                 ]}},
            ]
            session = fixture.write_session("thread-tools", tool_events=tool_events)
            metrics = codex_campaign.collect_session(session, CAMPAIGN)
            self.assertEqual(metrics["tool_call_wrappers"], 1)
            self.assertEqual(metrics["shell_commands"], 3)
            self.assertEqual(metrics["memory_read_ops"], 1)
            self.assertEqual(metrics["memory_read_output_chars"], len(memory_segment))

    def test_collect_reports_first_turn_cached_prefix(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = ProvenanceFixture(Path(directory))
            session = fixture.write_session(
                "thread-cache-prefix", first_cached_input_tokens=9984
            )
            metrics = codex_campaign.collect_session(session, CAMPAIGN)
            self.assertEqual(metrics["first_turn_cached_input_tokens"], 9984)

    def test_summarize_flags_asymmetric_memory_exposure_and_cache_states(self):
        runs = []
        for condition, cached, memory in (
            ("july11-bounded-recipe", (9984, 21248, 21248), (0, 0, 1)),
            ("docs-map-0.1.6", (9984, 9984, 21248), (1, 1, 1)),
        ):
            for repetition in range(3):
                runs.append({
                    "condition": condition, "repetition": repetition + 1,
                    "pair": repetition + 1,
                    "duration_seconds": 10, "tool_call_wrappers": 4,
                    "uncached_input_tokens": 20, "reasoning_tokens": 2,
                    "nonreasoning_output_tokens": 3, "total_tokens": 25,
                    "memory_read_ops": memory[repetition],
                    "first_turn_cached_input_tokens": cached[repetition],
                })
        summary = codex_campaign.summarize({"runs": runs})
        self.assertEqual(summary["medians"]["july11-bounded-recipe"]["duration_seconds"], 10)
        self.assertEqual(summary["docs_map_0_1_6_vs_july11_percent"]["uncached_input_tokens"], 0.0)
        self.assertEqual(summary["comparability"]["july11-bounded-recipe"], {
            "runs_with_memory_reads": 1,
            "first_turn_cached_input_tokens": {"9984": 1, "21248": 2},
        })
        self.assertEqual(summary["comparability"]["docs-map-0.1.6"], {
            "runs_with_memory_reads": 3,
            "first_turn_cached_input_tokens": {"9984": 2, "21248": 1},
        })
        self.assertEqual(summary["paired_uncached_input_differences"], [])

    def test_paired_campaign_requires_memory_isolation_and_recorded_pair_order(self):
        root = Path(__file__).resolve().parents[1]
        paired_path = root / "evals" / "retrieval" / "luna-max-cli-paired-v1.json"
        paired = json.loads(paired_path.read_text(encoding="utf-8"))
        frozen = json.loads(
            (root / "evals" / "retrieval" / "luna-max-july11-constant.json")
            .read_text(encoding="utf-8")
        )
        frozen_prompts = {item["id"]: item["prompt"] for item in frozen["conditions"]}
        paired_prompts = {item["id"]: item["prompt"] for item in paired["conditions"]}
        self.assertEqual(paired["host_context"]["memory"], "unavailable")
        self.assertEqual(paired["paired_execution"]["pairs"], 3)
        self.assertTrue(paired["paired_execution"]["record_order_before_launch"])
        self.assertEqual(paired_prompts, {
            "july11-bounded-recipe": frozen_prompts["july11-bounded-recipe"],
            "docs-map-candidate": frozen_prompts["docs-map-0.1.6"],
        })
        self.assertEqual(
            next(item for item in paired["conditions"] if item["id"] == "docs-map-candidate")["skill"],
            "diataxis-docs:docs-map",
        )

        with tempfile.TemporaryDirectory() as directory:
            fixture = ProvenanceFixture(Path(directory))
            campaign_path = fixture.base / "paired.json"
            campaign = dict(paired)
            campaign["target"] = dict(CAMPAIGN["target"])
            campaign["execution"] = dict(CAMPAIGN["execution"], repetitions_per_condition=1)
            campaign["paired_execution"] = dict(paired["paired_execution"], pairs=1)
            campaign_path.write_text(json.dumps(campaign), encoding="utf-8")
            memory_call = [{
                "type": "response_item", "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": {"type": "custom_tool_call", "name": "exec", "call_id": "m1",
                            "input": "tools.shell_command({command: \"Get-Content C:\\\\Users\\\\A\\\\.codex\\\\memories\\\\MEMORY.md\"});"},
            }]
            fixture.write_session("thread-july", injected=False)
            fixture.write_session("thread-candidate", injected=False, tool_events=memory_call)
            manifest = fixture.base / "manifest.json"
            manifest.write_text(json.dumps({
                "host_context": paired["host_context"],
                "runs": [
                    {"run_id": "j1", "condition": "july11-bounded-recipe", "repetition": 1,
                     "pair": 1, "pair_order": 1, "thread_id": "thread-july"},
                    {"run_id": "c1", "condition": "docs-map-candidate", "repetition": 1,
                     "pair": 1, "pair_order": 2, "thread_id": "thread-candidate"},
                ],
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "memory isolation failed"):
                codex_campaign.collect(
                    campaign_path, manifest, fixture.base / "result.json",
                    fixture.base / "sessions",
                )

    def test_summarize_uses_condition_medians(self):
        runs = []
        for condition, values in {"old": (10, 30, 20), "new": (5, 15, 10)}.items():
            for repetition, value in enumerate(values, 1):
                runs.append({
                    "condition": condition,
                    "repetition": repetition,
                    "duration_seconds": value,
                    "tool_call_wrappers": value,
                    "uncached_input_tokens": value,
                    "reasoning_tokens": value,
                    "nonreasoning_output_tokens": value,
                    "total_tokens": value,
                })
        summary = codex_campaign.summarize({"runs": runs})
        self.assertEqual(summary["medians"]["old"]["duration_seconds"], 20)
        self.assertEqual(summary["medians"]["new"]["uncached_input_tokens"], 10)

    def test_find_session_requires_exactly_one_match(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "found 0"):
                codex_campaign._find_session(root, "missing")
            (root / "rollout-thread-1.jsonl").write_text("{}\n", encoding="utf-8")
            self.assertEqual(codex_campaign._find_session(root, "thread-1").name, "rollout-thread-1.jsonl")
            (root / "copy-thread-1.jsonl").write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "found 2"):
                codex_campaign._find_session(root, "thread-1")

    def test_find_session_includes_archived_sibling(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            sessions = base / "sessions"
            archived = base / "archived_sessions"
            sessions.mkdir()
            archived.mkdir()
            expected = archived / "rollout-thread-2.jsonl"
            expected.write_text("{}\n", encoding="utf-8")
            self.assertEqual(codex_campaign._find_session(sessions, "thread-2"), expected)

    def test_events_rejects_malformed_jsonl(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.jsonl"
            path.write_text('{"ok": true}\nnot-json\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "line 2"):
                codex_campaign._events(path)

    def test_tree_digest_is_deterministic_and_detects_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "tree"
            (root / "b").mkdir(parents=True)
            (root / "a.txt").write_text("alpha", encoding="utf-8")
            (root / "b" / "c.txt").write_text("charlie", encoding="utf-8")
            first = codex_campaign._tree_digest(root)
            second = codex_campaign._tree_digest(root)
            self.assertEqual(first, second)
            self.assertEqual(first["files"], 2)
            (root / "b" / "c.txt").write_text("changed", encoding="utf-8")
            self.assertNotEqual(codex_campaign._tree_digest(root), first)

    def test_provenance_preflight_requires_cache_equal_to_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = ProvenanceFixture(Path(directory))
            receipt = fixture.build_receipt()
            self.assertEqual(receipt["candidate_commit"], fixture.commit)
            self.assertEqual(receipt["snapshot_version"], "0.1.6")
            self.assertEqual(receipt["marketplace_source"], "./plugins/diataxis-docs")
            self.assertEqual(
                receipt["cache_relative_prefix"],
                "statusnone-skills/diataxis-docs/0.1.6/",
            )
            self.assertNotIn(":\\", json.dumps(receipt))
            self.assertNotIn("/Users/", json.dumps(receipt))
            written = json.loads(fixture.receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(written, receipt)

            cached_map = (
                fixture.cache_root / "0.1.6" / "skills" / "docs-map" / "SKILL.md"
            )
            cached_map.write_text("stale 0.1.6 bytes\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "does not match candidate plugin bytes"):
                fixture.build_receipt()

    def test_provenance_preflight_rejects_ambiguous_or_drifted_states(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = ProvenanceFixture(Path(directory))
            with self.assertRaisesRegex(ValueError, "does not match expected candidate commit"):
                codex_campaign.build_provenance(
                    fixture.campaign_path, fixture.receipt_path,
                    expected_commit="f" * 40, conditions=["docs-map-candidate"],
                    repo_root=fixture.repo, cache_root=fixture.cache_root,
                )
            (fixture.cache_root / "0.1.7").mkdir()
            with self.assertRaisesRegex(ValueError, "exactly one cached plugin snapshot"):
                fixture.build_receipt()
            (fixture.cache_root / "0.1.7").rmdir()
            (fixture.plugin / "skills" / "docs-map" / "SKILL.md").write_text(
                "uncommitted", encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "dirty"):
                fixture.build_receipt()

    def test_collect_binds_candidate_sessions_to_injected_skill_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = ProvenanceFixture(Path(directory))
            receipt = fixture.build_receipt()
            fixture.write_session("thread-good")
            manifest = fixture.base / "manifest.json"
            manifest.write_text(json.dumps({
                "validity": "candidate-test",
                "runs": [{
                    "run_id": "docs-map-candidate-1", "condition": "docs-map-candidate",
                    "repetition": 1, "thread_id": "thread-good",
                }],
            }), encoding="utf-8")
            output = fixture.base / "result.json"
            result = codex_campaign.collect(
                fixture.campaign_path, manifest, output, fixture.base / "sessions",
                provenance_path=fixture.receipt_path,
                repo_root=fixture.repo, cache_root=fixture.cache_root,
            )
            run = result["runs"][0]
            self.assertEqual(
                run["injected_skill_sha256"],
                receipt["key_files"]["skills/docs-map/SKILL.md"],
            )
            self.assertEqual(
                run["injected_skill_source"],
                "statusnone-skills/diataxis-docs/0.1.6/skills/docs-map/SKILL.md",
            )
            self.assertEqual(result["candidate_provenance"]["drift"], "none")
            self.assertNotIn(":\\", json.dumps(result["candidate_provenance"]))

            unbound = codex_campaign.collect(
                fixture.campaign_path, manifest, output, fixture.base / "sessions",
            )
            self.assertNotIn("injected_skill_sha256", unbound["runs"][0])
            self.assertNotIn("candidate_provenance", unbound)

    def test_collect_rejects_sessions_that_did_not_load_candidate_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = ProvenanceFixture(Path(directory))
            fixture.build_receipt()
            manifest = fixture.base / "manifest.json"
            output = fixture.base / "result.json"

            def run_with(thread_id):
                manifest.write_text(json.dumps({"runs": [{
                    "run_id": "r1", "condition": "docs-map-candidate",
                    "repetition": 1, "thread_id": thread_id,
                }]}), encoding="utf-8")
                return codex_campaign.collect(
                    fixture.campaign_path, manifest, output, fixture.base / "sessions",
                    provenance_path=fixture.receipt_path,
                    repo_root=fixture.repo, cache_root=fixture.cache_root,
                )

            fixture.write_session(
                "thread-stale-bytes",
                body="---\nname: docs-map\n---\n\nold released 0.1.6 body\n",
            )
            with self.assertRaisesRegex(ValueError, "do not match the candidate"):
                run_with("thread-stale-bytes")

            fixture.write_session("thread-no-injection", injected=False)
            with self.assertRaisesRegex(ValueError, "no injected skill message"):
                run_with("thread-no-injection")

            fixture.write_session(
                "thread-foreign-cache",
                path_prefix="statusnone-skills/diataxis-docs/0.1.5/",
            )
            with self.assertRaisesRegex(ValueError, "pinned cache snapshot"):
                run_with("thread-foreign-cache")

            fixture.write_session(
                "thread-predates",
                started=datetime.now(timezone.utc) - timedelta(hours=1),
            )
            with self.assertRaisesRegex(ValueError, "predates the provenance receipt"):
                run_with("thread-predates")

    def test_collect_rejects_post_receipt_candidate_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = ProvenanceFixture(Path(directory))
            fixture.build_receipt()
            fixture.write_session("thread-good")
            manifest = fixture.base / "manifest.json"
            manifest.write_text(json.dumps({"runs": [{
                "run_id": "r1", "condition": "docs-map-candidate",
                "repetition": 1, "thread_id": "thread-good",
            }]}), encoding="utf-8")
            cached_map = fixture.cache_root / "0.1.6" / "skills" / "docs-map" / "SKILL.md"
            cached_map.write_text("swapped after launch\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "snapshot drifted"):
                codex_campaign.collect(
                    fixture.campaign_path, manifest, fixture.base / "result.json",
                    fixture.base / "sessions",
                    provenance_path=fixture.receipt_path,
                    repo_root=fixture.repo, cache_root=fixture.cache_root,
                )

    def test_collect_binds_cli_candidate_to_qualified_skill_and_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = ProvenanceFixture(Path(directory))
            root = Path(__file__).resolve().parents[1]
            campaign = json.loads(
                (root / "evals" / "retrieval" / "luna-max-cli-paired-v1.json")
                .read_text(encoding="utf-8")
            )
            campaign["target"] = dict(CAMPAIGN["target"])
            campaign["execution"] = dict(CAMPAIGN["execution"])
            campaign_path = fixture.base / "cli-campaign.json"
            campaign_path.write_text(json.dumps(campaign), encoding="utf-8")
            receipt = codex_campaign.build_provenance(
                campaign_path, fixture.receipt_path,
                expected_commit=fixture.commit, conditions=["docs-map-candidate"],
                repo_root=fixture.repo, cache_root=fixture.cache_root,
            )
            candidate = next(
                item for item in campaign["conditions"] if item["id"] == "docs-map-candidate"
            )
            request = f"${candidate['skill']}\n{candidate['prompt']}"
            user_event = {
                "type": "response_item", "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": {"type": "message", "role": "user",
                            "content": [{"type": "input_text", "text": request}]},
            }
            session = fixture.write_session(
                "thread-cli-qualified", injected=False, tool_events=[user_event]
            )
            metrics = codex_campaign.collect_session(session, campaign, receipt)
            self.assertEqual(
                metrics["requested_skill_sha256"],
                receipt["key_files"]["skills/docs-map/SKILL.md"],
            )
            self.assertEqual(
                metrics["skill_binding_evidence"],
                "qualified-cli-request-plus-verified-cache",
            )

    def test_campaign_constant_is_well_formed(self):
        root = Path(__file__).resolve().parents[1]
        campaign = json.loads(
            (root / "evals" / "retrieval" / "luna-max-july11-constant.json").read_text(encoding="utf-8")
        )
        self.assertEqual(codex_campaign._condition_ids(campaign), {
            "no-skill", "july11-bounded-recipe", "docs-map-0.1.6"
        })
        self.assertEqual(campaign["execution"]["repetitions_per_condition"], 3)
        self.assertEqual(campaign["target"]["commit"], "7609b76da4b2ea6845c5b9f38dabfbd17487f673")

    def test_0_1_7_correctness_campaign_is_capped_and_memory_isolated(self):
        root = Path(__file__).resolve().parents[1]
        campaign = json.loads(
            (root / "evals" / "retrieval" / "luna-low-0.1.7-correctness-v1.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(codex_campaign._condition_ids(campaign), {
            "docs-map-0.1.7-correctness"
        })
        self.assertEqual(campaign["execution"]["model"], "gpt-5.6-luna")
        self.assertEqual(campaign["execution"]["reasoning_effort"], "low")
        self.assertEqual(campaign["execution"]["repetitions_per_condition"], 3)
        self.assertEqual(campaign["execution"]["recommended_max_concurrency"], 1)
        self.assertEqual(campaign["host_context"], {
            "host": "Codex CLI", "memory": "unavailable"
        })
        self.assertEqual(campaign["conditions"][0]["skill"], "diataxis-docs:docs-map")
        self.assertEqual(campaign["decision_rule"]["maximum_repository_checker_attempts"], 0)
        self.assertEqual(campaign["decision_rule"]["maximum_memory_read_ops"], 0)


if __name__ == "__main__":
    unittest.main()
