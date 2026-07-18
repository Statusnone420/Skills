import copy
import contextlib
import io
import json
import os
import shutil
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
            "../private-roadmap.md",
            "..%2fprivate-roadmap.md",
            "%2e%2e/private.md",
            "/docs/../private-roadmap.md",
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

        for identifier in (
            "/nix/store/private-checkout",
            "model:/workspace/Skills/private",
            "model@/workspace/Skills/private",
            "model+/workspace/Skills/private",
            "model_/workspace/Skills/private",
        ):
            value = copy.deepcopy(self.receipt)
            value["run"]["model"] = identifier
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
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

    def test_orientation_skips_heading_shaped_frontmatter(self):
        scenarios = {
            "comment": "---\n# not a Markdown H1\ntitle: Guide\n---\n\n## Start\n",
            "block-scalar": "---\ntitle: |\n  ---\n  # not a Markdown H1\n---\n\n## Start\n",
            "body-h1": "---\n# still frontmatter\ntitle: Guide\n---\n\n# Actual H1\n",
        }
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name, source in scenarios.items():
                entry = root / f"{name}.md"
                entry.write_text(source, encoding="utf-8")
                observed = evidence.observe_entry_orientation(root, entry.name)
                with self.subTest(name=name):
                    self.assertEqual(
                        observed["literal_h1"],
                        {"status": "completed", "value": name == "body-h1"},
                    )
                    self.assertEqual(
                        observed["frontmatter_title"]["status"],
                        "unavailable" if name == "block-scalar" else "completed",
                    )

            unresolved = root / "unresolved.md"
            unresolved.write_text("---\ntitle: Guide\n# unresolved frontmatter\n", encoding="utf-8")
            observed = evidence.observe_entry_orientation(root, unresolved.name)
            self.assertEqual(observed["literal_h1"], {"status": "unavailable", "value": None})
            self.assertEqual(
                observed["frontmatter_title"], {"status": "unavailable", "value": None}
            )

            scalar_close = root / "scalar-close.md"
            scalar_close.write_text(
                "---\ntitle: Guide\ndescription: |\n  ---\n# still scalar\n",
                encoding="utf-8",
            )
            observed = evidence.observe_entry_orientation(root, scalar_close.name)
            self.assertEqual(
                observed["literal_h1"], {"status": "unavailable", "value": None}
            )
            self.assertEqual(
                observed["frontmatter_title"],
                {"status": "unavailable", "value": None},
            )

            oversized = root / "oversized.md"
            oversized.write_text(
                "---\ntitle: Guide\n" + "x" * evidence.MAX_FRONTMATTER_BYTES,
                encoding="utf-8",
            )
            observed = evidence.observe_entry_orientation(root, oversized.name)
            self.assertEqual(observed["literal_h1"], {"status": "unavailable", "value": None})
            self.assertEqual(
                observed["frontmatter_title"], {"status": "unavailable", "value": None}
            )

            crlf_oversized = root / "crlf-oversized.md"
            crlf_oversized.write_bytes(
                b"---\r\n" + b"#\r\n" * 25_000 + b"---\r\n# Actual H1\r\n"
            )
            observed = evidence.observe_entry_orientation(root, crlf_oversized.name)
            self.assertEqual(
                observed["literal_h1"], {"status": "unavailable", "value": None}
            )
            self.assertEqual(
                observed["frontmatter_title"], {"status": "unavailable", "value": None}
            )

            mixed = root / "mixed.md"
            mixed.write_text(
                "---\ntitle: Proven title\ntags: [one, two]\n---\n\n## Start\n",
                encoding="utf-8",
            )
            observed = evidence.observe_entry_orientation(root, mixed.name)
            self.assertEqual(
                observed["frontmatter_title"], {"status": "completed", "value": True}
            )

            duplicate = root / "duplicate.md"
            duplicate.write_text(
                "---\ntitle: First\ntitle: Second\n---\n\n## Start\n",
                encoding="utf-8",
            )
            observed = evidence.observe_entry_orientation(root, duplicate.name)
            self.assertEqual(
                observed["frontmatter_title"], {"status": "unavailable", "value": None}
            )

            malformed_quote = root / "malformed-quote.md"
            malformed_quote.write_text(
                "---\ntitle: 'Guide\n---\n\n## Start\n",
                encoding="utf-8",
            )
            observed = evidence.observe_entry_orientation(root, malformed_quote.name)
            self.assertEqual(
                observed["frontmatter_title"], {"status": "unavailable", "value": None}
            )

    def test_orientation_ignores_comments_and_indented_code(self):
        scenarios = {
            "html-comment": "<!--\n# not a heading\n-->\n## Start\n",
            "mdx-comment": "{/*\n# not a heading\n*/}\n## Start\n",
            "space-code": "    # not a heading\n## Start\n",
            "tab-code": "\t# not a heading\n## Start\n",
            "mixed-tab-code": "   \t# not a heading\n## Start\n",
            "html-inline": "<!-- note --> # not a heading\n",
            "html-close": "<!--\nnote\n--> # not a heading\n",
            "mdx-inline": "{/* note */} # not a heading\n",
            "four-backticks": "````\n```\n# not a heading\n````\n",
            "mixed-fence": "```\n~~~\n# not a heading\n```\n",
            "fence-inline-ticks": "```text\nsome ``` inline\n# not a heading\n```\n## Start\n",
            "fence-indented-ticks": "```text\n    ```\n# not a heading\n```\n## Start\n",
            "fence-nbsp-close": "```text\n# not a heading\n```\u00a0\n# still not a heading\n```\n## Start\n",
            "actual-h1": "# Actual H1\n",
            "indented-html-opener": "    <!--\n# Actual H1\n",
            "tab-html-opener": "\t<!--\n# Actual H1\n",
            "indented-mdx-opener": "    {/*\n# Actual H1\n",
        }
        expected_h1 = {
            "actual-h1",
            "indented-html-opener",
            "tab-html-opener",
            "indented-mdx-opener",
        }
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name, source in scenarios.items():
                entry = root / f"{name}.mdx"
                entry.write_text(source, encoding="utf-8")
                observed = evidence.observe_entry_orientation(root, entry.name)
                with self.subTest(name=name):
                    self.assertEqual(
                        observed["literal_h1"],
                        {"status": "completed", "value": name in expected_h1},
                    )

    def test_orientation_respects_markdown_and_mdx_contexts(self):
        scenarios = {
            "plain-md-mdx-syntax.md": (
                "{/*\n# Actual H1\n*/}\n",
                {"status": "completed", "value": True},
            ),
            "midline-html.mdx": (
                "prefix <!--\n# not a heading\n-->\n## Start\n",
                {"status": "completed", "value": True},
            ),
            "midline-mdx.mdx": (
                "prefix {/*\n# not a heading\n*/}\n## Start\n",
                {"status": "completed", "value": False},
            ),
            "chained-html-comments.md": (
                "<!-- one --> <!--\n# not a heading\n-->\n## Start\n",
                {"status": "completed", "value": False},
            ),
            "reopened-html-comment.md": (
                "<!--\n--> prefix <!--\n# not a heading\n-->\n## Start\n",
                {"status": "completed", "value": False},
            ),
            "chained-mdx-comments.mdx": (
                "{/* one */} {/*\n# not a heading\n*/}\n## Start\n",
                {"status": "completed", "value": False},
            ),
            "reopened-mdx-comment.mdx": (
                "{/*\n*/} prefix {/*\n# not a heading\n*/}\n## Start\n",
                {"status": "completed", "value": False},
            ),
            "raw-pre.md": (
                "<pre>\n# not a heading\n</pre>\n## Start\n",
                {"status": "completed", "value": False},
            ),
            "raw-script.mdx": (
                "<script>\n# not a heading\n</script>\n## Start\n",
                {"status": "completed", "value": True},
            ),
            "nbsp-div-boundary.md": (
                "<div\u00a0x>\n# Actual H1\n",
                {"status": "completed", "value": True},
            ),
            "nbsp-script-boundary.md": (
                "<script\u00a0>\n# Actual H1\n",
                {"status": "completed", "value": True},
            ),
            "nbsp-script-close.md": (
                "<script>\n</script\u00a0>\n# not a heading\n</script>\n## Start\n",
                {"status": "completed", "value": False},
            ),
            "raw-with-comment.md": (
                "<pre><!--\n-->\n# not a heading\n</pre>\n## Start\n",
                {"status": "completed", "value": False},
            ),
            "raw-div.md": (
                "<div>\n# not a heading\n</div>\n\n## Start\n",
                {"status": "completed", "value": False},
            ),
            "raw-div-nbsp-line.md": (
                "<div>\n\u00a0\n# not a heading\n",
                {"status": "completed", "value": False},
            ),
            "raw-table.mdx": (
                "<table>\n# not a heading\n</table>\n\n## Start\n",
                {"status": "completed", "value": True},
            ),
            "mdx-lowercase-pre.mdx": (
                "<pre>\n# Actual H1\n</pre>\n",
                {"status": "completed", "value": True},
            ),
            "mdx-lowercase-style.mdx": (
                "<style>\n# Actual H1\n</style>\n",
                {"status": "completed", "value": True},
            ),
            "mdx-lowercase-textarea.mdx": (
                "<textarea>\n# Actual H1\n</textarea>\n",
                {"status": "completed", "value": True},
            ),
            "raw-base.md": (
                "<base>\n# not a heading\n\n## Start\n",
                {"status": "completed", "value": False},
            ),
            "raw-basefont.md": (
                "<basefont>\n# not a heading\n\n## Start\n",
                {"status": "completed", "value": False},
            ),
            "raw-processing.md": (
                "<?target\n# not a heading\n?>\n## Start\n",
                {"status": "completed", "value": False},
            ),
            "raw-cdata.md": (
                "<![CDATA[\n# not a heading\n]]>\n## Start\n",
                {"status": "completed", "value": False},
            ),
            "raw-custom.md": (
                "<custom>\n# not a heading\n\n## Start\n",
                {"status": "unavailable", "value": None},
            ),
            "raw-invalid-custom.md": (
                "<x-card foo==bar>\n# Actual H1\n",
                {"status": "unavailable", "value": None},
            ),
            "mdx-component-child.mdx": (
                "<Custom>\n# Actual H1\n</Custom>\n",
                {"status": "completed", "value": True},
            ),
            "mdx-capitalized-table.mdx": (
                "<Table>\n# Actual H1\n</Table>\n",
                {"status": "completed", "value": True},
            ),
            "mdx-capitalized-script.mdx": (
                "<Script>\n# Actual H1\n</Script>\n",
                {"status": "completed", "value": True},
            ),
            "mdx-jsx-html-string.mdx": (
                '<Custom marker="<!--" />\n# Actual H1\n',
                {"status": "completed", "value": True},
            ),
            "mdx-jsx-comment-string.mdx": (
                '<Custom marker="{/*" />\n# Actual H1\n',
                {"status": "completed", "value": True},
            ),
            "mdx-lowercase-jsx-string.mdx": (
                '<span marker="<!--" />\n# Actual H1\n',
                {"status": "completed", "value": True},
            ),
            "mdx-midline-jsx-string.mdx": (
                'prefix <Custom marker="{/*" />\n# Actual H1\n',
                {"status": "completed", "value": True},
            ),
            "mdx-jsx-quoted-html-marker.mdx": (
                'prefix <Custom label="> <!--" />\n# Actual H1\n',
                {"status": "completed", "value": True},
            ),
            "mdx-jsx-quoted-mdx-marker.mdx": (
                'prefix <Custom label="> {/*" />\n# Actual H1\n',
                {"status": "completed", "value": True},
            ),
            "markdown-quoted-tag-marker.md": (
                'prefix <span title="> <!--">x</span>\n# Actual H1\n',
                {"status": "completed", "value": True},
            ),
            "esm-template.mdx": (
                "export const example = `\n# not a heading\n`\n",
                {"status": "unavailable", "value": None},
            ),
            "esm-then-heading.mdx": (
                "import Example from './example'\n\n# Actual H1\n",
                {"status": "completed", "value": True},
            ),
            "esm-named-import-parked.mdx": (
                "import { Callout } from './components'\n\n# Actual H1\n",
                {"status": "unavailable", "value": None},
            ),
            "esm-named-import-no-boundary.mdx": (
                "import { Callout } from './components'\n# Actual H1\n",
                {"status": "unavailable", "value": None},
            ),
            "esm-import-comment-text.mdx": (
                "import Example from './<!--example-->'\n\n# Actual H1\n",
                {"status": "completed", "value": True},
            ),
            "esm-export-html-text.mdx": (
                'export const marker = "<!--"\n\n# Actual H1\n',
                {"status": "completed", "value": True},
            ),
            "esm-export-mdx-text.mdx": (
                'export const marker = "{/*"\n\n# Actual H1\n',
                {"status": "completed", "value": True},
            ),
            "multiline-esm-template.mdx": (
                "export const data = {\n\n  sample: `\n# not a heading\n`,\n}\n",
                {"status": "unavailable", "value": None},
            ),
            "inline-html-marker.md": (
                "`<!--`\n# Actual H1\n",
                {"status": "completed", "value": True},
            ),
            "inline-mdx-marker.mdx": (
                "`{/*`\n# Actual H1\n",
                {"status": "completed", "value": True},
            ),
            "inline-raw-marker.md": (
                "`<pre>`\n# Actual H1\n",
                {"status": "completed", "value": True},
            ),
            "inline-esm-marker.mdx": (
                "`export const data = {`\n# Actual H1\n",
                {"status": "completed", "value": True},
            ),
            "inline-code-before-hash.md": (
                "`code` # not a heading\n",
                {"status": "completed", "value": False},
            ),
            "nbsp-before-hash.md": (
                "\u00a0# not a heading\n",
                {"status": "completed", "value": False},
            ),
            "em-space-before-hash.md": (
                "\u2003# not a heading\n",
                {"status": "completed", "value": False},
            ),
            "narrow-nbsp-before-hash.md": (
                "\u202f# not a heading\n",
                {"status": "completed", "value": False},
            ),
            "form-feed-before-hash.md": (
                "\f# not a heading\n",
                {"status": "completed", "value": False},
            ),
            "nbsp-after-hash.md": (
                "#\u00a0not a heading\n",
                {"status": "completed", "value": False},
            ),
            "em-space-after-hash.md": (
                "#\u2003not a heading\n",
                {"status": "completed", "value": False},
            ),
            "form-feed-after-hash.md": (
                "#\fnot a heading\n",
                {"status": "completed", "value": False},
            ),
            "multiline-code-html.md": (
                "`<!--\ninside code`\n# Actual H1\n",
                {"status": "completed", "value": True},
            ),
            "multiline-code-mdx.mdx": (
                "`{/*\ninside code`\n# Actual H1\n",
                {"status": "completed", "value": True},
            ),
            "unclosed-code-before-heading.md": (
                "`unclosed\n# Actual H1\n",
                {"status": "completed", "value": True},
            ),
            "unclosed-code-before-blank-heading.md": (
                "`unclosed\n\n# Actual H1\n",
                {"status": "completed", "value": True},
            ),
            "paired-code-across-heading.md": (
                "`foo\n# Actual H1\nbar`\n",
                {"status": "completed", "value": True},
            ),
            "escaped-backticks.md": (
                "\\`\n# Actual H1\n\\`\n",
                {"status": "completed", "value": True},
            ),
            "raw-backtick-boundary.md": (
                "<pre>`\n</pre>\n# Actual H1\n`\n",
                {"status": "completed", "value": True},
            ),
            "list-backtick-fence.md": (
                "- ```\n  # not a heading\n  ```\n",
                {"status": "unavailable", "value": None},
            ),
            "list-tilde-fence.md": (
                "- ~~~\n  # not a heading\n  ~~~\n",
                {"status": "unavailable", "value": None},
            ),
            "list-raw-div.md": (
                "- <div>\n  # not a heading\n\n",
                {"status": "unavailable", "value": None},
            ),
            "list-raw-pre.md": (
                "- <pre>\n  # not a heading\n  </pre>\n",
                {"status": "unavailable", "value": None},
            ),
            "ordered-list-fence.md": (
                "1. ```\n   # not a heading\n   ```\n",
                {"status": "unavailable", "value": None},
            ),
            "plus-list-raw.md": (
                "+ <div>\n  # not a heading\n\n",
                {"status": "unavailable", "value": None},
            ),
            "list-then-heading.md": (
                "- ordinary item\n\n# Actual H1\n",
                {"status": "completed", "value": True},
            ),
            "list-nested-heading.md": (
                "- ordinary item\n  # Actual H1\n",
                {"status": "completed", "value": True},
            ),
            "escaped-html-comment.md": (
                "\\<!--\n# Actual H1\n",
                {"status": "completed", "value": True},
            ),
            "escaped-mdx-comment.mdx": (
                "\\{/*\n# Actual H1\n",
                {"status": "completed", "value": True},
            ),
            "short-inline-html.md": (
                "prefix <!-->\n# Actual H1\n",
                {"status": "completed", "value": True},
            ),
            "short-inline-html-dash.md": (
                "prefix <!--->\n# Actual H1\n",
                {"status": "completed", "value": True},
            ),
            "short-block-html-dash.md": (
                "<!--->\n# Actual H1\n",
                {"status": "completed", "value": True},
            ),
            "multiline-mdx-expression.mdx": (
                "{`\n# not a heading\n`}\n",
                {"status": "unavailable", "value": None},
            ),
            "midline-mdx-expression.mdx": (
                "prefix {<span>\n# not a heading\n</span>}\n## Start\n",
                {"status": "unavailable", "value": None},
            ),
            "tag-then-html-comment.md": (
                "prefix <span> <!--\n# not a heading\n-->\n## Start\n",
                {"status": "completed", "value": True},
            ),
            "jsx-then-mdx-comment.mdx": (
                "prefix <Custom /> {/*\n# not a heading\n*/}\n## Start\n",
                {"status": "completed", "value": False},
            ),
            "leading-jsx-then-mdx-comment.mdx": (
                "<Custom /> {/*\n# not a heading\n*/}\n## Start\n",
                {"status": "unavailable", "value": None},
            ),
            "leading-jsx-then-html-comment.mdx": (
                "<Custom /> <!--\n# not a heading\n-->\n## Start\n",
                {"status": "unavailable", "value": None},
            ),
            "leading-lower-jsx-then-mdx-comment.mdx": (
                "<span /> {/*\n# not a heading\n*/}\n## Start\n",
                {"status": "unavailable", "value": None},
            ),
            "leading-lower-jsx-then-html-comment.mdx": (
                "<span /> <!--\n# not a heading\n-->\n## Start\n",
                {"status": "unavailable", "value": None},
            ),
            "leading-lower-jsx-then-expression.mdx": (
                "<span /> {<Custom>\n# not a heading\n</Custom>}\n## Start\n",
                {"status": "unavailable", "value": None},
            ),
            "leading-custom-element-then-comment.mdx": (
                "<x-card /> {/*\n# not a heading\n*/}\n## Start\n",
                {"status": "unavailable", "value": None},
            ),
            "leading-quoted-lower-jsx-then-comment.mdx": (
                '<span label=">" /> {/*\n# not a heading\n*/}\n## Start\n',
                {"status": "unavailable", "value": None},
            ),
            "html-comment-then-expression.mdx": (
                "prefix <!-- closed --> {<span>\n# not a heading\n</span>}\n## Start\n",
                {"status": "unavailable", "value": None},
            ),
            "mdx-comment-then-expression.mdx": (
                "prefix {/* closed */} {<span>\n# not a heading\n</span>}\n## Start\n",
                {"status": "unavailable", "value": None},
            ),
            "leading-html-comment-then-expression.mdx": (
                "<!-- closed --> {<span>\n# not a heading\n</span>}\n## Start\n",
                {"status": "unavailable", "value": None},
            ),
            "leading-mdx-comment-then-expression.mdx": (
                "{/* closed */} {<span>\n# not a heading\n</span>}\n## Start\n",
                {"status": "unavailable", "value": None},
            ),
            "leading-mdx-then-html-comment.mdx": (
                "{/* closed */} <!--\n# not a heading\n-->\n## Start\n",
                {"status": "unavailable", "value": None},
            ),
        }
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name, (source, expected) in scenarios.items():
                entry = root / name
                entry.write_text(source, encoding="utf-8")
                observed = evidence.observe_entry_orientation(root, entry.name)
                with self.subTest(name=name):
                    self.assertEqual(observed["literal_h1"], expected)

    def test_orientation_structural_lines_are_newline_style_independent(self):
        scenarios = {
            "empty-h1.md": ("#", {"status": "completed", "value": True}),
            "type-one-raw.md": (
                "<script\n# hidden\n</script>",
                {"status": "completed", "value": False},
            ),
            "type-six-raw.md": (
                "<div\n# hidden\n\n## Start",
                {"status": "completed", "value": False},
            ),
            "incomplete-component.mdx": (
                "<Component\n# unresolved",
                {"status": "unavailable", "value": None},
            ),
        }
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for newline in ("\n", "\r\n", "\r"):
                for name, (source, expected) in scenarios.items():
                    entry = root / name
                    entry.write_bytes(source.replace("\n", newline).encode("utf-8"))
                    observed = evidence.observe_entry_orientation(root, entry.name)
                    with self.subTest(name=name, newline=repr(newline)):
                        self.assertEqual(observed["literal_h1"], expected)

    def test_orientation_export_string_scan_is_linear_and_inert(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            closed_entry = root / "long-export-closed.mdx"
            closed_entry.write_text(
                'export const marker = "' + "\\a" * 50_000 + '"\n\n# Actual H1\n',
                encoding="utf-8",
            )
            closed = evidence.observe_entry_orientation(root, closed_entry.name)
            open_entry = root / "long-export-open.mdx"
            open_entry.write_text(
                'export const marker = "' + "\\a" * 50_000,
                encoding="utf-8",
            )
            open_string = evidence.observe_entry_orientation(root, open_entry.name)
        self.assertEqual(
            closed["literal_h1"], {"status": "completed", "value": True}
        )
        self.assertEqual(
            open_string["literal_h1"], {"status": "unavailable", "value": None}
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
            (docs / "README.md").write_text(
                "# Dirty before receipt\n\n## Start\n", encoding="utf-8"
            )
            dirty_start = subprocess.run(
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
        self.assertEqual(
            dirty_start.returncode, 0, dirty_start.stdout + dirty_start.stderr
        )
        dirty_receipt = json.loads(dirty_start.stdout)
        evidence.validate_evidence_receipt(dirty_receipt)
        self.assertEqual(dirty_receipt["git"]["before"]["value"], "dirty")
        self.assertEqual(dirty_receipt["write_audit"]["status"], "unavailable")
        self.assertEqual(
            dirty_receipt["write_audit"]["writes_observed"],
            {"status": "unavailable", "value": None},
        )
        self.assertIn(
            "write_audit.writes_observed", dirty_receipt["unavailable_evidence"]
        )

    def test_receipt_entrypoint_disables_bytecode_before_checker_imports(self):
        with tempfile.TemporaryDirectory() as td:
            copied_scripts = Path(td) / "scripts"
            shutil.copytree(SCRIPTS, copied_scripts)
            for cache in copied_scripts.rglob("__pycache__"):
                shutil.rmtree(cache)
            child_env = os.environ.copy()
            child_env.pop("PYTHONDONTWRITEBYTECODE", None)
            cli = subprocess.run(
                [sys.executable, str(copied_scripts / "evidence_receipt.py"), "--help"],
                cwd=copied_scripts,
                env=child_env,
                capture_output=True,
                text=True,
            )
            cli_caches = list(copied_scripts.rglob("__pycache__"))
            imported = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import sys; sys.path.insert(0, '.'); import evidence_receipt; "
                    "print(sys.dont_write_bytecode)",
                ],
                cwd=copied_scripts,
                env=child_env,
                capture_output=True,
                text=True,
            )
        self.assertEqual(cli.returncode, 0, cli.stdout + cli.stderr)
        self.assertEqual(cli_caches, [])
        self.assertEqual(imported.returncode, 0, imported.stdout + imported.stderr)
        self.assertEqual(imported.stdout.strip(), "False")

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
    def test_prepare_sparse_patterns_are_repository_rooted(self):
        self.assertEqual(
            prepare_docs_corpus._rooted_sparse_patterns(
                ["docs", "apps/docs/content", "mkdocs.yml"]
            ),
            "/docs\n/apps/docs/content\n/mkdocs.yml\n",
        )
        with self.assertRaisesRegex(ValueError, "trailing whitespace"):
            prepare_docs_corpus._rooted_sparse_patterns(["config "])
        with self.assertRaisesRegex(ValueError, "below the repository root"):
            prepare_docs_corpus._rooted_sparse_patterns(["."])

    def test_rooted_sparse_patterns_exclude_nested_name_collisions(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "source"
            source.mkdir()
            _git(source, "init")
            _git(source, "config", "user.email", "sparse@example.invalid")
            _git(source, "config", "user.name", "Sparse Fixture")
            files = {
                "docs/keep.md": "# Keep\n",
                "config": "root config\n",
                "nested/docs/skip.md": "# Skip\n",
                "nested/config": "nested config\n",
            }
            for relative, content in files.items():
                path = source / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            _git(source, "add", ".")
            _git(source, "commit", "-m", "fixture")

            checkout = root / "checkout"
            _git(root, "clone", "--no-checkout", str(source), str(checkout))
            prepare_docs_corpus._run(
                ["git", "-C", str(checkout), "sparse-checkout", "set", "--no-cone", "--stdin"],
                operation="configure sparse checkout fixture",
                input_text=prepare_docs_corpus._rooted_sparse_patterns(["docs", "config"]),
            )
            _git(checkout, "checkout", "--detach", "HEAD")

            self.assertTrue((checkout / "docs" / "keep.md").is_file())
            self.assertTrue((checkout / "config").is_file())
            self.assertFalse((checkout / "nested" / "docs" / "skip.md").exists())
            self.assertFalse((checkout / "nested" / "config").exists())

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

            for trailing_path in ("docs ", "config\u00a0"):
                manifest = json.loads(CORPUS.read_text(encoding="utf-8"))
                manifest["repositories"][0]["sparse_paths"] = [trailing_path]
                trailing_manifest = root / f"trailing-{len(trailing_path)}.json"
                trailing_manifest.write_text(json.dumps(manifest), encoding="utf-8")
                with self.subTest(trailing_path=trailing_path), self.assertRaisesRegex(
                    ValueError, "trailing whitespace"
                ):
                    run_docs_corpus.load_manifest(trailing_manifest)

            manifest = json.loads(CORPUS.read_text(encoding="utf-8"))
            manifest["repositories"][0]["sparse_paths"] = ["."]
            root_manifest = root / "root-sparse.json"
            root_manifest.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "must be below the root"):
                run_docs_corpus.load_manifest(root_manifest)

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
