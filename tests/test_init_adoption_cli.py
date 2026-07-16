import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).parents[1]
ADOPTION = ROOT / "skills" / "docs" / "scripts" / "init_closeout.py"


def _git(root: Path, *arguments: str) -> None:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stdout + completed.stderr)


def _build_repository(root: Path, *, second_root: bool = False) -> None:
    docs = root / "docs"
    docs.mkdir(parents=True)
    (docs / "README.md").write_text(
        "# Documentation\n\n- [Guide](guide.md)\n",
        encoding="utf-8",
        newline="\n",
    )
    (docs / "guide.md").write_text(
        "# Guide\n\nShared guidance.\n",
        encoding="utf-8",
        newline="\n",
    )
    (root / ".gitignore").write_text(
        "docs/local/\n",
        encoding="utf-8",
        newline="\n",
    )
    local = docs / "local"
    local.mkdir()
    (local / "private.md").write_text(
        "# PRIVATE_SENTINEL_DO_NOT_READ\n",
        encoding="utf-8",
        newline="\n",
    )
    if second_root:
        other = root / "documentation"
        other.mkdir()
        (other / "README.md").write_text(
            "# Other documentation\n",
            encoding="utf-8",
            newline="\n",
        )
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "fixture@example.invalid")
    _git(root, "config", "user.name", "Fixture")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "fixture")


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(root).parts
    }


def _run(root: Path, operation: str, receipt: Path, *extra: str):
    return subprocess.run(
        [
            sys.executable,
            str(ADOPTION),
            str(root),
            operation,
            "--receipt-file",
            str(receipt),
            *extra,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


class InitAdoptionCliTests(unittest.TestCase):
    @staticmethod
    def _junction(link: Path, target: Path) -> None:
        if os.name != "nt":
            raise unittest.SkipTest("Windows junction test")
        quoted_link = str(link).replace("'", "''")
        quoted_target = str(target).replace("'", "''")
        command = (
            "New-Item -ItemType Junction "
            f"-Path '{quoted_link}' -Target '{quoted_target}' | Out-Null"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            raise unittest.SkipTest(
                f"junction creation failed: {result.stderr.strip()}"
            )

    @unittest.skipUnless(os.name == "nt", "Windows case-folded path coverage")
    def test_case_variant_receipt_path_inside_repository_fails_before_write(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = base / "repo"
            _build_repository(repo)
            receipt = Path(str(repo / "init-receipt.json").swapcase())
            before = _snapshot(repo)

            completed = _run(repo, "adopt-preview", receipt)

            self.assertEqual(completed.returncode, 2)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["status"], "invalid-request")
            self.assertEqual(
                payload["classification"],
                "receipt-must-be-outside-repository",
            )
            self.assertFalse(receipt.exists())
            self.assertEqual(_snapshot(repo), before)

    def test_outside_looking_receipt_through_junction_fails_before_write(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = base / "repo"
            _build_repository(repo)
            (repo / "scratch").mkdir()
            alias = base / "outside-looking"
            self._junction(alias, repo)
            receipt = alias / "scratch" / "init-receipt.json"
            before = _snapshot(repo)

            completed = _run(repo, "adopt-preview", receipt)

            self.assertEqual(completed.returncode, 2)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["status"], "invalid-request")
            self.assertEqual(
                payload["classification"],
                "receipt-must-be-outside-repository",
            )
            self.assertFalse((repo / "scratch" / "init-receipt.json").exists())
            self.assertEqual(_snapshot(repo), before)

    def test_engine_owned_preview_is_all_unchanged_and_zero_repository_write(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = base / "repo"
            receipt = base / "init-receipt.json"
            _build_repository(repo)
            before = _snapshot(repo)

            completed = _run(repo, "adopt-preview", receipt)

            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            payload = json.loads(completed.stdout)
            serialized = json.dumps(payload, sort_keys=True)
            self.assertEqual(payload["schema_version"], 3)
            self.assertEqual(payload["status"], "approval-required")
            self.assertRegex(payload["preview_id"], r"^INIT-[0-9A-F]{12}$")
            self.assertRegex(payload["manifest_sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(payload["handling_summary"], {"left_unchanged": 2})
            self.assertEqual(payload["document_change_count"], 0)
            self.assertEqual(payload["writes"], 0)
            self.assertEqual(
                payload["operational_targets"],
                sorted(payload["operational_targets"]),
            )
            self.assertEqual(
                {
                    target
                    if not target.startswith(".diataxis/manifests/")
                    else ".diataxis/manifests/<manifest>.json"
                    for target in payload["operational_targets"]
                },
                {
                    ".diataxis/events.jsonl",
                    ".diataxis/findings.json",
                    ".diataxis/manifests/<manifest>.json",
                    ".diataxis/state.json",
                },
            )
            self.assertEqual(
                payload["milestones"],
                ["discovery", "evidence complete", "preview ready", "waiting for exact approval"],
            )
            self.assertEqual(sum(row["available"] for row in payload["score_receipt"]["categories"].values()), 100)
            self.assertNotIn("PRIVATE_SENTINEL_DO_NOT_READ", serialized)
            self.assertNotIn("docs/local/private.md", serialized)
            self.assertNotIn("Shared guidance.", serialized)
            self.assertTrue(receipt.is_file())
            self.assertEqual(_snapshot(repo), before)

    def test_exact_engine_receipt_applies_and_second_init_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = base / "repo"
            receipt = base / "init-receipt.json"
            _build_repository(repo)
            preview = json.loads(_run(repo, "adopt-preview", receipt).stdout)

            applied = _run(
                repo,
                "adopt-apply",
                receipt,
                "--approval",
                preview["approval"],
            )

            self.assertEqual(applied.returncode, 0, applied.stderr + applied.stdout)
            result = json.loads(applied.stdout)
            self.assertEqual(result["status"], "applied")
            self.assertTrue(result["successful_event_recorded"])
            self.assertEqual((repo / "docs" / "guide.md").read_text(encoding="utf-8"), "# Guide\n\nShared guidance.\n")

            second_receipt = base / "second-receipt.json"
            second = _run(repo, "adopt-preview", second_receipt)
            self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
            self.assertEqual(json.loads(second.stdout)["status"], "already-initialized")
            self.assertFalse(second_receipt.exists())

    def test_ambiguous_roots_wait_without_receipt_or_repository_write(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = base / "repo"
            receipt = base / "init-receipt.json"
            _build_repository(repo, second_root=True)
            before = _snapshot(repo)

            completed = _run(repo, "adopt-preview", receipt)

            self.assertEqual(completed.returncode, 2)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["status"], "waiting")
            self.assertEqual(payload["classification"], "scope-choice-required")
            self.assertEqual(payload["writes"], 0)
            self.assertFalse(receipt.exists())
            self.assertEqual(_snapshot(repo), before)

    def test_tampered_approval_fails_closed_before_repository_write(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = base / "repo"
            receipt = base / "init-receipt.json"
            _build_repository(repo)
            preview = json.loads(_run(repo, "adopt-preview", receipt).stdout)
            before = _snapshot(repo)
            tampered = re.sub(r"[0-9a-f]$", "0", preview["approval"])
            if tampered == preview["approval"]:
                tampered = preview["approval"][:-1] + "1"

            completed = _run(
                repo,
                "adopt-apply",
                receipt,
                "--approval",
                tampered,
            )

            self.assertEqual(completed.returncode, 2)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["status"], "stale-preview")
            self.assertFalse(payload["successful_event_recorded"])
            self.assertEqual(payload["writes"], 0)
            self.assertEqual(_snapshot(repo), before)

    def test_tampered_engine_receipt_fails_closed_before_repository_write(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = base / "repo"
            receipt = base / "init-receipt.json"
            _build_repository(repo)
            preview = json.loads(_run(repo, "adopt-preview", receipt).stdout)
            receipt_payload = json.loads(receipt.read_text(encoding="utf-8"))
            receipt_payload["evidence"]["dispositions"][0]["reason"] = (
                "Leave this document unchanged after untrusted receipt editing."
            )
            receipt.write_text(
                json.dumps(receipt_payload, sort_keys=True, separators=(",", ":"))
                + "\n",
                encoding="utf-8",
                newline="\n",
            )
            before = _snapshot(repo)

            completed = _run(
                repo,
                "adopt-apply",
                receipt,
                "--approval",
                preview["approval"],
            )

            self.assertEqual(completed.returncode, 2)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["status"], "stale-preview")
            self.assertEqual(payload["classification"], "adoption-receipt-drift")
            self.assertEqual(payload["writes"], 0)
            self.assertEqual(_snapshot(repo), before)

    def test_tracked_corpus_drift_fails_closed_before_repository_write(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = base / "repo"
            receipt = base / "init-receipt.json"
            _build_repository(repo)
            preview = json.loads(_run(repo, "adopt-preview", receipt).stdout)
            (repo / "docs" / "new.md").write_text(
                "# Newly tracked document\n",
                encoding="utf-8",
                newline="\n",
            )
            _git(repo, "add", "docs/new.md")
            before = _snapshot(repo)

            completed = _run(
                repo,
                "adopt-apply",
                receipt,
                "--approval",
                preview["approval"],
            )

            self.assertEqual(completed.returncode, 2)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["status"], "stale-preview")
            self.assertEqual(payload["classification"], "adoption-receipt-drift")
            self.assertEqual(payload["writes"], 0)
            self.assertEqual(_snapshot(repo), before)

    def test_large_ignored_tree_does_not_slow_or_block_adoption(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = base / "repo"
            receipt = base / "init-receipt.json"
            _build_repository(repo)
            ignored = repo / "docs" / "local"
            for index in range(300):
                (ignored / f"entry-{index:03d}.md").write_text(
                    "# Local-only cache\n",
                    encoding="utf-8",
                    newline="\n",
                )

            completed = _run(repo, "adopt-preview", receipt)

            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["handling_summary"], {"left_unchanged": 2})

    def test_broken_git_marker_fails_closed_without_receipt_or_repository_write(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = base / "repo"
            receipt = base / "init-receipt.json"
            (repo / ".git").mkdir(parents=True)
            (repo / "docs").mkdir()
            (repo / "docs" / "README.md").write_text(
                "# Documentation\n",
                encoding="utf-8",
                newline="\n",
            )
            before = _snapshot(repo)

            completed = _run(repo, "adopt-preview", receipt)

            self.assertEqual(completed.returncode, 2)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["status"], "invalid-request")
            self.assertFalse(receipt.exists())
            self.assertEqual(_snapshot(repo), before)


if __name__ == "__main__":
    unittest.main()
