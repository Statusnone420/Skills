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

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "error")
        self.assertFalse(payload["has_findings"])
        self.assertEqual(
            payload["error"],
            "unsupported documentation navigation manifest",
        )
        self.assertEqual(payload["findings"], [])

    def test_navigation_manifest_cannot_bypass_missing_document_map(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_cline_shaped_fixture(root)

            result = self._checker(root, map_path="docs/docs.json")

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(
            json.loads(result.stdout)["error"],
            "unsupported documentation navigation manifest",
        )

    def test_schema_json_manifest_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_cline_shaped_fixture(
                root,
                schema="https://mintlify.com/schema.json",
            )

            result = self._checker(root, map_path="docs/README.md")

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(
            json.loads(result.stdout)["error"],
            "unsupported documentation navigation manifest",
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

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(
            json.loads(result.stdout)["error"],
            "unsupported documentation navigation manifest",
        )

    def test_init_adoption_refuses_recognized_unmapped_navigation(self):
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

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "waiting")
        self.assertEqual(
            payload["classification"],
            "unsupported-documentation-navigation-manifest",
        )
        self.assertEqual(payload["writes"], 0)
        self.assertFalse(receipt_exists)

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

        self.assertEqual(checker.returncode, 2, checker.stdout + checker.stderr)
        self.assertEqual(
            json.loads(checker.stdout)["error"],
            "unsupported documentation navigation manifest",
        )
        self.assertEqual(adoption.returncode, 2, adoption.stdout + adoption.stderr)
        adoption_payload = json.loads(adoption.stdout)
        self.assertEqual(adoption_payload["status"], "waiting")
        self.assertEqual(
            adoption_payload["classification"],
            "unsupported-documentation-navigation-manifest",
        )
        self.assertEqual(adoption_payload["writes"], 0)
        self.assertFalse(receipt_exists)

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


if __name__ == "__main__":
    unittest.main()
