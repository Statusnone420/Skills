import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).parents[1]
CHECKER = ROOT / "skills" / "docs" / "scripts" / "check.py"

from tests.init_journey_fixture import (
    build_large_init_fixture,
    build_small_init_fixture,
    snapshot_repository,
)


def run_init_cli(repo: Path, *, scope: str | None = None, token: str | None = None) -> dict:
    completed = run_init_cli_process(repo, scope=scope, token=token)
    if completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout)
    return json.loads(completed.stdout)


def run_init_cli_process(
    repo: Path,
    *,
    scope: str | None = None,
    token: str | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        str(CHECKER),
        str(repo),
        "--json",
        "--agent",
        "--init-discovery",
    ]
    if scope is not None:
        command.extend(["--scope", scope])
    if token is not None:
        command.extend(["--continuation", token])
    completed = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed


class InitJourneyCliTests(unittest.TestCase):
    def test_cli_tied_roots_reports_one_recommended_choice_without_content(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = build_small_init_fixture(
                Path(td),
                shared_roots=("docs", "documentation"),
            )
            before = snapshot_repository(fixture.root)

            payload = run_init_cli(fixture.root)

            self.assertEqual(payload["status"], "choice-required")
            self.assertEqual(payload["selection_reason"], "choice-required")
            self.assertEqual(payload["recommended_scope"], "docs")
            self.assertEqual(
                [item["path"] for item in payload["candidates"]],
                ["docs", "documentation"],
            )
            self.assertIsNone(payload["selected_scope"])
            self.assertEqual(payload["content_batch"]["paths"], [])
            self.assertEqual(payload["continuation"]["status"], "blocked")
            self.assertTrue(payload["requires_user_action"])
            self.assertEqual(payload["user_action"], "choose-explicit-scope")
            self.assertNotIn(str(fixture.root), json.dumps(payload, sort_keys=True))
            self.assertEqual(snapshot_repository(fixture.root), before)

    def test_cli_normalized_dot_scope_uses_automatic_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = build_small_init_fixture(
                Path(td),
                shared_roots=("docs", "documentation"),
            )
            before = snapshot_repository(fixture.root)

            payload = run_init_cli(fixture.root, scope=".")

            self.assertEqual(payload["status"], "choice-required")
            self.assertEqual(payload["selection_reason"], "choice-required")
            self.assertEqual(
                [item["path"] for item in payload["candidates"]],
                ["docs", "documentation"],
            )
            self.assertIsNone(payload["selected_scope"])
            self.assertEqual(payload["content_batch"]["paths"], [])
            self.assertEqual(snapshot_repository(fixture.root), before)

    def test_cli_private_only_repository_stays_zero_write_and_supplementary(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = build_small_init_fixture(
                Path(td),
                shared_roots=(),
                private_routes=(".local/alpha-campaign", ".local/beta-decisions"),
            )
            before = snapshot_repository(fixture.root)

            payload = run_init_cli(fixture.root)
            serialized = json.dumps(payload, sort_keys=True)

            self.assertEqual(payload["status"], "adoption-preview")
            self.assertEqual(payload["selection_reason"], "no-maintained-documentation")
            self.assertEqual(payload["selected_scope"], ".")
            self.assertEqual(payload["candidates"], [])
            self.assertEqual(
                [item["path"] for item in payload["local_knowledge"]["candidates"]],
                list(fixture.private_routes),
            )
            self.assertFalse(payload["local_knowledge"]["absence_claim_allowed"])
            self.assertEqual(payload["content_batch"]["paths"], [])
            self.assertEqual(payload["adoption_preview"]["writes"], 0)
            self.assertNotIn("PRIVATE_SENTINEL_", serialized)
            self.assertEqual(snapshot_repository(fixture.root), before)

    def test_cli_oversized_single_document_pauses_without_dead_cursor(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = build_small_init_fixture(
                Path(td),
                file_bytes=300 * 1024,
            )
            before = snapshot_repository(fixture.root)

            payload = run_init_cli(fixture.root)

            self.assertEqual(payload["status"], "stopped")
            self.assertEqual(payload["selected_scope"], "docs")
            self.assertEqual(payload["continuation"]["status"], "blocked")
            self.assertIsNone(payload["continuation"]["cursor"])
            self.assertIsNone(payload["continuation"]["token"])
            self.assertEqual(payload["content_batch"]["paths"], [])
            self.assertTrue(payload["content_batch"]["blocked_by_metadata"])
            self.assertFalse(payload["content_batch"]["truncated"])
            self.assertEqual(payload["next_boundary"], [])
            self.assertEqual(snapshot_repository(fixture.root), before)

    def test_cli_restarts_after_contents_change_between_batches(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = build_small_init_fixture(Path(td), files_per_root=13)
            first = run_init_cli(fixture.root)
            old_token = first["continuation"]["token"]
            (fixture.root / "docs" / "guide-00.md").write_text(
                "# changed evidence\n",
                encoding="utf-8",
                newline="\n",
            )
            after_change = snapshot_repository(fixture.root)

            stale = run_init_cli_process(fixture.root, token=old_token)
            stale_payload = json.loads(stale.stdout)

            self.assertEqual(stale.returncode, 0)
            self.assertEqual(stale_payload["status"], "stopped")
            self.assertEqual(stale_payload["continuation"]["status"], "rejected")
            self.assertTrue(stale_payload["continuation"]["fresh_preview_required"])
            self.assertEqual(stale_payload["user_action"], "restart-fresh-discovery")
            self.assertEqual(stale_payload["content_batch"]["paths"], [])
            self.assertNotIn(str(fixture.root), stale.stdout)
            self.assertEqual(snapshot_repository(fixture.root), after_change)

            fresh = run_init_cli(fixture.root)
            self.assertEqual(fresh["status"], "batch-limited")
            self.assertNotEqual(fresh["continuation"]["token"], old_token)

    def test_cli_restarts_after_same_size_content_change_with_restored_timestamp(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = build_small_init_fixture(Path(td), files_per_root=13)
            first = run_init_cli(fixture.root)
            old_token = first["continuation"]["token"]
            changed = fixture.root / "docs" / "guide-00.md"
            original_stat = changed.stat()
            changed.write_bytes(b"X" * original_stat.st_size)
            os.utime(
                changed,
                ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
            )

            stale = run_init_cli_process(fixture.root, token=old_token)
            stale_payload = json.loads(stale.stdout)

            self.assertEqual(stale.returncode, 0)
            self.assertEqual(stale_payload["status"], "stopped")
            self.assertEqual(stale_payload["continuation"]["status"], "rejected")
            self.assertTrue(stale_payload["continuation"]["fresh_preview_required"])
            self.assertEqual(stale_payload["content_batch"]["paths"], [])

    def test_cli_restarts_after_next_evidence_disappears(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = build_small_init_fixture(Path(td), files_per_root=13)
            first = run_init_cli(fixture.root)
            old_token = first["continuation"]["token"]
            vanished = fixture.root / "docs" / "guide-12.md"
            vanished.unlink()
            after_delete = snapshot_repository(fixture.root)

            stale = run_init_cli_process(fixture.root, token=old_token)
            stale_payload = json.loads(stale.stdout)

            self.assertEqual(stale.returncode, 0)
            self.assertEqual(stale_payload["continuation"]["status"], "rejected")
            self.assertTrue(stale_payload["continuation"]["fresh_preview_required"])
            self.assertEqual(stale_payload["content_batch"]["paths"], [])
            self.assertEqual(snapshot_repository(fixture.root), after_delete)

    def test_cli_rejects_a_continuation_token_from_another_repository(self):
        with tempfile.TemporaryDirectory() as td:
            first_root = Path(td) / "first"
            second_root = Path(td) / "second"
            first_fixture = build_small_init_fixture(first_root, files_per_root=13)
            second_fixture = build_small_init_fixture(second_root, files_per_root=13)
            first = run_init_cli(first_fixture.root)

            crossed = run_init_cli_process(
                second_fixture.root,
                token=first["continuation"]["token"],
            )
            payload = json.loads(crossed.stdout)

            self.assertEqual(crossed.returncode, 0)
            self.assertEqual(payload["status"], "stopped")
            self.assertEqual(payload["continuation"]["status"], "rejected")
            self.assertTrue(payload["continuation"]["fresh_preview_required"])
            self.assertEqual(payload["content_batch"]["paths"], [])
            self.assertNotIn(str(first_fixture.root), crossed.stdout)

    def test_cli_unknown_host_keeps_ordinary_docs_out_of_protected_lane(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = build_small_init_fixture(Path(td))
            payload = run_init_cli(fixture.root)

            self.assertEqual(payload["protected_surfaces"]["host"], "unknown")
            guide = next(
                item
                for item in payload["protected_surfaces"]["items"]
                if item["path"] == "docs/guide.md"
            )
            self.assertEqual(guide["role"], "internal-documentation")
            self.assertFalse(guide["protected"])
            self.assertEqual(guide["default_disposition"], "retain")

    def test_cli_github_surface_is_protected_without_leaking_private_content(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = build_small_init_fixture(Path(td))
            github_issue = fixture.root / ".github" / "ISSUE_TEMPLATE" / "bug.md"
            github_issue.parent.mkdir(parents=True)
            github_issue.write_text(
                "PRIVATE_GITHUB_TEMPLATE_CONTENT\n",
                encoding="utf-8",
                newline="\n",
            )
            before = snapshot_repository(fixture.root)

            payload = run_init_cli(fixture.root)
            by_path = {
                item["path"]: item for item in payload["protected_surfaces"]["items"]
            }

            self.assertEqual(payload["protected_surfaces"]["host"], "github")
            self.assertTrue(by_path[".github"]["protected"])
            self.assertNotIn("PRIVATE_GITHUB_TEMPLATE_CONTENT", json.dumps(payload))
            self.assertEqual(
                [item["path"] for item in payload["content_batch"]["paths"]],
                ["docs/guide.md"],
            )
            self.assertEqual(snapshot_repository(fixture.root), before)

    def test_large_init_cli_selects_shared_root_and_reaches_every_file_exactly_once(self):
        with __import__("tempfile").TemporaryDirectory() as td:
            fixture = build_large_init_fixture(Path(td))
            before = snapshot_repository(fixture.root)
            payloads = []
            token = None

            for _ in range(32):
                payload = run_init_cli(fixture.root, token=token)
                payloads.append(payload)
                serialized = json.dumps(payload, sort_keys=True)
                self.assertNotIn("PRIVATE_SENTINEL_029", serialized)
                self.assertNotIn("PRIVATE_SENTINEL_030", serialized)

                if len(payloads) == 1:
                    self.assertEqual(payload["selected_scope"], "docs")
                    self.assertEqual(
                        payload["local_knowledge"]["status"],
                        "present-uninspected",
                    )
                    self.assertEqual(
                        [
                            candidate["path"]
                            for candidate in payload["local_knowledge"]["candidates"]
                        ],
                        list(fixture.private_routes),
                    )
                    self.assertFalse(
                        payload["local_knowledge"]["absence_claim_allowed"]
                    )

                continuation = payload["continuation"]
                token = continuation.get("token")
                if continuation["status"] == "available" and token is None:
                    self.fail("Init CLI did not expose an opaque continuation token")
                if token is None:
                    break
            else:
                self.fail("Init continuation did not reach a terminal response")

            disclosed = [
                item["path"]
                for payload in payloads
                for item in payload["content_batch"]["paths"]
            ]
            self.assertEqual(disclosed, list(fixture.shared_paths))
            self.assertEqual(len(disclosed), len(set(disclosed)))
            self.assertIn(next(iter(fixture.unique_facts)), disclosed)

            final = payloads[-1]
            self.assertEqual(final["completeness"]["status"], "complete")
            self.assertEqual(final["continuation"]["status"], "complete")
            self.assertTrue(final["content_batch"]["complete"])
            self.assertEqual(final["content_batch"]["next_boundary"], None)
            self.assertEqual(snapshot_repository(fixture.root), before)

    def test_cli_rejects_mutated_token_without_writing_or_exposing_paths(self):
        with __import__("tempfile").TemporaryDirectory() as td:
            fixture = build_large_init_fixture(Path(td))
            before = snapshot_repository(fixture.root)
            first = run_init_cli(fixture.root)
            token = first["continuation"]["token"]
            mutated = token[:4] + ("A" if token[4] != "A" else "B") + token[5:]

            completed = run_init_cli_process(fixture.root, token=mutated)

            self.assertEqual(completed.returncode, 2)
            self.assertEqual(
                json.loads(completed.stdout)["error"],
                "content continuation token is invalid",
            )
            self.assertNotIn(str(fixture.root), completed.stdout)
            self.assertEqual(snapshot_repository(fixture.root), before)
