import copy
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import quote


ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / "skills" / "docs" / "scripts"
TOOLS = ROOT / "tools"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(TOOLS))

from _docs_checker import evidence
from _docs_checker.health import HEALTH_RUBRIC_VERSION, HEALTH_WEIGHTS
import prepare_docs_corpus
import run_docs_corpus
import evidence_receipt as evidence_receipt_cli


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
            "#api:v1?token=SUPERSECRET",
        ):
            value = copy.deepcopy(self.receipt)
            value["evidence"]["deterministic"]["findings"][0]["target"] = {
                "status": "completed",
                "value": target,
            }
            with self.subTest(target=target), self.assertRaises(ValueError):
                evidence.validate_evidence_receipt(value)
            with self.subTest(target=f"canonical-{target}"), self.assertRaises(ValueError):
                evidence.canonical_receipt_bytes(value)

        for target in ("/docs/safe-guide#api:v1", "/docs/safe-guide#file:v1"):
            value = copy.deepcopy(self.receipt)
            value["evidence"]["deterministic"]["findings"][0]["target"] = {
                "status": "completed",
                "value": target,
            }
            with self.subTest(target=target):
                evidence.validate_evidence_receipt(value)

        for credential in (
            "sk-proj-synthetic012345678901234567890",
            "glpat-synthetic012345678901234567890",
            "npm_012345678901234567890123456789012345",
            "pypi-synthetic012345678901234567890",
            "eyJsynthetic01.eyJsynthetic02.synthetic-signature",
            "".join(("sk", "_live_", "synthetic012345678901234567890")),
            "".join(("rk", "_live_", "synthetic012345678901234567890")),
            "lin_api_synthetic012345678901234567890",
            "ya29.synthetic012345678901234567890",
            "SG.synthetic012345.synthetic01234567890",
            "hf_synthetic012345678901234567890",
            "sk-ant-synthetic012345678901234567890",
            "gsk_synthetic0123456789012345678901234567890",
            "r8_0123456789012345678901234567890123456",
            "shpat_synthetic012345678901234567890",
            "sq0atp-synthetic012345678901234567890",
            "dop_v1_synthetic012345678901234567890",
            "vercel_synthetic012345678901234567890",
            "sbp_synthetic012345678901234567890",
            "ASIA0123456789ABCDEF",
        ):
            value = copy.deepcopy(self.receipt)
            value["run"]["model"] = credential
            with self.subTest(credential=credential), self.assertRaisesRegex(
                ValueError, "credential-shaped"
            ):
                evidence.validate_evidence_receipt(value)
            with self.subTest(credential=f"canonical-{credential}"), self.assertRaisesRegex(
                ValueError, "credential-shaped"
            ):
                evidence.canonical_receipt_bytes(value)

        for private_path in (
            ".local/private.md",
            r"\Users\private\notes.md",
            "docs/guide.md?token=SUPERSECRET",
            quote(quote(quote(quote("/root/.ssh/id_rsa", safe=""), safe=""), safe=""), safe=""),
        ):
            value = copy.deepcopy(self.receipt)
            value["evidence"]["deterministic"]["findings"][0]["path"] = {
                "status": "completed",
                "value": private_path,
            }
            with self.subTest(private_path=private_path), self.assertRaises(ValueError):
                evidence.validate_evidence_receipt(value)

        deeply_encoded_target = "/tmp/private/secret.txt"
        deeply_encoded_parameter = "guide.md?token=SUPERSECRET"
        for _ in range(4):
            deeply_encoded_target = quote(deeply_encoded_target, safe="")
            deeply_encoded_parameter = quote(deeply_encoded_parameter, safe="")
        for target in (deeply_encoded_target, deeply_encoded_parameter):
            value = copy.deepcopy(self.receipt)
            value["evidence"]["deterministic"]["findings"][0]["target"] = {
                "status": "completed",
                "value": target,
            }
            with self.subTest(target=target), self.assertRaises(ValueError):
                evidence.validate_evidence_receipt(value)

        for target in (
            "<mailto:private@example.invalid>",
            "<data:text/plain,private>",
            " <https://example.invalid/private> ",
            '<data:text/plain;base64,U1VQRVJTRUNSRVQ=> "title"',
            '<mailto:private@example.invalid> "contact"',
            "<<data:text/plain,private>>",
            "title data:text/plain,private",
            "docs/(data:text/plain,private)",
        ):
            value = copy.deepcopy(self.receipt)
            value["evidence"]["deterministic"]["findings"][0]["target"] = {
                "status": "completed",
                "value": target,
            }
            with self.subTest(target=target), self.assertRaises(ValueError):
                evidence.validate_evidence_receipt(value)
            with self.subTest(target=f"canonical-{target}"), self.assertRaises(ValueError):
                evidence.canonical_receipt_bytes(value)

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
        value["health"]["percentage"] = {"status": "completed", "value": 29.5}
        with self.assertRaisesRegex(ValueError, "integer"):
            evidence.validate_evidence_receipt(value)

        value = copy.deepcopy(self.receipt)
        value["health"]["categories"]["entry"]["earned"] = {
            "status": "completed",
            "value": 21,
        }
        with self.assertRaisesRegex(ValueError, "exceeds available"):
            evidence.validate_evidence_receipt(value)

    def test_builder_counts_hidden_pages_separately_from_pages(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            docs = root / "docs"
            docs.mkdir()
            (docs / "README.md").write_text("# Home\n\n## Start\n", encoding="utf-8")
            findings, _, measurements = run_docs_corpus.check(
                root,
                map_path="docs/README.md",
                scope="docs",
                _measurements=True,
            )
            health = run_docs_corpus.health_summary(
                measurements,
                findings=measurements["active_findings"],
                baseline=measurements["baseline"],
                freshness=measurements["freshness"],
                coverage=measurements["coverage"],
            )
            navigation = copy.deepcopy(measurements["navigation"])
            navigation["navigated_pages"] = ["docs/README.md", "docs/guide.md"]
            navigation["hidden_pages"] = ["docs/guide.md"]
            builder = {
                "receipt_id": "page-count-regression",
                "repository_identifier": "example.invalid/docs/page-count",
                "commit": "0" * 40,
                "checker_version": "0.1.4",
                "run": copy.deepcopy(self.receipt["run"]),
                "checker_payload": {
                    "navigation": navigation,
                    "health": health,
                    "findings": findings,
                },
                "orientation": copy.deepcopy(self.receipt["orientation"]),
                "semantic": run_docs_corpus._semantic_not_assessed(),
            }
            receipt = evidence.build_evidence_receipt(**builder)

            for field, malformed in (
                ("run", None),
                ("semantic", {}),
                (
                    "semantic",
                    {"status": "not_assessed", "evaluator": None, "findings": []},
                ),
                (
                    "semantic",
                    {
                        "status": "not_assessed",
                        "evaluator": run_docs_corpus._semantic_not_assessed()["evaluator"],
                        "findings": None,
                    },
                ),
                ("unresolved", None),
                ("doctor", None),
                ("doctor", {}),
            ):
                malformed_builder = {**builder, field: malformed}
                with self.subTest(field=field, malformed=malformed), self.assertRaises(ValueError):
                    evidence.build_evidence_receipt(**malformed_builder)
        self.assertEqual(receipt["counts"]["pages"], {"status": "completed", "value": 2})
        self.assertEqual(receipt["counts"]["hidden_pages"], {"status": "completed", "value": 1})

    def test_unavailable_is_not_zero_and_must_match_index(self):
        with self.assertRaises(ValueError):
            evidence.evidence_value("unavailable", 0)
        value = copy.deepcopy(self.receipt)
        value["unavailable_evidence"] = []
        with self.assertRaisesRegex(ValueError, "unavailable_evidence"):
            evidence.validate_evidence_receipt(value)

    def test_deep_receipt_input_fails_with_controlled_validation_error(self):
        deeply_nested = []
        current = deeply_nested
        for _ in range(1_100):
            child = []
            current.append(child)
            current = child
        value = copy.deepcopy(self.receipt)
        value["evidence"]["semantic"]["findings"] = deeply_nested
        with self.assertRaises(ValueError):
            evidence.validate_evidence_receipt(value)

        for field, malformed in (("semantic", []), ("unresolved", {})):
            value = copy.deepcopy(self.receipt)
            value["evidence"][field]["status"] = malformed
            with self.subTest(field=field), self.assertRaises(ValueError):
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

    def test_metadata_input_is_bounded_before_json_parse(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            metadata = root / "oversized.json"
            metadata.write_bytes(b" " * (evidence.MAX_RECEIPT_BYTES + 1))
            with self.assertRaisesRegex(ValueError, "exceeds capacity"):
                evidence_receipt_cli._metadata(metadata)

            deeply_nested = root / "deeply-nested.json"
            deeply_nested.write_text("[" * 2_000 + "]" * 2_000, encoding="utf-8")
            with self.assertRaises(ValueError):
                evidence_receipt_cli._metadata(deeply_nested)

    def test_receipt_cli_io_failure_does_not_expose_private_path(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            metadata = root / "metadata.json"
            metadata.write_text(
                json.dumps(
                    {
                        "receipt_id": "io-failure",
                        "repository_identifier": "example.invalid/docs/io-failure",
                        "run": copy.deepcopy(self.receipt["run"]),
                        "semantic": run_docs_corpus._semantic_not_assessed(),
                        "unresolved": [],
                        "doctor": copy.deepcopy(self.receipt["doctor"]),
                    }
                ),
                encoding="utf-8",
            )
            private = Path(r"C:\Users\Synthetic\private-repo\docs\README.md")
            output = io.StringIO()
            with mock.patch.object(evidence_receipt_cli, "_git", return_value=""), mock.patch.object(
                evidence_receipt_cli,
                "check",
                side_effect=OSError(13, "access denied", str(private)),
            ), contextlib.redirect_stdout(output):
                self.assertEqual(
                    evidence_receipt_cli.main([str(root), "--metadata-file", str(metadata)]),
                    2,
                )
        self.assertEqual(
            json.loads(output.getvalue()),
            {"status": "failed", "error": "evidence receipt I/O failed", "receipt": None},
        )
        self.assertNotIn(str(private), output.getvalue())


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

    def test_manifest_input_is_bounded_before_parse_and_iteration(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            oversized = root / "oversized.json"
            oversized.write_bytes(b" " * (run_docs_corpus.MAX_MANIFEST_BYTES + 1))
            with self.assertRaisesRegex(ValueError, "exceeds capacity"):
                run_docs_corpus.load_manifest(oversized)

            deeply_nested = root / "deeply-nested.json"
            deeply_nested.write_text("[" * 2_000 + "]" * 2_000, encoding="utf-8")
            with self.assertRaises(ValueError):
                run_docs_corpus.load_manifest(deeply_nested)

            for malformed in ({}, ["nested"], 42, None):
                manifest = json.loads(CORPUS.read_text(encoding="utf-8"))
                manifest["repositories"][0]["config_probes"] = [malformed]
                malformed_path = root / f"malformed-{type(malformed).__name__}.json"
                malformed_path.write_text(json.dumps(manifest), encoding="utf-8")
                with self.subTest(malformed=malformed), self.assertRaisesRegex(
                    ValueError, "config_probes is invalid"
                ):
                    run_docs_corpus.load_manifest(malformed_path)

            manifest = json.loads(CORPUS.read_text(encoding="utf-8"))
            manifest["repositories"][0]["provider"] = {}
            malformed_provider = root / "malformed-provider.json"
            malformed_provider.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "provider is invalid"):
                run_docs_corpus.load_manifest(malformed_provider)

            for control_path in ("docs\n/*", "docs\r\n!/*", "docs\tprivate", "docs\x00private"):
                manifest = json.loads(CORPUS.read_text(encoding="utf-8"))
                manifest["repositories"][0]["sparse_paths"] = [control_path]
                control_manifest = root / f"control-{len(control_path)}.json"
                control_manifest.write_text(json.dumps(manifest), encoding="utf-8")
                with self.subTest(control_path=control_path), self.assertRaisesRegex(
                    ValueError, "control characters"
                ):
                    run_docs_corpus.load_manifest(control_manifest)

            for pattern_path in ("*", "docs/**", "docs/[ab]", "!docs/private"):
                manifest = json.loads(CORPUS.read_text(encoding="utf-8"))
                manifest["repositories"][0]["sparse_paths"] = [pattern_path]
                pattern_manifest = root / f"pattern-{len(pattern_path)}.json"
                pattern_manifest.write_text(json.dumps(manifest), encoding="utf-8")
                with self.subTest(pattern_path=pattern_path), self.assertRaisesRegex(
                    ValueError, "contains pattern syntax"
                ):
                    run_docs_corpus.load_manifest(pattern_manifest)

            for private_path in (
                ".local/private-config.json",
                "docs/sk-live-abcdefghijklmnop.json",
                "docs/%252elocal/private-config.json",
            ):
                manifest = json.loads(CORPUS.read_text(encoding="utf-8"))
                manifest["repositories"][0]["config_probes"] = [private_path]
                private_manifest = root / f"private-{len(private_path)}.json"
                private_manifest.write_text(json.dumps(manifest), encoding="utf-8")
                with self.subTest(private_path=private_path), self.assertRaises(ValueError):
                    run_docs_corpus.load_manifest(private_manifest)

            manifest = json.loads(CORPUS.read_text(encoding="utf-8"))
            manifest["repositories"][0]["config_probes"] = [
                f"docs/config-{index}.json"
                for index in range(run_docs_corpus.MAX_PROBES_PER_REPOSITORY + 1)
            ]
            too_many = root / "too-many.json"
            too_many.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "config_probes is invalid"):
                run_docs_corpus.load_manifest(too_many)

            manifest = json.loads(CORPUS.read_text(encoding="utf-8"))
            manifest["repositories"][0]["entry"] = (
                "docs/" + "x" * run_docs_corpus.MAX_MANIFEST_PATH_BYTES
            )
            long_path = root / "long-path.json"
            long_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "exceeds capacity"):
                run_docs_corpus.load_manifest(long_path)

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

    def test_configuration_paths_are_sanitized_before_outer_corpus_output(self):
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
                "config_probes": ["docs/sk-live-abcdefghijklmnop.json"],
                "sparse_paths": ["docs"],
            }
            _fixture_checkout(
                workspace,
                spec,
                {
                    "docs/index.md": "# Vite\n",
                    "docs/.vitepress/config.ts": "export default {}\n",
                    spec["config_probes"][0]: "inert\n",
                },
            )
            with self.assertRaisesRegex(ValueError, "credential-shaped"):
                run_docs_corpus.run_repository(workspace, spec)

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

    def test_prepare_manifest_and_marker_reads_are_bounded(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            original = prepare_docs_corpus.WORKSPACE_ROOT
            prepare_docs_corpus.WORKSPACE_ROOT = base
            try:
                oversized_manifest = base / "oversized-manifest.json"
                oversized_manifest.write_bytes(b" " * (run_docs_corpus.MAX_MANIFEST_BYTES + 1))
                with self.assertRaisesRegex(ValueError, "manifest exceeds capacity"):
                    prepare_docs_corpus._workspace(
                        base / "manifest-workspace",
                        oversized_manifest,
                        "docs-corpus-v1",
                    )

                marker_workspace = base / "marker-workspace"
                marker_workspace.mkdir()
                (marker_workspace / prepare_docs_corpus.MARKER).write_bytes(
                    b" " * (prepare_docs_corpus.MAX_MARKER_BYTES + 1)
                )
                with self.assertRaises(ValueError):
                    prepare_docs_corpus._workspace(
                        marker_workspace,
                        CORPUS,
                        "docs-corpus-v1",
                    )

                deep_marker_workspace = base / "deep-marker-workspace"
                deep_marker_workspace.mkdir()
                (deep_marker_workspace / prepare_docs_corpus.MARKER).write_text(
                    "[" * 2_000 + "]" * 2_000,
                    encoding="utf-8",
                )
                with self.assertRaises(ValueError):
                    prepare_docs_corpus._workspace(
                        deep_marker_workspace,
                        CORPUS,
                        "docs-corpus-v1",
                    )
            finally:
                prepare_docs_corpus.WORKSPACE_ROOT = original

    def test_prepare_command_failure_does_not_expose_checkout_path(self):
        private = Path(r"C:\Users\Synthetic\private\docs-corpus")
        failed = mock.Mock(returncode=1)
        with mock.patch.object(prepare_docs_corpus.subprocess, "run", return_value=failed):
            with self.assertRaisesRegex(ValueError, "initialize repository") as raised:
                prepare_docs_corpus._run(
                    ["git", "init", str(private)],
                    operation="initialize repository",
                )
        self.assertNotIn(str(private), str(raised.exception))

        output = io.StringIO()
        with mock.patch.object(
            prepare_docs_corpus,
            "prepare",
            side_effect=OSError(f"access denied: {private}"),
        ), contextlib.redirect_stdout(output):
            self.assertEqual(prepare_docs_corpus.main([]), 2)
        self.assertEqual(
            json.loads(output.getvalue()),
            {"status": "failed", "error": "corpus preparation I/O failed"},
        )
        self.assertNotIn(str(private), output.getvalue())

        output = io.StringIO()
        with mock.patch.object(
            run_docs_corpus,
            "run_corpus",
            side_effect=OSError(f"access denied: {private}"),
        ), contextlib.redirect_stdout(output):
            self.assertEqual(run_docs_corpus.main([]), 2)
        self.assertEqual(
            json.loads(output.getvalue()),
            {"status": "failed", "error": "corpus runner I/O failed"},
        )
        self.assertNotIn(str(private), output.getvalue())

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
