import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / "skills" / "docs" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from _docs_checker import discovery
from _docs_checker import init_closeout as closeout
from _docs_checker import lifecycle
from _docs_checker import lifecycle_io
from _docs_checker import scan as docs_scan
from tests.init_v3_fixture import (
    document_change,
    evidence_v3,
    request_v3,
    whole_file_disposition,
)


def canonical_bytes(value):
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def digest(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def section_value(data, start, end, level, heading_path, occurrence=1):
    return {
        "kind": "atx-section-v1",
        "level": level,
        "heading_path": list(heading_path),
        "occurrence": occurrence,
        "start_byte": start,
        "end_byte": end,
        "raw_span_digest": digest(data[start:end]),
    }


def section_item_id(path, section):
    payload = canonical_bytes({"path": path, "section": section})
    return "SEC-" + hashlib.sha256(payload).hexdigest()[:24].upper()


def subordinate_disposition(
    path,
    data,
    section,
    *,
    disposition="DISCARDED",
    recovery,
    target=None,
    target_digest=None,
):
    item = {
        "item_id": section_item_id(path, section),
        "path": path,
        "section": copy.deepcopy(section),
        "disposition": disposition,
        "reason": "Approval-bound exact ATX section disposition.",
        "source_digest": digest(data),
        "recovery": copy.deepcopy(recovery),
    }
    if target is not None:
        item["target"] = target
    if target_digest is not None:
        item["target_digest"] = target_digest
    return item


def remove_sections(data, sections):
    result = data
    for section in sorted(sections, key=lambda item: item["start_byte"], reverse=True):
        result = result[: section["start_byte"]] + result[section["end_byte"] :]
    return result


def write_documents(root, values):
    for relative, data in values.items():
        target = Path(root) / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def initialize_git(root):
    for arguments in (
        ("init", "-q"),
        ("config", "core.autocrlf", "false"),
        ("config", "user.email", "fixture@example.invalid"),
        ("config", "user.name", "Fixture"),
        ("add", "."),
        ("commit", "-qm", "fixture"),
    ):
        completed = subprocess.run(
            ["git", "-C", str(root), *arguments],
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            raise AssertionError(completed.stderr.decode("utf-8", "replace"))


def git_text(root, *arguments):
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        check=True,
    ).stdout.decode("ascii").strip()


def git_recovery(root, relative, data):
    return {
        "kind": "git",
        "commit": git_text(root, "rev-parse", "HEAD"),
        "blob": git_text(root, "rev-parse", f"HEAD:{relative}"),
        "digest": digest(data),
    }


def selected_scan(root):
    return discovery.scan_selected_document_corpus(
        root,
        "docs",
        "selected-scope-exact",
    )


def tree_bytes(root):
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in Path(root).rglob("*")
        if path.is_file() and ".git" not in path.relative_to(root).parts
    }


def ordered_dispositions(dispositions):
    return sorted(
        copy.deepcopy(dispositions),
        key=lambda item: (
            item["path"].casefold(),
            item["path"],
            0 if item["section"] == {"kind": "whole-file"} else 1,
            item["item_id"],
        ),
    )


def configured_evidence(root, dispositions):
    evidence = evidence_v3(dispositions=ordered_dispositions(dispositions))
    map_path = Path(root) / "docs" / "README.md"
    map_bytes = map_path.stat().st_size
    evidence["hot_path_bytes"] = {
        point: {
            "value": map_bytes,
            "unit": "bytes",
            "provenance": [
                {
                    "route": "docs/README.md",
                    "bytes": map_bytes,
                    "source": "filesystem-stat",
                }
            ],
        }
        for point in ("before", "after")
    }
    return evidence


class InitV3SectionTests(unittest.TestCase):
    def parser(self):
        self.assertTrue(
            hasattr(docs_scan, "parse_atx_sections"),
            "Init v3 section parser is missing: _docs_checker.scan.parse_atx_sections",
        )
        return docs_scan.parse_atx_sections

    def parse(self, data):
        parser = self.parser()
        try:
            return parser(data)
        except Exception as exc:
            self.fail(
                "valid strict-UTF-8 ATX source did not parse: "
                f"{getattr(exc, 'classification', type(exc).__name__)}"
            )

    def assert_section_error(self, expected, data):
        parser = self.parser()
        with self.assertRaises(Exception) as caught:
            parser(data)
        self.assertEqual(getattr(caught.exception, "classification", None), expected)

    def derive(self, root, dispositions, changes, acceptance=None):
        try:
            return closeout.derive_document_transition_v3(
                root,
                selected_scan(root),
                dispositions,
                changes,
                acceptance,
            )
        except closeout.InitCloseoutError as exc:
            self.fail(
                "valid section transition is not implemented: "
                f"{exc.status}/{exc.classification}"
            )
        except ImportError as exc:
            self.fail(f"valid section transition is missing its parser: {exc}")

    def assert_closeout_error(
        self,
        expected,
        root,
        dispositions,
        changes=None,
        acceptance=None,
    ):
        try:
            with self.assertRaises(closeout.InitCloseoutError) as caught:
                closeout.derive_document_transition_v3(
                    root,
                    selected_scan(root),
                    dispositions,
                    [] if changes is None else changes,
                    acceptance,
                )
        except ImportError as exc:
            self.fail(f"section rejection is missing its parser: {exc}")
        self.assertEqual(caught.exception.classification, expected)

    def test_atx_grammar_leading_spaces_hash_whitespace_and_optional_closer_is_exact(self):
        data = (
            b"# One\r\n"
            b" ## Two ##\r\n"
            b"   ### Three ###   \r\n"
            b"    # Not heading\r\n"
            b"####### Too many\r\n"
            b"#No space\r\n"
            b"\t# Tab indent\r\n"
            b"###### Six\t\r\n"
            b"# Tail\r\n"
            b"tail\r\n"
        )
        tail = data.index(b"# Tail")
        expected = [
            section_value(data, 0, tail, 1, ["one"]),
            section_value(data, data.index(b" ## Two"), tail, 2, ["one", "two"]),
            section_value(
                data,
                data.index(b"   ### Three"),
                tail,
                3,
                ["one", "two", "three"],
            ),
            section_value(
                data,
                data.index(b"###### Six"),
                tail,
                6,
                ["one", "two", "three", "six"],
            ),
            section_value(data, tail, len(data), 1, ["tail"]),
        ]

        self.assertEqual(self.parse(data), expected)

    def test_backtick_tilde_fences_pseudo_headings_unclosed_and_ambiguous_fences_are_exact(self):
        valid = (
            b"# Visible\n"
            b"```python\n"
            b"# hidden in backticks\n"
            b"````\n"
            b"# After\n"
            b"~~~~\n"
            b"## hidden in tildes\n"
            b"~~~~~~\n"
            b"# Done\n"
        )
        self.assertEqual(
            [item["heading_path"][-1] for item in self.parse(valid)],
            ["visible", "after", "done"],
        )

        malformed = (
            b"# Visible\n```\n# hidden\n",
            b"# Visible\n```\n# hidden\n``` trailing\n# ambiguous\n",
            b"# Valid\ninvalid utf-8: \xff\n",
        )
        for data in malformed:
            with self.subTest(data=data):
                self.assert_section_error("malformed-section-source", data)

    def test_heading_normalization_uses_nfc_unicode_space_casefold_ancestry_and_occurrence(self):
        data = (
            "# ROOT\n"
            "## CAFÉ\u00a0Name\n"
            "first\n"
            "## CAFE\u0301\tNAME\n"
            "second\n"
            "### Straße\n"
            "child\n"
            "## CAFÉ Name\n"
            "third\n"
        ).encode("utf-8")
        sections = self.parse(data)
        equal = [item for item in sections if item["level"] == 2]

        self.assertEqual(
            [(item["heading_path"], item["occurrence"]) for item in equal],
            [
                (["root", "café name"], 1),
                (["root", "café name"], 2),
                (["root", "café name"], 3),
            ],
        )
        child = next(item for item in sections if item["level"] == 3)
        self.assertEqual(child["heading_path"], ["root", "café name", "strasse"])
        self.assertEqual(child["occurrence"], 1)

    def test_span_coordinates_are_zero_based_half_open_raw_byte_offsets(self):
        data = (
            b"# Caf\xc3\xa9\r\n"
            b"intro \xe2\x9c\x93\r\n"
            b"## Child\r\n"
            b"body\r\n"
            b"# Tail\r\n"
            b"end\r\n"
        )
        child_start = data.index(b"## Child")
        tail_start = data.index(b"# Tail")
        sections = self.parse(data)

        self.assertEqual(
            sections,
            [
                section_value(data, 0, tail_start, 1, ["café"]),
                section_value(data, child_start, tail_start, 2, ["café", "child"]),
                section_value(data, tail_start, len(data), 1, ["tail"]),
            ],
        )
        for section in sections:
            raw = data[section["start_byte"] : section["end_byte"]]
            self.assertEqual(digest(raw), section["raw_span_digest"])
        self.assertEqual(data[child_start:tail_start], b"## Child\r\nbody\r\n")

    def test_section_schema_item_id_duplicates_stale_spans_overlap_and_offsets_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = "docs/README.md"
            data = b"# Root\n## Repeat\nA\n## Repeat\nB\n## Tail\nC\n"
            write_documents(root, {path: data})
            initialize_git(root)
            recovery = git_recovery(root, path, data)
            second = data.index(b"## Repeat", data.index(b"## Repeat") + 1)
            tail = data.index(b"## Tail")
            first_section = section_value(
                data,
                data.index(b"## Repeat"),
                second,
                2,
                ["root", "repeat"],
                1,
            )
            second_section = section_value(
                data,
                second,
                tail,
                2,
                ["root", "repeat"],
                2,
            )
            root_section = section_value(data, 0, len(data), 1, ["root"], 1)
            base = whole_file_disposition(path, data)
            first = subordinate_disposition(
                path,
                data,
                first_section,
                recovery=recovery,
            )
            second_item = subordinate_disposition(
                path,
                data,
                second_section,
                recovery=recovery,
            )

            ambiguous_section = copy.deepcopy(second_section)
            ambiguous_section["occurrence"] = 1
            ambiguous = subordinate_disposition(
                path,
                data,
                ambiguous_section,
                recovery=recovery,
            )
            stale_section = copy.deepcopy(first_section)
            stale_section["raw_span_digest"] = "sha256:" + "0" * 64
            stale = subordinate_disposition(
                path,
                data,
                stale_section,
                recovery=recovery,
            )
            invalid_section = copy.deepcopy(first_section)
            invalid_section["end_byte"] = invalid_section["start_byte"]
            invalid_section["raw_span_digest"] = digest(b"")
            invalid = subordinate_disposition(
                path,
                data,
                invalid_section,
                recovery=recovery,
            )
            extra_section = copy.deepcopy(first_section)
            extra_section["slug"] = "repeat"
            extra = subordinate_disposition(
                path,
                data,
                extra_section,
                recovery=recovery,
            )
            ancestor = subordinate_disposition(
                path,
                data,
                root_section,
                recovery=recovery,
            )
            cases = (
                ("duplicate-section-item-id", [base, first, copy.deepcopy(first)]),
                ("ambiguous-section-identity", [base, ambiguous]),
                ("stale-section-span", [base, stale]),
                ("overlapping-section-spans", [base, ancestor, first]),
                ("invalid-section-offsets", [base, invalid]),
                ("invalid-section-fields", [base, extra]),
            )
            for expected, dispositions in cases:
                with self.subTest(expected=expected):
                    self.assert_closeout_error(expected, root, dispositions)

            self.assertNotEqual(first["item_id"], second_item["item_id"])

    def test_section_paths_require_whole_file_retain_base_and_emit_no_section_retain(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = "docs/README.md"
            data = b"# Root\n## Remove\nbody\n"
            write_documents(root, {path: data})
            initialize_git(root)
            recovery = git_recovery(root, path, data)
            selected = section_value(
                data,
                data.index(b"## Remove"),
                len(data),
                2,
                ["root", "remove"],
            )
            section = subordinate_disposition(
                path,
                data,
                selected,
                recovery=recovery,
            )
            nonretain_base = whole_file_disposition(
                path,
                data,
                disposition="DISCARDED",
                recovery=recovery,
            )
            retained_section = subordinate_disposition(
                path,
                data,
                selected,
                disposition="RETAIN",
                recovery=recovery,
            )

            self.assert_closeout_error("section-base-required", root, [section])
            self.assert_closeout_error(
                "section-base-not-retained",
                root,
                [nonretain_base, section],
            )
            self.assert_closeout_error(
                "section-retain-forbidden",
                root,
                [whole_file_disposition(path, data), retained_section],
            )

    def test_subordinate_variants_are_exact_and_repeat_one_full_file_recovery_object(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = "docs/README.md"
            target = "docs/canonical.md"
            data = (
                b"# Root\n"
                b"## Migrate\nM\n"
                b"## Deduplicate\nD\n"
                b"## Archive\nA\n"
                b"## Discard\nX\n"
            )
            canonical = b"# Canonical\nD\n"
            write_documents(root, {path: data, target: canonical})
            initialize_git(root)
            recovery = git_recovery(root, path, data)
            markers = [
                (b"## Migrate", "migrate"),
                (b"## Deduplicate", "deduplicate"),
                (b"## Archive", "archive"),
                (b"## Discard", "discard"),
            ]
            sections = []
            for index, (marker, name) in enumerate(markers):
                start = data.index(marker)
                end = data.index(markers[index + 1][0]) if index + 1 < len(markers) else len(data)
                sections.append(section_value(data, start, end, 2, ["root", name]))
            items = [
                subordinate_disposition(
                    path,
                    data,
                    sections[0],
                    disposition="MIGRATED",
                    recovery=recovery,
                    target="docs/migrated.md",
                ),
                subordinate_disposition(
                    path,
                    data,
                    sections[1],
                    disposition="DEDUPLICATED",
                    recovery=recovery,
                    target=target,
                    target_digest=digest(canonical),
                ),
                subordinate_disposition(
                    path,
                    data,
                    sections[2],
                    disposition="ARCHIVED",
                    recovery=recovery,
                    target="docs/archive/section.md",
                ),
                subordinate_disposition(
                    path,
                    data,
                    sections[3],
                    recovery=recovery,
                ),
            ]
            result = remove_sections(data, sections)
            changes = [
                document_change(
                    "REPLACE",
                    path,
                    result,
                    source_item_ids=sorted(item["item_id"] for item in items),
                ),
                document_change(
                    "CREATE",
                    "docs/archive/section.md",
                    data[sections[2]["start_byte"] : sections[2]["end_byte"]],
                    source_item_ids=[items[2]["item_id"]],
                ),
                document_change(
                    "CREATE",
                    "docs/migrated.md",
                    b"# Migrated\nPreserved meaning.\n",
                    source_item_ids=[items[0]["item_id"]],
                ),
            ]
            dispositions = [
                whole_file_disposition(path, data),
                *items,
                whole_file_disposition(target, canonical),
            ]
            evidence = evidence_v3(
                dispositions=ordered_dispositions(dispositions)
            )
            validated = closeout.validate_public_request(
                request_v3(evidence=evidence, document_changes=changes),
                "preview",
            )

            expected_fields = {
                "MIGRATED": {
                    "item_id", "path", "section", "disposition", "reason",
                    "source_digest", "recovery", "target",
                },
                "DEDUPLICATED": {
                    "item_id", "path", "section", "disposition", "reason",
                    "source_digest", "recovery", "target", "target_digest",
                },
                "ARCHIVED": {
                    "item_id", "path", "section", "disposition", "reason",
                    "source_digest", "recovery", "target",
                },
                "DISCARDED": {
                    "item_id", "path", "section", "disposition", "reason",
                    "source_digest", "recovery",
                },
            }
            section_items = [
                item
                for item in validated["evidence"]["dispositions"]
                if item["section"]["kind"] == "atx-section-v1"
            ]
            for item in section_items:
                self.assertEqual(set(item), expected_fields[item["disposition"]])
                self.assertEqual(item["recovery"], recovery)
                self.assertNotIn("content_base64", item)
                self.assertNotIn("result_digest", item)
            self.assertTrue(
                all(
                    "content_base64" in change
                    for change in validated["document_changes"]
                    if change["operation"] in {"CREATE", "REPLACE"}
                )
            )

            mismatched = copy.deepcopy(dispositions)
            mismatched[2]["recovery"]["digest"] = digest(b"not the source")
            self.assert_closeout_error(
                "recovery-mismatch",
                root,
                mismatched,
                changes,
            )

            for index, extra in ((1, "target_digest"), (4, "target")):
                invalid = copy.deepcopy(dispositions)
                invalid[index][extra] = digest(canonical) if extra.endswith("digest") else target
                with self.subTest(extra=extra), self.assertRaises(
                    closeout.InitCloseoutError
                ) as caught:
                    closeout.validate_public_request(
                        request_v3(
                            evidence=evidence_v3(
                                dispositions=ordered_dispositions(invalid)
                            )
                        ),
                        "preview",
                    )
                self.assertEqual(caught.exception.classification, "invalid-disposition-fields")

    def test_section_hard_delete_recovery_is_forbidden_and_no_git_requires_archive(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = "docs/README.md"
            data = b"# Root\n## Discard\nbody\n"
            write_documents(root, {path: data})
            selected = section_value(
                data,
                data.index(b"## Discard"),
                len(data),
                2,
                ["root", "discard"],
            )
            base = whole_file_disposition(path, data)
            result = remove_sections(data, [selected])
            archive_recovery = {
                "kind": "archive",
                "mode": "planned",
                "path": "docs/recovery/README.md",
                "digest": digest(data),
            }
            archived = subordinate_disposition(
                path,
                data,
                selected,
                recovery=archive_recovery,
            )
            changes = [
                document_change(
                    "CREATE",
                    archive_recovery["path"],
                    data,
                    source_item_ids=[archived["item_id"]],
                ),
                document_change(
                    "REPLACE",
                    path,
                    result,
                    source_item_ids=[archived["item_id"]],
                ),
            ]
            transition = self.derive(root, [base, archived], changes)
            archive = next(
                item
                for item in transition["operations"]
                if item["path"] == archive_recovery["path"]
            )
            self.assertEqual(archive["result_bytes"], data)

            forbidden_recoveries = (
                {"kind": "hard-delete-request"},
                {
                    "kind": "accepted-hard-delete",
                    "discard_set_id": "DISCARD-" + "A" * 16,
                    "acceptance_digest": digest(b"acceptance"),
                },
            )
            for recovery in forbidden_recoveries:
                item = subordinate_disposition(
                    path,
                    data,
                    selected,
                    recovery=recovery,
                )
                with self.subTest(recovery=recovery["kind"]):
                    self.assert_closeout_error(
                        "section-hard-delete-forbidden",
                        root,
                        [base, item],
                    )

    def test_all_source_removals_aggregate_one_replace_using_reverse_span_order_only(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = "docs/README.md"
            data = (
                b"# Root\r\n"
                b"intro\r\n"
                b"## Remove A\r\nA1\r\n"
                b"## Keep\r\nK\r\n"
                b"## Remove B\r\nB1\r\n"
            )
            write_documents(root, {path: data})
            initialize_git(root)
            recovery = git_recovery(root, path, data)
            keep = data.index(b"## Keep")
            remove_b = data.index(b"## Remove B")
            sections = [
                section_value(
                    data,
                    data.index(b"## Remove A"),
                    keep,
                    2,
                    ["root", "remove a"],
                ),
                section_value(
                    data,
                    remove_b,
                    len(data),
                    2,
                    ["root", "remove b"],
                ),
            ]
            items = [
                subordinate_disposition(
                    path,
                    data,
                    section,
                    recovery=recovery,
                )
                for section in sections
            ]
            expected = remove_sections(data, sections)
            transition = self.derive(
                root,
                [whole_file_disposition(path, data), *items],
                [
                    document_change(
                        "REPLACE",
                        path,
                        expected,
                        source_item_ids=sorted(item["item_id"] for item in items),
                    )
                ],
            )

            self.assertEqual(len(transition["operations"]), 1)
            operation = transition["operations"][0]
            self.assertEqual(operation["operation"], "REPLACE")
            self.assertEqual(operation["result_bytes"], expected)
            self.assertEqual(operation["source_item_ids"], sorted(item["item_id"] for item in items))
            self.assertEqual(expected, b"# Root\r\nintro\r\n## Keep\r\nK\r\n")
            self.assertNotIn(b"\n", expected.replace(b"\r\n", b""))

            altered = expected.replace(b"K\r\n", b"changed\r\n")
            self.assert_closeout_error(
                "section-result-mismatch",
                root,
                [whole_file_disposition(path, data), *items],
                [
                    document_change(
                        "REPLACE",
                        path,
                        altered,
                        source_item_ids=sorted(item["item_id"] for item in items),
                    )
                ],
            )

    def test_migrated_archived_targets_are_absent_unique_creates_and_dedup_target_is_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = "docs/README.md"
            data = b"# Root\n## Move\nmove body\n"
            write_documents(root, {path: data})
            initialize_git(root)
            selected = section_value(
                data,
                data.index(b"## Move"),
                len(data),
                2,
                ["root", "move"],
            )
            recovery = git_recovery(root, path, data)
            item = subordinate_disposition(
                path,
                data,
                selected,
                disposition="MIGRATED",
                recovery=recovery,
                target="docs/moved.md",
            )
            target_bytes = b"# Moved\nPreserved meaning.\n"
            transition = self.derive(
                root,
                [whole_file_disposition(path, data), item],
                [
                    document_change(
                        "REPLACE",
                        path,
                        remove_sections(data, [selected]),
                        source_item_ids=[item["item_id"]],
                    ),
                    document_change(
                        "CREATE",
                        item["target"],
                        target_bytes,
                        source_item_ids=[item["item_id"]],
                    ),
                ],
            )
            create = next(op for op in transition["operations"] if op["operation"] == "CREATE")
            self.assertEqual(create["path"], item["target"])
            self.assertEqual(create["result_bytes"], target_bytes)
            self.assertFalse((root / item["target"]).exists())

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = "docs/README.md"
            data = b"# Root\n## Archive\narchive body\n"
            write_documents(root, {path: data})
            initialize_git(root)
            selected = section_value(
                data,
                data.index(b"## Archive"),
                len(data),
                2,
                ["root", "archive"],
            )
            recovery = git_recovery(root, path, data)
            item = subordinate_disposition(
                path,
                data,
                selected,
                disposition="ARCHIVED",
                recovery=recovery,
                target="docs/archive/section.md",
            )
            raw_span = data[selected["start_byte"] : selected["end_byte"]]
            transition = self.derive(
                root,
                [whole_file_disposition(path, data), item],
                [
                    document_change(
                        "REPLACE",
                        path,
                        remove_sections(data, [selected]),
                        source_item_ids=[item["item_id"]],
                    ),
                    document_change(
                        "CREATE",
                        item["target"],
                        raw_span,
                        source_item_ids=[item["item_id"]],
                    ),
                ],
            )
            create = next(op for op in transition["operations"] if op["operation"] == "CREATE")
            self.assertEqual(create["result_bytes"], raw_span)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = "docs/README.md"
            target = "docs/canonical.md"
            data = b"# Root\n## Duplicate\nduplicate meaning\n"
            canonical = b"# Canonical\nverified meaning\n"
            write_documents(root, {path: data, target: canonical})
            initialize_git(root)
            selected = section_value(
                data,
                data.index(b"## Duplicate"),
                len(data),
                2,
                ["root", "duplicate"],
            )
            item = subordinate_disposition(
                path,
                data,
                selected,
                disposition="DEDUPLICATED",
                recovery=git_recovery(root, path, data),
                target=target,
                target_digest=digest(canonical),
            )
            transition = self.derive(
                root,
                [
                    whole_file_disposition(path, data),
                    item,
                    whole_file_disposition(target, canonical),
                ],
                [
                    document_change(
                        "REPLACE",
                        path,
                        remove_sections(data, [selected]),
                        source_item_ids=[item["item_id"]],
                    )
                ],
            )
            self.assertFalse(any(op["path"] == target for op in transition["operations"]))
            self.assertEqual((root / target).read_bytes(), canonical)

            scan = selected_scan(root)
            corpus_transition = {
                "starting": scan["corpus"],
                "result": discovery.derive_result_corpus(
                    scan,
                    transition["operations"],
                ),
            }
            manifest_a = lifecycle.prepare_dispositions(
                None,
                [
                    whole_file_disposition(path, data),
                    item,
                    whole_file_disposition(target, canonical),
                ],
                removed_items=[item["item_id"]],
                git_available=True,
                command="init",
                approval_bindings=[],
                corpus_transition=corpus_transition,
                document_results=transition["document_results"],
            )
            changed_dispositions = copy.deepcopy(manifest_a["dispositions"])
            changed_item = next(
                disposition
                for disposition in changed_dispositions
                if disposition["item_id"] == item["item_id"]
            )
            changed_item["target_digest"] = digest(b"changed canonical target")
            manifest_b = lifecycle.prepare_dispositions(
                None,
                changed_dispositions,
                removed_items=[item["item_id"]],
                git_available=True,
                command="init",
                approval_bindings=[],
                corpus_transition=corpus_transition,
                document_results=transition["document_results"],
            )
            self.assertNotEqual(
                manifest_a["manifest_identity"],
                manifest_b["manifest_identity"],
            )

    def test_discard_and_every_aggregate_source_replace_have_full_file_recovery(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = "docs/README.md"
            data = b"# Root\n## Discard\ndiscard body\n"
            write_documents(root, {path: data})
            initialize_git(root)
            selected = section_value(
                data,
                data.index(b"## Discard"),
                len(data),
                2,
                ["root", "discard"],
            )
            item = subordinate_disposition(
                path,
                data,
                selected,
                recovery=git_recovery(root, path, data),
            )
            result = remove_sections(data, [selected])
            transition = self.derive(
                root,
                [whole_file_disposition(path, data), item],
                [
                    document_change(
                        "REPLACE",
                        path,
                        result,
                        source_item_ids=[item["item_id"]],
                    )
                ],
            )
            operation = transition["operations"][0]
            self.assertEqual(operation["starting_digest"], digest(data))
            self.assertEqual(operation["result_digest"], digest(result))
            self.assertRegex(operation["recovery_binding"], r"^sha256:[0-9a-f]{64}$")

            incomplete = copy.deepcopy(item)
            incomplete["recovery"]["digest"] = selected["raw_span_digest"]
            self.assert_closeout_error(
                "recovery-mismatch",
                root,
                [whole_file_disposition(path, data), incomplete],
            )

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = "docs/README.md"
            data = b"# Documentation\n\n## Move\nMove body.\n"
            write_documents(
                root,
                {
                    path: data,
                    "AGENTS.md": b"# Repository agents\n",
                },
            )
            initialize_git(root)
            selected = section_value(
                data,
                data.index(b"## Move"),
                len(data),
                2,
                ["documentation", "move"],
            )
            item = subordinate_disposition(
                path,
                data,
                selected,
                disposition="MIGRATED",
                recovery=git_recovery(root, path, data),
                target="docs/moved.md",
            )
            dispositions = [whole_file_disposition(path, data), item]
            target_a = b"# Moved\nApproved result A.\n"
            changes_a = [
                document_change(
                    "REPLACE",
                    path,
                    remove_sections(data, [selected]),
                    source_item_ids=[item["item_id"]],
                ),
                document_change(
                    "CREATE",
                    item["target"],
                    target_a,
                    source_item_ids=[item["item_id"]],
                ),
            ]
            evidence = configured_evidence(root, dispositions)
            retained_source = remove_sections(data, [selected])
            evidence["hot_path_bytes"]["after"] = {
                "value": len(retained_source),
                "unit": "bytes",
                "provenance": [
                    {
                        "route": path,
                        "bytes": len(retained_source),
                        "source": "filesystem-stat",
                    }
                ],
            }
            try:
                prepared = closeout.prepare_initialization_closeout(
                    root,
                    request_v3(evidence=evidence, document_changes=changes_a),
                )
            except closeout.InitCloseoutError as exc:
                self.fail(
                    "section closeout did not join v3 preparation: "
                    f"{exc.status}/{exc.classification}"
                )
            plan = prepared["plan"]
            document_operations = {
                operation["path"]: operation for operation in plan["document_operations"]
            }
            self.assertEqual(document_operations[item["target"]]["result_digest"], digest(target_a))
            self.assertEqual(document_operations[path]["result_digest"], digest(remove_sections(data, [selected])))
            journal = plan["journal_models"]["prepared"]
            self.assertEqual(
                {
                    entry["path"]
                    for entry in journal["entries"]
                    if entry["plane"] == "document"
                },
                {path, item["target"]},
            )
            persisted = b"".join(
                content
                for relative, content in plan["targets"].items()
                if relative.startswith(".diataxis/")
            ) + b"".join(plan["journal_bytes"].values())
            persisted += canonical_bytes(
                {"dispositions": prepared["dispositions"]}
            )
            self.assertNotIn(data, persisted)
            self.assertNotIn(target_a, persisted)
            self.assertNotIn(b"content_base64", persisted)
            self.assertNotIn(b"result_bytes", persisted)

            target_b = b"# Moved\nApproved result B.\n"
            changed_result = closeout.prepare_initialization_closeout(
                root,
                request_v3(
                    "apply",
                    evidence=evidence,
                    document_changes=[
                        changes_a[0],
                        document_change(
                            "CREATE",
                            item["target"],
                            target_b,
                            source_item_ids=[item["item_id"]],
                        ),
                    ],
                    approval=prepared["approval"],
                ),
            )
            before_stale = tree_bytes(root)
            stale = closeout.apply_response(root, changed_result, prepared["approval"])
            self.assertEqual(stale["status"], "stale-preview")
            self.assertEqual(stale["writes"], 0)
            self.assertEqual(tree_bytes(root), before_stale)

            before_rollback = tree_bytes(root)
            with mock.patch.object(
                lifecycle_io,
                "_verify_pre_event_v3",
                side_effect=OSError("forced section verification failure"),
            ):
                failed = lifecycle_io.apply_verified_closeout(
                    root,
                    plan,
                    approved_transaction=plan["transaction_id"],
                    verification=lambda: True,
                )
            self.assertEqual(failed["status"], "closeout-failed")
            self.assertTrue(failed["rollback"]["complete"])
            self.assertEqual(tree_bytes(root), before_rollback)
            self.assertFalse((root / ".diataxis").exists())

            prepared_recovery = lifecycle_io._prepare_recovery_area_v3(root, plan)
            recovery_root = (
                root
                / ".diataxis"
                / "recovery"
                / plan["transaction_id"]
            )
            clean_preview = lifecycle_io.preview_state_conflict_recovery(root)
            self.assertEqual(clean_preview["status"], "approval-required")
            self.assertEqual(clean_preview["action"], "rollback")

            interrupted = copy.deepcopy(prepared_recovery["journal"])
            interrupted["phase"] = "installing"
            source_entry = next(
                entry
                for entry in interrupted["entries"]
                if entry["path"] == path
            )
            self.assertEqual(source_entry["operation"], "REPLACE")
            self.assertEqual(source_entry["role"], "document-source")
            (root / path).write_bytes(
                (recovery_root / source_entry["result"]["staged"]).read_bytes()
            )
            source_entry["status"] = "installed"
            (recovery_root / "journal.json").write_bytes(
                canonical_bytes(interrupted)
            )

            interrupted_preview = lifecycle_io.preview_state_conflict_recovery(root)
            self.assertEqual(interrupted_preview["status"], "approval-required")
            self.assertEqual(interrupted_preview["action"], "rollback")
            recovered = lifecycle_io.apply_state_conflict_recovery(
                root,
                interrupted_preview,
                approved_preview=interrupted_preview["approval"],
                verification=None,
            )
            self.assertEqual(recovered["status"], "recovered")
            self.assertEqual((root / path).read_bytes(), data)
            self.assertFalse((root / ".diataxis").exists())

            manifest_path = next(
                path
                for path, role in plan["target_roles"].items()
                if role == "manifest"
            )
            manifest_payload = json.loads(plan["targets"][manifest_path])
            document_result = next(
                result
                for result in manifest_payload["document_results"]
                if result["path"] == item["target"]
            )
            manifest_a = lifecycle.prepare_dispositions(
                None,
                prepared["dispositions"],
                removed_items=[item["item_id"]],
                git_available=True,
                command="init",
                approval_bindings=[],
                corpus_transition=prepared["corpus_transition"],
                document_results=manifest_payload["document_results"],
            )
            changed_document_results = copy.deepcopy(
                manifest_payload["document_results"]
            )
            changed_document_result = next(
                result
                for result in changed_document_results
                if result["path"] == item["target"]
            )
            changed_document_result["result_digest"] = digest(target_b)
            manifest_b = lifecycle.prepare_dispositions(
                None,
                prepared["dispositions"],
                removed_items=[item["item_id"]],
                git_available=True,
                command="init",
                approval_bindings=[],
                corpus_transition=prepared["corpus_transition"],
                document_results=changed_document_results,
            )
            self.assertEqual(document_result["result_digest"], digest(target_a))
            self.assertNotEqual(manifest_a["manifest_identity"], manifest_b["manifest_identity"])


if __name__ == "__main__":
    unittest.main()
