import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SKILL = ROOT / "skills" / "docs"
sys.path.insert(0, str(SKILL / "scripts"))
import check as docs_checker


class MdxCompatibilityTests(unittest.TestCase):
    def _write_cline_shaped_fixture(self, root):
        docs = root / "docs"
        guide = docs / "getting-started"
        guide.mkdir(parents=True)
        (docs / "docs.json").write_text(
            json.dumps(
                {
                    "$schema": "https://mintlify.com/docs.json",
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

    def _checker(self, root, *, map_path):
        return subprocess.run(
            [
                sys.executable,
                "-B",
                str(SKILL / "scripts" / "check.py"),
                str(root),
                "--json",
                "--agent",
                "--scope",
                "docs",
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
