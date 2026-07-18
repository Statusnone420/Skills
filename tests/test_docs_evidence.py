import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / "skills" / "docs" / "scripts"
TOOLS = ROOT / "tools"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(TOOLS))

from _docs_checker import evidence
from _docs_checker.health import HEALTH_RUBRIC_VERSION, HEALTH_WEIGHTS
import prepare_docs_corpus
import run_docs_corpus


DOGFOOD = ROOT / "evals" / "dogfood" / "cline-0.1.3.json"
CORPUS = ROOT / "evals" / "docs-corpus-v1.json"
BASELINE = ROOT / "evals" / "docs-corpus-baseline-v1.json"
EVIDENCE_CLI = SCRIPTS / "evidence_receipt.py"
PINS = {
    "cline": "d1837366c0b3a8cfa595e098e00c26275426fbc0",
    "supabase": "c1d010a699a76738db63d22d33256b15bd7aea7a",
    "docusaurus": "a0bc32214436d52a5ac9de9be1a515d872987366",
    "vite": "e16ff3a1199293ac9cdfa6132c08fdea162215f3",
    "uv": "bb9eba0bb4e04c08d8a09f1096ede335e54e5503",
    "kubernetes-website": "5e1d1bde0ca03efe09608d59c573d6ec87052c24",
}


def _git(root, *args):
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def _directory_reparse(link, target):
    if os.name == "nt":
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode:
            raise AssertionError(f"junction creation failed: {completed.stderr.strip()}")
    else:
        link.symlink_to(target, target_is_directory=True)


def _fixture_checkout(workspace, spec, files):
    root = workspace / spec["id"]
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.email", "docs-corpus@example.invalid")
    _git(root, "config", "user.name", "Docs Corpus")
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "fixture")
    commit = _git(root, "rev-parse", "HEAD")
    _git(root, "remote", "add", "origin", spec["repository_url"])
    _git(root, "checkout", "--detach", commit)
    spec["commit"] = commit
    return root


class EvidenceReceiptTests(unittest.TestCase):
    def setUp(self):
        self.receipt = json.loads(DOGFOOD.read_text(encoding="utf-8"))

    def test_sanitized_dogfood_receipt_is_complete_and_explicit(self):
        evidence.validate_evidence_receipt(self.receipt)
        health = self.receipt["health"]
        self.assertEqual(health["percentage"], {"status": "completed", "value": 29})
        self.assertFalse(health["score_gates"]["map_has_h1"]["value"])
        self.assertFalse(health["score_gates"]["useful_entry"]["value"])
        self.assertEqual(set(health["categories"]), set(HEALTH_WEIGHTS))
        self.assertEqual(
            {name: row["available"]["value"] for name, row in health["categories"].items()},
            HEALTH_WEIGHTS,
        )
        deterministic = self.receipt["evidence"]["deterministic"]
        self.assertEqual(deterministic["status"], "completed")
        self.assertEqual(len(deterministic["findings"]), 6)
        self.assertEqual({row["kind"] for row in deterministic["findings"]}, {"missing-anchor"})
        semantic = self.receipt["evidence"]["semantic"]
        self.assertEqual(semantic["status"], "completed")
        self.assertEqual(semantic["findings"], [])
        self.assertEqual(self.receipt["repository"]["commit"]["status"], "unavailable")
        self.assertEqual(self.receipt["run"]["duration_seconds"]["value"], 508.747)
        self.assertIn("repository.commit", self.receipt["unavailable_evidence"])

    def test_receipt_rejects_unknown_sensitive_and_absolute_data(self):
        for mutation in ("unknown", "raw_transcript"):
            value = copy.deepcopy(self.receipt)
            value[mutation] = "not allowed"
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                evidence.validate_evidence_receipt(value)
        value = copy.deepcopy(self.receipt)
        value["evidence"]["deterministic"]["findings"][0]["path"] = {
            "status": "completed",
            "value": r"C:\private\notes.md",
        }
        with self.assertRaises(ValueError):
            evidence.validate_evidence_receipt(value)

        for target in (
            "/tmp/private/secret.txt",
            "/root/.ssh/id_rsa",
            r"\\server\private\secret.txt",
            "see /tmp/private/secret.txt",
            "file:///etc/passwd",
            "guide.md?token=SUPERSECRET",
            "guide.md?access_token=SUPERSECRET",
            "%2Froot%2F.ssh%2Fid_rsa",
            "guide.md?%74oken=SUPERSECRET",
        ):
            value = copy.deepcopy(self.receipt)
            value["evidence"]["deterministic"]["findings"][0]["target"] = {
                "status": "completed",
                "value": target,
            }
            with self.subTest(target=target), self.assertRaises(ValueError):
                evidence.validate_evidence_receipt(value)

        value = copy.deepcopy(self.receipt)
        value["evidence"]["deterministic"]["findings"][0]["target"] = {
            "status": "completed",
            "value": "/docs/safe-guide#setup",
        }
        evidence.validate_evidence_receipt(value)

        value = copy.deepcopy(self.receipt)
        value["run"]["model"] = "sk-proj-synthetic012345678901234567890"
        with self.assertRaisesRegex(ValueError, "credential-shaped"):
            evidence.validate_evidence_receipt(value)

    def test_completed_health_requires_every_category(self):
        value = copy.deepcopy(self.receipt)
        del value["health"]["categories"]["titles"]
        with self.assertRaisesRegex(ValueError, "every category"):
            evidence.validate_evidence_receipt(value)

        value = copy.deepcopy(self.receipt)
        value["health"]["percentage"] = {"status": "completed", "value": 101}
        with self.assertRaisesRegex(ValueError, "exceed 100"):
            evidence.validate_evidence_receipt(value)

        value = copy.deepcopy(self.receipt)
        value["health"]["categories"]["entry"]["earned"] = {
            "status": "completed",
            "value": 21,
        }
        with self.assertRaisesRegex(ValueError, "exceeds available"):
            evidence.validate_evidence_receipt(value)

    def test_unavailable_is_not_zero_and_must_match_index(self):
        with self.assertRaises(ValueError):
            evidence.evidence_value("unavailable", 0)
        value = copy.deepcopy(self.receipt)
        value["unavailable_evidence"] = []
        with self.assertRaisesRegex(ValueError, "unavailable_evidence"):
            evidence.validate_evidence_receipt(value)

    def test_orientation_is_inert_and_does_not_change_rubric(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            entry = root / "docs" / "index.mdx"
            entry.parent.mkdir()
            entry.write_text(
                "---\ntitle: Rendered title\n---\n\n```js\n# not a heading\nthrow new Error('never execute')\n```\n\n## Start\n",
                encoding="utf-8",
            )
            observed = evidence.observe_entry_orientation(root, "docs/index.mdx")
        self.assertEqual(observed["literal_h1"], {"status": "completed", "value": False})
        self.assertEqual(observed["frontmatter_title"], {"status": "completed", "value": True})
        self.assertEqual(observed["provider_rendered_title"]["status"], "unavailable")
        self.assertEqual(HEALTH_RUBRIC_VERSION, 2)
        self.assertEqual(
            HEALTH_WEIGHTS,
            {"entry": 20, "path_safety": 15, "links": 20, "anchors": 10, "reachability": 25, "titles": 10},
        )

    def test_stdout_receipt_entrypoint_combines_one_checker_run(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            root = base / "repo"
            root.mkdir()
            _git(root, "init")
            _git(root, "config", "user.email", "receipt@example.invalid")
            _git(root, "config", "user.name", "Receipt Fixture")
            docs = root / "docs"
            docs.mkdir()
            (docs / "README.md").write_text("# Home\n\n## Start\n", encoding="utf-8")
            _git(root, "add", ".")
            _git(root, "commit", "-m", "fixture")
            metadata = base / "metadata.json"
            metadata.write_text(
                json.dumps(
                    {
                        "receipt_id": "fixture-receipt",
                        "repository_identifier": "example.invalid/docs/fixture",
                        "run": {
                            "id": "fixture-run",
                            "client": "test-harness",
                            "model_provider": "local",
                            "model": "deterministic-harness",
                            "effort": "not-applicable",
                            "turns": {"status": "completed", "value": 1},
                            "duration_seconds": {"status": "not_assessed", "value": None},
                            "commands": ["docs-check"],
                        },
                        "semantic": run_docs_corpus._semantic_not_assessed(),
                        "unresolved": [],
                        "doctor": {
                            "status": "not_assessed",
                            "treatment_fingerprint": evidence.evidence_value("not_assessed"),
                            "approval_line_present": evidence.evidence_value("not_assessed"),
                        },
                    }
                ),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(EVIDENCE_CLI),
                    str(root),
                    "--metadata-file",
                    str(metadata),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        receipt = json.loads(completed.stdout)
        evidence.validate_evidence_receipt(receipt)
        self.assertEqual(receipt["health"]["status"], "completed")
        self.assertEqual(receipt["write_audit"]["writes_observed"]["value"], 0)


class CorpusHarnessTests(unittest.TestCase):
    def test_manifest_has_six_exact_immutable_pins(self):
        manifest = run_docs_corpus.load_manifest(CORPUS)
        self.assertEqual({row["id"]: row["commit"] for row in manifest["repositories"]}, PINS)
        self.assertEqual(
            {row["provider"] for row in manifest["repositories"]},
            {"mintlify", "custom-mdx", "docusaurus", "vitepress", "mkdocs", "hugo"},
        )
        for row in manifest["repositories"]:
            self.assertTrue(row["entry"])
            self.assertTrue(row["authority_probes"])
            self.assertTrue(row["config_probes"])
            self.assertTrue(row["sparse_paths"])

    def test_checked_in_baseline_separates_supported_and_unavailable_evidence(self):
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        self.assertEqual(baseline["checker_version"], "0.1.4")
        self.assertEqual(baseline["rubric"]["changed"], False)
        rows = {row["id"]: row for row in baseline["repositories"]}
        self.assertEqual(set(rows), set(PINS))
        for row in rows.values():
            evidence.validate_evidence_receipt(row["receipt"])
            self.assertTrue(all(item["status"] == "completed" for item in row["configurations"]))
        self.assertEqual(rows["cline"]["receipt"]["health"]["percentage"]["value"], 29)
        self.assertEqual(rows["cline"]["receipt"]["counts"]["pages"]["value"], 107)
        self.assertEqual(rows["cline"]["receipt"]["counts"]["hidden_pages"]["value"], 3)
        for repository_id in set(rows) - {"cline"}:
            health = rows[repository_id]["receipt"]["health"]
            self.assertEqual(health["status"], "not_assessed")
            self.assertIsNone(health["percentage"]["value"])

    def test_unsupported_provider_is_observed_but_not_scored_or_executed(self):
        with tempfile.TemporaryDirectory() as td:
            workspace = Path(td)
            spec = {
                "id": "vite",
                "repository_url": "https://github.com/vitejs/vite.git",
                "commit": "0" * 40,
                "provider": "vitepress",
                "measurement": "unsupported",
                "scope": "docs",
                "entry": "docs/index.md",
                "authority_probes": ["docs/.vitepress/config.ts"],
                "config_probes": ["docs/.vitepress/config.ts"],
                "sparse_paths": ["docs"],
            }
            root = _fixture_checkout(
                workspace,
                spec,
                {
                    "docs/index.md": "---\ntitle: Vite\n---\n<script>throw new Error('never execute')</script>\n",
                    "docs/.vitepress/config.ts": "throw new Error('never execute')\n",
                },
            )
            before = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
            result = run_docs_corpus.run_repository(workspace, spec)
            after = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
        self.assertEqual(before, after)
        self.assertEqual(result["receipt"]["health"]["status"], "not_assessed")
        self.assertEqual(result["receipt"]["health"]["percentage"]["value"], None)
        self.assertEqual(result["receipt"]["counts"]["pages"]["status"], "not_assessed")
        self.assertEqual(result["configurations"][0]["status"], "completed")
        self.assertTrue(result["configurations"][0]["sha256"].startswith("sha256:"))

    def test_checkout_verification_rejects_branch_dirty_and_wrong_commit(self):
        with tempfile.TemporaryDirectory() as td:
            workspace = Path(td)
            spec = {
                "id": "uv",
                "repository_url": "https://github.com/astral-sh/uv.git",
                "commit": "0" * 40,
                "provider": "mkdocs",
                "measurement": "unsupported",
                "scope": "docs",
                "entry": "docs/index.md",
                "authority_probes": ["mkdocs.yml"],
                "config_probes": ["mkdocs.yml"],
                "sparse_paths": ["docs", "mkdocs.yml"],
            }
            root = _fixture_checkout(
                workspace,
                spec,
                {"docs/index.md": "# uv\n", "mkdocs.yml": "site_name: uv\n"},
            )
            good_commit = spec["commit"]
            spec["commit"] = "f" * 40
            with self.assertRaisesRegex(ValueError, "commit mismatch"):
                run_docs_corpus.verify_checkout(workspace, spec)
            spec["commit"] = good_commit
            (root / "dirty.txt").write_text("dirty\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "dirty"):
                run_docs_corpus.verify_checkout(workspace, spec)
            (root / "dirty.txt").unlink()
            _git(root, "switch", "-c", "fixture-branch")
            with self.assertRaisesRegex(ValueError, "not detached"):
                run_docs_corpus.verify_checkout(workspace, spec)

    def test_missing_checkout_error_is_sanitized(self):
        with tempfile.TemporaryDirectory() as td:
            workspace = Path(td)
            spec = copy.deepcopy(run_docs_corpus.load_manifest(CORPUS)["repositories"][0])
            with self.assertRaisesRegex(ValueError, r"^corpus repository is missing: cline$") as raised:
                run_docs_corpus.verify_checkout(workspace, spec)
            self.assertNotIn(str(workspace), str(raised.exception))

    def test_prepare_refuses_unowned_or_existing_workspace(self):
        with tempfile.TemporaryDirectory() as td:
            original = prepare_docs_corpus.WORKSPACE_ROOT
            try:
                prepare_docs_corpus.WORKSPACE_ROOT = Path(td)
                unowned = Path(td) / "unowned"
                unowned.mkdir()
                (unowned / "keep.txt").write_text("keep\n", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "unowned"):
                    prepare_docs_corpus._workspace(unowned, CORPUS, "docs-corpus-v1")
                self.assertEqual((unowned / "keep.txt").read_text(encoding="utf-8"), "keep\n")
            finally:
                prepare_docs_corpus.WORKSPACE_ROOT = original

    def test_runner_output_cannot_write_into_workspace_or_through_reparse(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = root / "workspace"
            workspace.mkdir()
            target_output = workspace / "cline" / "receipt.json"
            with self.assertRaisesRegex(ValueError, "outside the corpus workspace"):
                run_docs_corpus._output_path(target_output, workspace)
            self.assertFalse(target_output.exists())

            outside = root / "outside"
            outside.mkdir()
            alias = root / "alias"
            _directory_reparse(alias, outside)
            with self.assertRaisesRegex(ValueError, "symlink|reparse"):
                run_docs_corpus._output_path(alias / "receipt.json", workspace)

    def test_prepare_reparse_workspace_cannot_write_outside(self):
        with tempfile.TemporaryDirectory() as td:
            original = prepare_docs_corpus.WORKSPACE_ROOT
            base = Path(td)
            root = base / "owned"
            root.mkdir()
            outside = base / "outside"
            outside.mkdir()
            sentinel = outside / "sentinel.txt"
            sentinel.write_text("keep\n", encoding="utf-8")
            workspace = root / "docs-corpus-v1"
            _directory_reparse(workspace, outside)
            try:
                prepare_docs_corpus.WORKSPACE_ROOT = root
                with self.assertRaisesRegex(ValueError, "symlink|reparse"):
                    prepare_docs_corpus._workspace(workspace, CORPUS, "docs-corpus-v1")
                self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep\n")
                self.assertFalse((outside / prepare_docs_corpus.MARKER).exists())
            finally:
                prepare_docs_corpus.WORKSPACE_ROOT = original


if __name__ == "__main__":
    unittest.main()
