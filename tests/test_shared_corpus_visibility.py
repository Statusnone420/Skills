import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / "skills" / "docs" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from _docs_checker import discovery as docs_discovery
from _docs_checker.paths import tracked_markdown_scope


def write_markdown(root, relative, text="# Document\n"):
    target = Path(root) / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")
    return target


def run_git(root, *arguments):
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        text=True,
        check=True,
    )


def initialize_git(root):
    run_git(root, "init", "--quiet")


def build_visibility_fixture(root):
    initialize_git(root)
    (root / ".gitignore").write_text(
        "docs/private/\ndocs/forced/\n",
        encoding="utf-8",
        newline="\n",
    )
    write_markdown(root, "docs/tracked.md", "# Tracked\n")
    write_markdown(
        root,
        "docs/UNTRACKED_SENTINEL.md",
        "# UNTRACKED_BODY_SENTINEL\n",
    )
    write_markdown(
        root,
        "docs/private/IGNORED_SENTINEL.md",
        "# IGNORED_BODY_SENTINEL\n",
    )
    write_markdown(root, "docs/forced/tracked.md", "# Forced tracked\n")
    run_git(root, "add", "--", ".gitignore", "docs/tracked.md")
    run_git(root, "add", "-f", "--", "docs/forced/tracked.md")


class SharedCorpusVisibilityTests(unittest.TestCase):
    def test_declared_but_broken_git_repository_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".git").mkdir()
            write_markdown(root, "docs/README.md", "# Shared docs\n")

            with self.assertRaises(OSError):
                tracked_markdown_scope(root, ".")

    def test_init_corpus_uses_tracked_membership_not_ignore_appearance(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            build_visibility_fixture(root)

            result = docs_discovery.scan_selected_document_corpus(
                root,
                "docs",
                "selected-scope-exact",
            )

            self.assertTrue(result["complete"])
            self.assertEqual(
                result["paths"],
                ["docs/forced/tracked.md", "docs/tracked.md"],
            )

    def test_init_discovery_never_discloses_untracked_or_ignored_routes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            build_visibility_fixture(root)

            payload = docs_discovery.discover_init_scope(
                root,
                explicit_scope="docs",
            )
            serialized = json.dumps(payload, sort_keys=True)

            self.assertEqual(
                [item["path"] for item in payload["scope_metadata"]["paths"]],
                ["docs/forced/tracked.md", "docs/tracked.md"],
            )
            self.assertEqual(
                [item["path"] for item in payload["content_batch"]["paths"]],
                ["docs/forced/tracked.md", "docs/tracked.md"],
            )
            self.assertNotIn("UNTRACKED_SENTINEL", serialized)
            self.assertNotIn("IGNORED_SENTINEL", serialized)
            self.assertNotIn("UNTRACKED_BODY_SENTINEL", serialized)
            self.assertNotIn("IGNORED_BODY_SENTINEL", serialized)

    def test_ignored_untracked_tree_does_not_consume_init_corpus_cap(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            initialize_git(root)
            (root / ".gitignore").write_text(
                "docs/local-cache/\n",
                encoding="utf-8",
                newline="\n",
            )
            write_markdown(root, "docs/README.md", "# Shared docs\n")
            for batch in range(3):
                for index in range(100):
                    write_markdown(
                        root,
                        f"docs/local-cache/{batch}/{index:03d}.md",
                        "# Local-only cache entry\n",
                    )
            run_git(root, "add", "--", ".gitignore", "docs/README.md")

            result = docs_discovery.scan_selected_document_corpus(
                root,
                "docs",
                "selected-scope-exact",
            )

            self.assertTrue(result["complete"], result)
            self.assertEqual(result["paths"], ["docs/README.md"])


if __name__ == "__main__":
    unittest.main()
