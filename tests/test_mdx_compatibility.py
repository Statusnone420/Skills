import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SKILL = ROOT / "skills" / "docs"
INIT_CLOSEOUT = SKILL / "scripts" / "init_closeout.py"
sys.path.insert(0, str(SKILL / "scripts"))
import check as docs_checker


class MdxCompatibilityTests(unittest.TestCase):
    def _write_cline_shaped_fixture(
        self,
        root,
        *,
        schema="https://mintlify.com/docs.json",
    ):
        docs = root / "docs"
        guide = docs / "getting-started"
        guide.mkdir(parents=True)
        (docs / "docs.json").write_text(
            json.dumps(
                {
                    "$schema": schema,
                    "navigation": {
                        "tabs": [
                            {
                                "tab": "Docs",
                                "groups": [
                                    {
                                        "group": "Start",
                                        "pages": [
                                            "cline-overview",
                                            "getting-started/installing-cline",
                                        ],
                                    }
                                ],
                            }
                        ]
                    },
                }
            ),
            encoding="utf-8",
        )
        (docs / "cline-overview.mdx").write_text(
            """---
title: Cline overview
---

import { Card } from '/snippets/components.jsx'

# Cline overview

Cline is an AI coding agent.

<Card title="Install">
  Read the [installation guide](getting-started/installing-cline.mdx).
</Card>
""",
            encoding="utf-8",
        )
        (guide / "installing-cline.mdx").write_text(
            """---
title: Installing Cline
---

# Installing Cline

Install the extension and choose a model provider.
""",
            encoding="utf-8",
        )
        subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
        subprocess.run(["git", "add", "docs"], cwd=root, check=True)

    def _checker(self, root, *, map_path, scope="docs"):
        return subprocess.run(
            [
                sys.executable,
                "-B",
                str(SKILL / "scripts" / "check.py"),
                str(root),
                "--json",
                "--agent",
                "--scope",
                scope,
                "--map",
                map_path,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )

    def test_explicit_discovery_selects_and_protects_mdx_corpus(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_cline_shaped_fixture(root)

            payload = docs_checker.discover_init_scope(root, explicit_scope="docs")

        selected = [item["path"] for item in payload["scope_metadata"]["paths"]]
        self.assertEqual(
            selected,
            [
                "docs/cline-overview.mdx",
                "docs/getting-started/installing-cline.mdx",
            ],
        )
        self.assertEqual(
            [item["path"] for item in payload["content_batch"]["paths"]],
            selected,
        )
        protected = {
            item["path"]: item for item in payload["protected_surfaces"]["items"]
        }
        for path in ["docs/docs.json", *selected]:
            with self.subTest(path=path):
                self.assertTrue(protected[path]["protected"])
                self.assertEqual(protected[path]["default_disposition"], "retain")

    def test_missing_markdown_map_fails_closed_on_detected_navigation_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_cline_shaped_fixture(root)

            result = self._checker(root, map_path="docs/README.md")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "clean")
        self.assertEqual(payload["navigation"]["provider"], "mintlify")
        self.assertEqual(payload["navigation"]["authority"], "docs/docs.json")
        self.assertEqual(payload["findings"], [])

    def test_navigation_manifest_cannot_bypass_missing_document_map(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_cline_shaped_fixture(root)

            result = self._checker(root, map_path="docs/docs.json")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            json.loads(result.stdout)["navigation"]["authority"], "docs/docs.json"
        )

    def test_schema_json_manifest_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_cline_shaped_fixture(
                root,
                schema="https://mintlify.com/schema.json",
            )

            result = self._checker(root, map_path="docs/README.md")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            json.loads(result.stdout)["navigation"]["provider"], "mintlify"
        )

    def test_root_scope_detects_conventional_nested_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_cline_shaped_fixture(root)

            result = self._checker(
                root,
                map_path="docs/README.md",
                scope=".",
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            json.loads(result.stdout)["navigation"]["authority"], "docs/docs.json"
        )

    def test_init_adoption_measures_supported_navigation_surface(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "repo"
            root.mkdir()
            self._write_cline_shaped_fixture(root)
            subprocess.run(
                ["git", "config", "user.email", "fixture@example.invalid"],
                cwd=root,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Fixture"],
                cwd=root,
                check=True,
            )
            subprocess.run(
                ["git", "commit", "--quiet", "-m", "fixture"],
                cwd=root,
                check=True,
            )
            receipt = base / "init-receipt.json"

            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(INIT_CLOSEOUT),
                    str(root),
                    "adopt-preview",
                    "--scope",
                    "docs",
                    "--receipt-file",
                    str(receipt),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            receipt_exists = receipt.exists()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "approval-required")
        self.assertEqual(payload["handling_summary"], {"left_unchanged": 2})
        self.assertEqual(payload["writes"], 0)
        self.assertTrue(receipt_exists)

    def test_descendant_scope_inherits_manifest_for_check_and_init(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "repo"
            root.mkdir()
            self._write_cline_shaped_fixture(root)
            subprocess.run(
                ["git", "config", "user.email", "fixture@example.invalid"],
                cwd=root,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Fixture"],
                cwd=root,
                check=True,
            )
            subprocess.run(
                ["git", "commit", "--quiet", "-m", "fixture"],
                cwd=root,
                check=True,
            )
            scope = "docs/getting-started"
            checker = self._checker(
                root,
                map_path=f"{scope}/README.md",
                scope=scope,
            )
            receipt = base / "init-receipt.json"
            adoption = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(INIT_CLOSEOUT),
                    str(root),
                    "adopt-preview",
                    "--scope",
                    scope,
                    "--receipt-file",
                    str(receipt),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            receipt_exists = receipt.exists()

        self.assertEqual(checker.returncode, 0, checker.stdout + checker.stderr)
        self.assertEqual(
            json.loads(checker.stdout)["navigation"]["scope"], scope
        )
        self.assertEqual(adoption.returncode, 0, adoption.stdout + adoption.stderr)
        adoption_payload = json.loads(adoption.stdout)
        self.assertEqual(adoption_payload["status"], "approval-required")
        self.assertEqual(adoption_payload["writes"], 0)
        self.assertTrue(receipt_exists)

    def test_explicit_mdx_map_is_measured_without_executing_components(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_cline_shaped_fixture(root)

            result = self._checker(root, map_path="docs/cline-overview.mdx")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "clean")
        self.assertFalse(payload["has_findings"])
        self.assertEqual(payload["health"]["percentage"], 100)
        self.assertEqual(
            payload["health"]["categories"]["reachability"]["raw"],
            {"reachable": 2, "maintained": 2},
        )

    def test_supported_mintlify_surface_is_measured_without_root_map(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_cline_shaped_fixture(root)
            (root / "README.md").write_text("# Repository orientation\n", encoding="utf-8")
            (root / "docs" / "hidden.mdx").write_text(
                "---\ntitle: Hidden guide\n---\n\n# Hidden guide\n\nDirect URL only.\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "README.md", "docs"], cwd=root, check=True)

            result = self._checker(root, map_path="docs/README.md", scope=".")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        navigation = payload["navigation"]
        self.assertEqual(navigation["status"], "measured")
        self.assertEqual(navigation["provider"], "mintlify")
        self.assertEqual(navigation["scope"], "docs")
        self.assertEqual(navigation["authority"], "docs/docs.json")
        self.assertEqual(navigation["entry"], "docs/cline-overview.mdx")
        self.assertEqual(
            navigation["navigated_pages"],
            [
                "docs/cline-overview.mdx",
                "docs/getting-started/installing-cline.mdx",
            ],
        )
        self.assertEqual(navigation["hidden_pages"], ["docs/hidden.mdx"])
        self.assertEqual(payload["health"]["surface"], "docs")
        self.assertNotIn("README.md", navigation["navigated_pages"])

    def test_mintlify_tab_icon_metadata_is_inert(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_cline_shaped_fixture(root)
            manifest = json.loads((root / "docs" / "docs.json").read_text(encoding="utf-8"))
            manifest["navigation"]["tabs"][0]["icon"] = "book"
            (root / "docs" / "docs.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            result = self._checker(root, map_path="docs/README.md", scope="docs")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["navigation"]["status"], "measured")
        self.assertEqual(
            payload["navigation"]["navigated_pages"],
            [
                "docs/cline-overview.mdx",
                "docs/getting-started/installing-cline.mdx",
            ],
        )

    def test_mintlify_links_and_exact_redirects_resolve_provider_routes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_cline_shaped_fixture(root)
            docs = root / "docs"
            installing = docs / "getting-started" / "installing-cline.mdx"
            installing.write_text(
                installing.read_text(encoding="utf-8")
                + "\n## Install\n\nThe install step.\n",
                encoding="utf-8",
            )
            overview = docs / "cline-overview.mdx"
            overview.write_text(
                overview.read_text(encoding="utf-8")
                + "\n[Install](getting-started/installing-cline?mode=quick#install)\n"
                + "[Overview](/cline-overview?from=docs#cline-overview)\n"
                + "[Legacy](/legacy?from=docs)\n",
                encoding="utf-8",
            )
            manifest = json.loads((docs / "docs.json").read_text(encoding="utf-8"))
            manifest["redirects"] = [
                {
                    "source": "/legacy",
                    "destination": "/getting-started/installing-cline#install",
                }
            ]
            (docs / "docs.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            subprocess.run(["git", "add", "docs"], cwd=root, check=True)

            result = self._checker(root, map_path="docs/README.md", scope="docs")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(
            [item["kind"] for item in payload["findings"]],
            [],
        )
        self.assertEqual(
            payload["navigation"]["redirects"],
            [
                {
                    "source": "/legacy",
                    "destination": "/getting-started/installing-cline#install",
                }
            ],
        )

    def test_duplicate_titles_in_distinct_navigation_contexts_are_not_prioritized(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_cline_shaped_fixture(root)
            docs = root / "docs"
            (docs / "reference.mdx").write_text(
                "# Cline overview\n\nReference context.\n", encoding="utf-8"
            )
            manifest = json.loads((docs / "docs.json").read_text(encoding="utf-8"))
            manifest["navigation"]["tabs"].append(
                {"tab": "Reference", "pages": ["reference"]}
            )
            (docs / "docs.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            subprocess.run(["git", "add", "docs"], cwd=root, check=True)

            result = self._checker(root, map_path="docs/README.md", scope="docs")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertNotIn("duplicate-title", [item["kind"] for item in payload["findings"]])
        self.assertIn(
            "Reference",
            payload["navigation"]["contexts"]["docs/reference.mdx"][0]["breadcrumb"],
        )

    def test_unsupported_mintlify_features_are_unmeasured_without_fallback_score(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_cline_shaped_fixture(root)
            docs = root / "docs"
            (docs / "docs.json").write_text(
                json.dumps(
                    {
                        "$schema": "https://mintlify.com/docs.json",
                        "$ref": "./navigation.json",
                    }
                ),
                encoding="utf-8",
            )

            result = self._checker(root, map_path="docs/README.md", scope="docs")

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "unmeasured")
        self.assertFalse(payload["has_findings"])
        self.assertNotIn("health", payload)
        self.assertEqual(payload["findings"], [])
        self.assertEqual(payload["navigation"]["status"], "unmeasured")
        self.assertIn("$ref", payload["navigation"]["unsupported_features"])

    def test_unknown_navigation_fields_fail_closed_without_ignored_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_cline_shaped_fixture(root)
            docs = root / "docs"
            manifest = json.loads((docs / "docs.json").read_text(encoding="utf-8"))
            manifest["navigation"]["future-navigation"] = ["cline-overview"]
            (docs / "docs.json").write_text(json.dumps(manifest), encoding="utf-8")
            subprocess.run(["git", "add", "docs"], cwd=root, check=True)

            result = self._checker(root, map_path="docs/README.md", scope="docs")

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "unmeasured")
        self.assertIn("navigation-shape", payload["navigation"]["unsupported_features"])
        self.assertNotIn("health", payload)

    def test_empty_navigation_fails_closed_without_a_fallback_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_cline_shaped_fixture(root)
            docs = root / "docs"
            manifest = json.loads((docs / "docs.json").read_text(encoding="utf-8"))
            manifest["navigation"] = {"tabs": []}
            (docs / "docs.json").write_text(json.dumps(manifest), encoding="utf-8")
            subprocess.run(["git", "add", "docs"], cwd=root, check=True)

            result = self._checker(root, map_path="docs/README.md", scope="docs")

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "unmeasured")
        self.assertIn("empty-navigation", payload["navigation"]["unsupported_features"])
        self.assertNotIn("health", payload)

    def test_init_preview_uses_provider_entry_and_protects_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "repo"
            root.mkdir()
            self._write_cline_shaped_fixture(root)
            subprocess.run(
                ["git", "config", "user.email", "fixture@example.invalid"],
                cwd=root,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Fixture"], cwd=root, check=True
            )
            subprocess.run(["git", "add", "docs"], cwd=root, check=True)
            subprocess.run(["git", "commit", "--quiet", "-m", "fixture"], cwd=root, check=True)
            receipt = base / "init-receipt.json"

            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(INIT_CLOSEOUT),
                    str(root),
                    "adopt-preview",
                    "--scope",
                    "docs",
                    "--receipt-file",
                    str(receipt),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            receipt_payload = (
                json.loads(receipt.read_text(encoding="utf-8")) if receipt.exists() else {}
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "approval-required")
        self.assertEqual(payload["handling_summary"], {"left_unchanged": 2})
        self.assertEqual(receipt_payload["evidence"]["map_path"], "docs/cline-overview.mdx")
        self.assertEqual(
            [item["path"] for item in receipt_payload["evidence"]["dispositions"]],
            [
                "docs/cline-overview.mdx",
                "docs/getting-started/installing-cline.mdx",
            ],
        )
        self.assertNotIn(
            "docs/docs.json",
            [item["path"] for item in receipt_payload["evidence"]["dispositions"]],
        )

    def test_frontmatter_scalar_policy_is_shared_by_md_markdown_and_mdx(self):
        from _docs_checker.formats import is_document_path, parse_frontmatter_scalars

        source = "---\ntitle: \"Shared title\"\nhidden: true\n---\n\n# Shared title\n"
        for suffix in (".md", ".markdown", ".mdx"):
            with self.subTest(suffix=suffix):
                self.assertTrue(is_document_path("page" + suffix))
                parsed = parse_frontmatter_scalars(source)
                self.assertEqual(parsed["status"], "measured")
                self.assertEqual(parsed["values"], {"title": "Shared title", "hidden": True})

    def test_explicit_hidden_navigation_page_is_hidden_not_unreachable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_cline_shaped_fixture(root)
            docs = root / "docs"
            (docs / "hidden.mdx").write_text(
                "---\ntitle: Hidden guide\nhidden: true\n---\n\n# Hidden guide\n",
                encoding="utf-8",
            )
            manifest = json.loads((docs / "docs.json").read_text(encoding="utf-8"))
            manifest["navigation"]["tabs"][0]["groups"][0]["pages"].append("hidden")
            (docs / "docs.json").write_text(json.dumps(manifest), encoding="utf-8")
            subprocess.run(["git", "add", "docs"], cwd=root, check=True)

            result = self._checker(root, map_path="docs/README.md", scope="docs")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertIn("docs/hidden.mdx", payload["navigation"]["hidden_pages"])
        self.assertNotIn(
            "unreachable", [item["kind"] for item in payload["findings"]]
        )

    def test_missing_navigation_page_is_a_deterministic_provider_finding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_cline_shaped_fixture(root)
            docs = root / "docs"
            manifest = json.loads((docs / "docs.json").read_text(encoding="utf-8"))
            manifest["navigation"]["tabs"][0]["groups"][0]["pages"].append("missing")
            (docs / "docs.json").write_text(json.dumps(manifest), encoding="utf-8")
            subprocess.run(["git", "add", "docs"], cwd=root, check=True)

            result = self._checker(root, map_path="docs/README.md", scope="docs")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(
            [item for item in payload["findings"] if item["kind"] == "missing-navigation-page"],
            [
                {
                    "kind": "missing-navigation-page",
                    "path": "docs/missing",
                    "route": "missing",
                    "context": ["Docs", "Start"],
                }
            ],
        )

    def test_ambiguous_extension_match_and_redirect_cycle_fail_closed(self):
        for case in ("ambiguous", "cycle"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self._write_cline_shaped_fixture(root)
                docs = root / "docs"
                manifest = json.loads((docs / "docs.json").read_text(encoding="utf-8"))
                if case == "ambiguous":
                    for suffix in (".md", ".mdx"):
                        (docs / ("ambiguous" + suffix)).write_text(
                            "# Ambiguous\n", encoding="utf-8"
                        )
                    manifest["navigation"]["tabs"][0]["groups"][0]["pages"].append("ambiguous")
                else:
                    manifest["redirects"] = [
                        {"source": "/one", "destination": "/two"},
                        {"source": "/two", "destination": "/one"},
                    ]
                (docs / "docs.json").write_text(json.dumps(manifest), encoding="utf-8")
                subprocess.run(["git", "add", "docs"], cwd=root, check=True)

                result = self._checker(root, map_path="docs/README.md", scope="docs")
                payload = json.loads(result.stdout)

            self.assertEqual(result.returncode, 2)
            self.assertEqual(payload["status"], "unmeasured")
            self.assertNotIn("health", payload)
            self.assertEqual(payload["findings"], [])
            self.assertTrue(payload["navigation"]["unsupported_features"])

    def test_duplicate_manifest_keys_fail_closed_without_fallback_surface(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_cline_shaped_fixture(root)
            (root / "docs" / "docs.json").write_text(
                '{"$schema":"https://mintlify.com/docs.json",'
                '"$schema":"https://mintlify.com/docs.json"}',
                encoding="utf-8",
            )

            result = self._checker(root, map_path="docs/README.md", scope="docs")

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "unmeasured")
        self.assertEqual(payload["navigation"]["unsupported_features"], ["duplicate-json-key"])
        self.assertEqual(payload["findings"], [])


if __name__ == "__main__":
    unittest.main()
