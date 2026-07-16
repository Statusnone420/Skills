import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / "skills" / "docs" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from _docs_checker import discovery as docs_discovery
from _docs_checker import paths as docs_paths
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


def create_directory_link(link, target):
    if os.name != "nt":
        try:
            link.symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            raise unittest.SkipTest("directory symlinks unavailable") from exc
        return
    command = (
        "New-Item -ItemType Junction -Path "
        f"'{str(link).replace(chr(39), chr(39) * 2)}' -Target "
        f"'{str(target).replace(chr(39), chr(39) * 2)}' | Out-Null"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise unittest.SkipTest("directory junctions unavailable")


class SharedCorpusVisibilityTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows short paths only")
    def test_prevalidated_git_inventory_accepts_short_root_alias(self):
        import ctypes

        with tempfile.TemporaryDirectory() as td:
            long_root = Path(td) / "Git Repository With Long Name"
            long_root.mkdir()
            initialize_git(long_root)
            write_markdown(long_root, "docs/tracked.md", "# Tracked\n")
            run_git(long_root, "add", "--", "docs/tracked.md")

            buffer = ctypes.create_unicode_buffer(32768)
            length = ctypes.windll.kernel32.GetShortPathNameW(
                str(long_root),
                buffer,
                len(buffer),
            )
            if not length or os.path.normcase(buffer.value) == os.path.normcase(
                str(long_root)
            ):
                self.skipTest("Windows short-path aliases unavailable")

            result = docs_discovery.scan_selected_document_corpus(
                Path(buffer.value),
                "docs",
                "selected-scope-exact",
            )

            self.assertTrue(result["complete"], result)
            self.assertEqual(result["paths"], ["docs/tracked.md"])

    def test_prechecked_missing_git_marker_avoids_unbudgeted_probe(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            not_a_repository = subprocess.CompletedProcess(
                ["git"],
                1,
                stdout=b"",
                stderr=b"",
            )

            with mock.patch.object(
                docs_paths.subprocess,
                "run",
                return_value=not_a_repository,
            ), mock.patch.object(
                docs_paths.os.path,
                "lexists",
                side_effect=AssertionError("unbudgeted Git marker probe"),
            ):
                self.assertIsNone(
                    tracked_markdown_scope(
                        root,
                        ".",
                        git_marker_present=False,
                    )
                )

    def test_git_backed_init_accounts_for_every_python_metadata_probe(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            build_visibility_fixture(root)
            write_markdown(root, "README.md", "# Repository\n")
            run_git(root, "add", "--", "README.md")
            real_lstat = docs_discovery.os.lstat
            real_stat = docs_discovery.os.stat
            observed = {"lstat": 0, "stat": 0}

            def counted_lstat(path, *args, **kwargs):
                observed["lstat"] += 1
                return real_lstat(path, *args, **kwargs)

            def counted_stat(path, *args, **kwargs):
                observed["stat"] += 1
                return real_stat(path, *args, **kwargs)

            with mock.patch.object(
                docs_discovery.os,
                "lstat",
                side_effect=counted_lstat,
            ), mock.patch.object(
                docs_discovery.os,
                "stat",
                side_effect=counted_stat,
            ):
                payload = docs_discovery.discover_init_scope(root)

            self.assertNotEqual(payload["status"], "stopped")
            self.assertEqual(
                payload["observed"]["metadata_operations"],
                observed["lstat"] + observed["stat"],
            )

    def test_git_backed_init_rejects_tracked_route_through_reparse_parent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            build_visibility_fixture(root)
            original_docs = root / "original-docs"
            (root / "docs").rename(original_docs)
            outside = root / "outside"
            write_markdown(outside, "tracked.md", "# OUTSIDE_SENTINEL\n")
            create_directory_link(root / "docs", outside)

            with self.assertRaises(ValueError):
                docs_discovery.discover_init_scope(root)

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
