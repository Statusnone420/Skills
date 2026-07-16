import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / "skills" / "docs" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from _docs_checker import discovery as docs_discovery
from tests.init_journey_fixture import build_large_init_fixture


def canonical_digest(value):
    payload = (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def write_markdown(root, relative, text="# Document\n"):
    target = Path(root) / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")
    return target


def whole_file_disposition(relative):
    return {
        "item_id": f"{relative}#<whole-file>",
        "path": relative,
        "section": {"kind": "whole-file"},
        "disposition": "RETAIN",
        "reason": "Retain the complete verified document.",
        "source_digest": "sha256:" + "0" * 64,
    }


class InitV3CorpusTests(unittest.TestCase):
    def assert_classification(self, expected, callable_, *args):
        with self.assertRaises(docs_discovery.CorpusValidationError) as caught:
            callable_(*args)
        self.assertEqual(caught.exception.classification, expected)

    def test_scan_103_paths_is_complete_ordered_and_body_free(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fixture = build_large_init_fixture(root)

            body_read = AssertionError("metadata-only corpus scan opened a body")
            with (
                mock.patch.object(Path, "read_bytes", side_effect=body_read),
                mock.patch.object(Path, "read_text", side_effect=body_read),
                mock.patch("builtins.open", side_effect=body_read),
            ):
                result = docs_discovery.scan_selected_document_corpus(
                    root,
                    "docs",
                    "selected-scope-exact",
                )

            expected_paths = list(fixture.shared_paths)
            expected_corpus = {
                "coverage_version": "init-corpus-v1",
                "coverage_mode": "selected-scope-exact",
                "ordering_version": "repo-relative-casefold-v1",
                "selected_scope": "docs",
                "write_boundary": "docs",
                "path_count": 103,
                "paths_digest": canonical_digest(
                    {
                        "ordering_version": "repo-relative-casefold-v1",
                        "paths": expected_paths,
                    }
                ),
            }
            self.assertTrue(result["complete"])
            self.assertEqual(result["paths"], expected_paths)
            self.assertEqual(result["content_reads"], 0)
            self.assertEqual(result["corpus"], expected_corpus)

    def test_coverage_rejects_omission_foreign_duplicate_case_and_section_only(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            paths = ["docs/a.md", "docs/b.md", "docs/c.md"]
            for relative in paths:
                write_markdown(root, relative)
            scan = docs_discovery.scan_selected_document_corpus(
                root,
                "docs",
                "selected-scope-exact",
            )
            exact = [whole_file_disposition(path) for path in paths]
            normalized = docs_discovery.validate_corpus_coverage(scan, exact)
            self.assertEqual([item["path"] for item in normalized], paths)

            self.assert_classification(
                "incomplete-corpus",
                docs_discovery.validate_corpus_coverage,
                scan,
                exact[:-1],
            )
            self.assert_classification(
                "foreign-disposition",
                docs_discovery.validate_corpus_coverage,
                scan,
                exact + [whole_file_disposition("docs/foreign.md")],
            )
            self.assert_classification(
                "duplicate-document-disposition",
                docs_discovery.validate_corpus_coverage,
                scan,
                exact + [whole_file_disposition("DOCS/A.md")],
            )
            section_only = [dict(item) for item in exact]
            section_only[0] = {
                **section_only[0],
                "item_id": "SEC-" + "A" * 24,
                "section": {
                    "kind": "atx-section-v1",
                    "level": 1,
                    "heading_path": ["document"],
                    "occurrence": 1,
                    "start_byte": 0,
                    "end_byte": 11,
                    "raw_span_digest": "sha256:" + "0" * 64,
                },
            }
            self.assert_classification(
                "unsupported-item-granularity",
                docs_discovery.validate_corpus_coverage,
                scan,
                section_only,
            )

    def test_scan_reports_truncation_io_missing_scope_and_reparse_without_bodies(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for index in range(257):
                write_markdown(root, f"docs/{index:03d}.md", "x")
            limited = docs_discovery.scan_selected_document_corpus(
                root,
                "docs",
                "selected-scope-exact",
            )
            self.assertFalse(limited["complete"])
            self.assertEqual(
                limited["boundary"]["classification"],
                "corpus-scope-limited",
            )

        with tempfile.TemporaryDirectory() as td:
            missing = docs_discovery.scan_selected_document_corpus(
                Path(td),
                "missing",
                "selected-scope-exact",
            )
            self.assertFalse(missing["complete"])
            self.assertEqual(
                missing["boundary"]["classification"],
                "incomplete-corpus",
            )

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_markdown(root, "docs/a.md")
            with mock.patch.object(
                docs_discovery,
                "_scan_selected_scope",
                side_effect=OSError("private path detail"),
            ):
                failed = docs_discovery.scan_selected_document_corpus(
                    root,
                    "docs",
                    "selected-scope-exact",
                )
            self.assertFalse(failed["complete"])
            self.assertEqual(
                failed["boundary"],
                {
                    "classification": "incomplete-corpus",
                    "phase": "corpus-scan",
                },
            )

        if hasattr(os, "symlink"):
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                real = root / "real-docs"
                real.mkdir()
                write_markdown(real, "a.md")
                try:
                    os.symlink(real, root / "docs", target_is_directory=True)
                except OSError:
                    pass
                else:
                    unsafe = docs_discovery.scan_selected_document_corpus(
                        root,
                        "docs",
                        "selected-scope-exact",
                    )
                    self.assertFalse(unsafe["complete"])
                    self.assertEqual(
                        unsafe["boundary"]["classification"],
                        "incomplete-corpus",
                    )

    def test_root_scope_preserves_root_document_behavior(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_markdown(root, "README.md")
            write_markdown(root, "PLAN.md")
            write_markdown(root, "docs/nested.md")
            write_markdown(root, ".cache/ignored.md")

            result = docs_discovery.scan_selected_document_corpus(
                root,
                ".",
                "selected-scope-exact",
            )
            self.assertTrue(result["complete"])
            self.assertEqual(result["paths"], ["PLAN.md", "README.md"])
            self.assertEqual(result["corpus"]["write_boundary"], ".")

    def test_empty_scope_never_escalates_to_root_write_jurisdiction(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_markdown(root, "README.md")

            result = docs_discovery.scan_selected_document_corpus(
                root,
                "",
                "selected-scope-exact",
            )

            self.assertFalse(result["complete"])
            self.assertIsNone(result["corpus"])
            self.assertEqual(
                result["boundary"]["classification"],
                "incomplete-corpus",
            )

    def test_empty_adoption_binds_root_scan_and_tracked_result_without_exhaustive_claim(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            starting = docs_discovery.scan_selected_document_corpus(
                root,
                ".",
                "empty-adoption",
            )
            self.assertTrue(starting["complete"])
            self.assertEqual(starting["paths"], [])
            self.assertEqual(starting["corpus"]["coverage_mode"], "empty-adoption")
            self.assertEqual(starting["corpus"]["path_count"], 0)
            self.assertEqual(starting["corpus"]["write_boundary"], ".")

            result = docs_discovery.derive_result_corpus(
                starting,
                [{"operation": "CREATE", "path": "docs/README.md"}],
            )
            self.assertEqual(result["coverage_mode"], "empty-adoption")
            self.assertEqual(result["selected_scope"], ".")
            self.assertEqual(result["write_boundary"], ".")
            self.assertEqual(result["path_count"], 1)
            self.assertEqual(
                result["paths_digest"],
                canonical_digest(
                    {
                        "ordering_version": "repo-relative-casefold-v1",
                        "paths": ["docs/README.md"],
                    }
                ),
            )

    def test_result_corpus_is_start_minus_deletes_plus_creates(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for relative in ("docs/a.md", "docs/b.md", "docs/c.md"):
                write_markdown(root, relative)
            starting = docs_discovery.scan_selected_document_corpus(
                root,
                "docs",
                "selected-scope-exact",
            )
            result = docs_discovery.derive_result_corpus(
                starting,
                [
                    {"operation": "DELETE", "path": "docs/a.md"},
                    {"operation": "REPLACE", "path": "docs/b.md"},
                    {"operation": "CREATE", "path": "docs/d.md"},
                ],
            )
            expected_paths = ["docs/b.md", "docs/c.md", "docs/d.md"]
            self.assertEqual(result["path_count"], 3)
            self.assertEqual(
                result["paths_digest"],
                canonical_digest(
                    {
                        "ordering_version": "repo-relative-casefold-v1",
                        "paths": expected_paths,
                    }
                ),
            )


if __name__ == "__main__":
    unittest.main()
