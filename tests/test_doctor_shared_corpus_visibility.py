import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / "skills" / "docs" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import check as docs_check


def write_markdown(root, relative, text):
    target = Path(root) / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")
    return target


def run_git(root, *arguments):
    subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        text=True,
        check=True,
    )


class DoctorSharedCorpusVisibilityTests(unittest.TestCase):
    def test_doctor_never_readds_local_markdown_through_hot_or_link_routes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_git(root, "init", "--quiet")
            (root / ".gitignore").write_text(
                "docs/private/\ndocs/forced/\n",
                encoding="utf-8",
                newline="\n",
            )
            write_markdown(
                root,
                "docs/README.md",
                """# Documentation

[Shared guide](guide.md#guide)
[Private ignored](private/ignored.md#private-anchor) <!-- docs:current -->
[Forced tracked](forced/keep.md#forced) <!-- docs:current -->
""",
            )
            write_markdown(root, "docs/guide.md", "# Guide\n")
            write_markdown(
                root,
                "docs/private/ignored.md",
                "# Private Anchor\n\nPRIVATE_BODY_SENTINEL\n",
            )
            write_markdown(
                root,
                "docs/UNTRACKED_SENTINEL.md",
                "# Untracked\n\nUNTRACKED_BODY_SENTINEL\n",
            )
            write_markdown(root, "docs/forced/keep.md", "# Forced\n")
            run_git(
                root,
                "add",
                "--",
                ".gitignore",
                "docs/README.md",
                "docs/guide.md",
            )
            run_git(root, "add", "-f", "--", "docs/forced/keep.md")

            reads = []
            real_read_text = Path.read_text

            def record_read_text(path, *args, **kwargs):
                try:
                    reads.append(path.relative_to(root).as_posix())
                except ValueError:
                    pass
                return real_read_text(path, *args, **kwargs)

            with mock.patch.object(Path, "read_text", record_read_text):
                findings, hot_path, measurements = docs_check.check(
                    root,
                    map_path="docs/README.md",
                    hot_paths=[
                        "docs/private/ignored.md",
                        "docs/forced/keep.md",
                    ],
                    scope="docs",
                    _measurements=True,
                )

            self.assertNotIn("docs/private/ignored.md", reads)
            self.assertNotIn("docs/UNTRACKED_SENTINEL.md", reads)
            self.assertIn("docs/forced/keep.md", reads)
            self.assertEqual(measurements["maintained_files"], 3)
            self.assertEqual(measurements["valid_links"], 2)
            self.assertEqual(measurements["valid_anchors"], 2)
            self.assertEqual(
                {item["path"] for item in hot_path["files"]},
                {"docs/README.md", "docs/forced/keep.md"},
            )
            self.assertEqual(
                measurements["map_current_routes"],
                [{"route": "docs/forced/keep.md", "marker": "current"}],
            )
            self.assertTrue(
                any(
                    finding.get("kind") == "missing-link"
                    and finding.get("path") == "docs/README.md"
                    and finding.get("target") == "private/ignored.md"
                    for finding in findings
                )
            )

    def test_no_git_doctor_keeps_filesystem_corpus_behavior(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_markdown(
                root,
                "docs/README.md",
                """# Documentation

[Local page](local.md#local) <!-- docs:current -->
""",
            )
            write_markdown(root, "docs/local.md", "# Local\n")

            _, hot_path, measurements = docs_check.check(
                root,
                map_path="docs/README.md",
                hot_paths=["docs/local.md"],
                scope="docs",
                _measurements=True,
            )

            self.assertEqual(measurements["maintained_files"], 2)
            self.assertEqual(measurements["valid_links"], 1)
            self.assertEqual(measurements["valid_anchors"], 1)
            self.assertEqual(
                {item["path"] for item in hot_path["files"]},
                {"docs/README.md", "docs/local.md"},
            )
            self.assertEqual(
                measurements["map_current_routes"],
                [{"route": "docs/local.md", "marker": "current"}],
            )


if __name__ == "__main__":
    unittest.main()
