import base64
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / "skills" / "docs" / "scripts"
CLOSEOUT = SCRIPTS / "init_closeout.py"
sys.path.insert(0, str(SCRIPTS))

from _docs_checker import init_closeout as closeout
from _docs_checker.memory import _strict_json_loads
from tests.init_v3_fixture import (
    document_change,
    evidence_v3,
    request_v3,
    sha256_digest,
    whole_file_disposition,
)


def initialize_git(root):
    for arguments in (
        ("init", "-q"),
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


def repository_snapshot(root):
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in Path(root).rglob("*")
        if path.is_file() and ".git" not in path.relative_to(root).parts
    }


class InitV3CodecTests(unittest.TestCase):
    def assert_invalid(self, value, operation="preview"):
        with self.assertRaises(closeout.InitCloseoutError) as caught:
            closeout.validate_public_request(value, operation)
        self.assertEqual(caught.exception.status, "invalid-request")
        return caught.exception.classification

    def test_root_is_v3_only_exact_and_operation_bound(self):
        request = request_v3()
        normalized = closeout.validate_public_request(request, "preview")
        self.assertEqual(normalized, request)
        self.assertIsNot(normalized, request)

        for version in (1, 2, True, "3"):
            with self.subTest(version=version):
                invalid = copy.deepcopy(request)
                invalid["schema_version"] = version
                self.assert_invalid(invalid)

        unknown = copy.deepcopy(request)
        unknown["legacy"] = False
        self.assert_invalid(unknown)
        self.assert_invalid(request, "apply")

        apply = request_v3("apply")
        normalized_apply = closeout.validate_public_request(apply, "apply")
        self.assertEqual(normalized_apply["approval"], apply["approval"])
        bad_approval = copy.deepcopy(apply)
        bad_approval["approval"] = True
        self.assert_invalid(bad_approval, "apply")

        with self.assertRaises(ValueError):
            _strict_json_loads(
                '{"schema_version":3,"schema_version":3}',
                "init request",
            )

    def test_cli_duplicate_and_capacity_failures_use_exact_v3_envelope(self):
        with tempfile.TemporaryDirectory() as td:
            duplicate = (
                b'{"schema_version":3,"schema_version":3,"operation":"preview",'
                b'"evidence":{},"document_changes":[],"hard_delete_acceptance":null}'
            )
            process = subprocess.run(
                [sys.executable, str(CLOSEOUT), td, "preview"],
                cwd=ROOT,
                input=duplicate,
                capture_output=True,
                check=False,
            )
            self.assertEqual(process.returncode, 2)
            payload = json.loads(process.stdout)
            self.assertEqual(payload["schema_version"], 3)
            self.assertEqual(
                payload["rollback"],
                {
                    "required": False,
                    "complete": True,
                    "documents": "not-required",
                    "controls": "not-required",
                    "cleanup": "not-required",
                },
            )

            oversized = b"{" + b"x" * closeout.MAX_REQUEST_BYTES
            capacity = subprocess.run(
                [sys.executable, str(CLOSEOUT), td, "preview"],
                cwd=ROOT,
                input=oversized,
                capture_output=True,
                check=False,
            )
            self.assertEqual(capacity.returncode, 2)
            capacity_payload = json.loads(capacity.stdout)
            self.assertEqual(capacity_payload["schema_version"], 3)
            self.assertEqual(capacity_payload["classification"], "request-capacity")

    def test_v3_cli_preview_binds_corpus_and_apply_drift_is_zero_write(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "docs").mkdir()
            documents = {
                "docs/README.md": b"# Documentation\n",
                "docs/page.md": b"# Page\n",
            }
            for relative, data in documents.items():
                (root / relative).write_bytes(data)
            (root / "AGENTS.md").write_text("# Instructions\n", encoding="utf-8")
            (root / ".gitignore").write_text(".local/\n", encoding="utf-8")
            initialize_git(root)

            dispositions = [
                whole_file_disposition(relative, data)
                for relative, data in sorted(
                    documents.items(),
                    key=lambda item: (item[0].casefold(), item[0]),
                )
            ]
            preview_request = request_v3(
                evidence=evidence_v3(dispositions=dispositions)
            )
            before = repository_snapshot(root)
            preview_process = subprocess.run(
                [sys.executable, str(CLOSEOUT), str(root), "preview"],
                cwd=ROOT,
                input=(json.dumps(preview_request, separators=(",", ":")) + "\n").encode(),
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                preview_process.returncode,
                0,
                preview_process.stdout.decode("utf-8", "replace"),
            )
            preview = json.loads(preview_process.stdout)
            self.assertEqual(preview["status"], "approval-required")
            self.assertEqual(preview["corpus_transition"]["starting"]["path_count"], 2)
            self.assertEqual(preview["corpus_transition"]["starting"], preview["corpus_transition"]["result"])
            self.assertEqual(preview["document_change_count"], 0)
            self.assertEqual(repository_snapshot(root), before)

            (root / "docs" / "new.md").write_bytes(b"# Drift\n")
            completed = subprocess.run(
                ["git", "-C", str(root), "add", "--", "docs/new.md"],
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                completed.returncode,
                0,
                completed.stderr.decode("utf-8", "replace"),
            )
            apply_request = request_v3(
                "apply",
                evidence=evidence_v3(dispositions=dispositions),
                approval=preview["approval"],
            )
            before_apply = repository_snapshot(root)
            apply_process = subprocess.run(
                [sys.executable, str(CLOSEOUT), str(root), "apply"],
                cwd=ROOT,
                input=(json.dumps(apply_request, separators=(",", ":")) + "\n").encode(),
                capture_output=True,
                check=False,
            )
            self.assertEqual(apply_process.returncode, 2)
            applied = json.loads(apply_process.stdout)
            self.assertEqual(applied["status"], "stale-preview")
            self.assertEqual(applied["writes"], 0)
            self.assertEqual(applied["preview_id"], preview["preview_id"])
            self.assertEqual(
                applied["manifest_sha256"],
                preview["manifest_sha256"],
            )
            self.assertEqual(
                set(applied),
                {
                    "schema_version",
                    "status",
                    "classification",
                    "boundary",
                    "writes",
                    "partial_state",
                    "rollback",
                    "successful_event_recorded",
                    "preview_id",
                    "manifest_sha256",
                },
            )
            self.assertEqual(repository_snapshot(root), before_apply)

    def test_evidence_nested_objects_are_closed_and_exactly_typed(self):
        request = request_v3()
        request["evidence"].update(
            {
                "verified_documents": [
                    {
                        "document": "docs/README.md",
                        "digest": "sha256-text:" + "a" * 64,
                        "sources": [
                            {
                                "path": "docs/source.md",
                                "digest": "sha256-bytes:" + "b" * 64,
                            }
                        ],
                        "verified_event": "EVT-ABCDEF12",
                    }
                ],
                "protected_intent": [
                    {
                        "id": "INTENT-1",
                        "intent_key": "primary-route",
                        "source": "docs/README.md#overview",
                        "preserve": True,
                        "status": "active",
                    }
                ],
                "trust_coverage": {
                    "status": "verified",
                    "numerator": 1,
                    "denominator": 1,
                    "routes": [
                        {
                            "route": "docs/README.md",
                            "verified": True,
                            "freshness": "fresh",
                            "sources": ["state:verified-document"],
                        }
                    ],
                },
                "approvals": [{"id": "APR-1", "fingerprint": "c" * 64}],
            }
        )
        closeout.validate_public_request(request, "preview")

        mutations = (
            lambda item: item["evidence"].update(extra=False),
            lambda item: item["evidence"]["verified_documents"][0].update(extra=False),
            lambda item: item["evidence"]["verified_documents"][0]["sources"][0].update(extra=False),
            lambda item: item["evidence"]["protected_intent"][0].update(preserve=1),
            lambda item: item["evidence"]["hot_path_bytes"]["before"].update(value=True),
            lambda item: item["evidence"]["hot_path_bytes"]["after"]["provenance"][0].update(extra=False),
            lambda item: item["evidence"]["trust_coverage"]["routes"][0].update(verified=1),
            lambda item: item["evidence"]["approvals"][0].update(extra=False),
            lambda item: item["evidence"]["event"].update(extra=False),
            lambda item: item["evidence"]["source_changes"].update(agents_orientation=1),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                invalid = copy.deepcopy(request)
                mutate(invalid)
                self.assert_invalid(invalid)

    def test_disposition_and_recovery_variants_are_closed(self):
        git = {
            "kind": "git",
            "commit": "a" * 40,
            "blob": "b" * 40,
            "digest": "sha256:" + "c" * 64,
        }
        archive = {
            "kind": "archive",
            "mode": "planned",
            "path": "docs/archive/source.md",
            "digest": "sha256:" + "d" * 64,
        }
        hard_delete = {"kind": "hard-delete-request"}
        for recovery in (git, archive, hard_delete):
            normalized = closeout._normalize_recovery_v3(recovery)
            self.assertEqual(normalized, recovery)
            invalid = {**recovery, "extra": False}
            with self.assertRaises(closeout.InitCloseoutError):
                closeout._normalize_recovery_v3(invalid)

        source = b"# Source\n"
        variants = (
            whole_file_disposition(data=source),
            whole_file_disposition(
                data=source,
                disposition="MIGRATED",
                target="docs/migrated.md",
                recovery=git,
            ),
            whole_file_disposition(
                data=source,
                disposition="DEDUPLICATED",
                target="docs/canonical.md",
                target_digest="sha256:" + "e" * 64,
                recovery=git,
            ),
            whole_file_disposition(
                data=source,
                disposition="ARCHIVED",
                target="docs/archive/source.md",
                recovery=archive,
            ),
            whole_file_disposition(
                data=source,
                disposition="DISCARDED",
                recovery=hard_delete,
            ),
        )
        for item in variants:
            with self.subTest(disposition=item["disposition"]):
                self.assertEqual(
                    closeout._normalize_disposition_v3(item, "docs"),
                    item,
                )
                invalid = {**item, "extra": False}
                with self.assertRaises(closeout.InitCloseoutError):
                    closeout._normalize_disposition_v3(invalid, "docs")

        accepted = {
            "kind": "accepted-hard-delete",
            "discard_set_id": "DISCARD-" + "A" * 16,
            "acceptance_digest": "sha256:" + "f" * 64,
        }
        with self.assertRaises(closeout.InitCloseoutError):
            closeout._normalize_recovery_v3(accepted)

        unresolved = whole_file_disposition(disposition="UNRESOLVED")
        self.assertEqual(
            closeout._normalize_disposition_v3(unresolved, "docs"),
            unresolved,
        )

    def test_document_change_union_is_canonical_utf8_markdown(self):
        changes = (
            document_change(),
            document_change(
                "REPLACE",
                "docs/README.md",
                b"# Replaced\r\n",
                source_item_ids=["docs/README.md#<whole-file>"],
            ),
            document_change(
                "DELETE",
                "docs/old.md",
                source_item_ids=["docs/old.md#<whole-file>"],
            ),
        )
        for change in changes:
            with self.subTest(operation=change["operation"]):
                normalized = closeout._normalize_document_change_v3(change, "docs")
                self.assertEqual(normalized["public"], change)
                if change["operation"] in {"CREATE", "REPLACE"}:
                    self.assertEqual(
                        normalized["result_bytes"],
                        base64.b64decode(change["content_base64"]),
                    )
                else:
                    self.assertNotIn("result_bytes", normalized)

        invalid_changes = []
        whitespace = document_change()
        whitespace["content_base64"] += "\n"
        invalid_changes.append(whitespace)
        invalid_utf8 = document_change()
        invalid_utf8["content_base64"] = base64.b64encode(b"\xff").decode("ascii")
        invalid_changes.append(invalid_utf8)
        invalid_changes.append(document_change(path="docs/not-markdown.txt"))
        invalid_changes.append(document_change(path="../escape.md"))
        extra = document_change()
        extra["role"] = "caller-controlled"
        invalid_changes.append(extra)
        wrong_ids = document_change(source_item_ids=[True])
        invalid_changes.append(wrong_ids)
        for index, change in enumerate(invalid_changes):
            with self.subTest(index=index):
                with self.assertRaises(closeout.InitCloseoutError):
                    closeout._normalize_document_change_v3(change, "docs")

    def test_document_paths_reject_private_control_and_windows_ambiguous_segments(self):
        unsafe_paths = (
            "docs/.local/private.md",
            "docs/.diataxis/control.md",
            "docs/trailing./page.md",
            "docs/short~1/page.md",
            "docs/CON.md",
        )
        for path in unsafe_paths:
            with self.subTest(path=path):
                with self.assertRaises(closeout.InitCloseoutError):
                    closeout._normalize_document_change_v3(
                        document_change(path=path),
                        "docs",
                    )

    def test_capacity_boundaries_are_exact(self):
        exact_document = document_change(data=b"x" * (2 * 1024 * 1024))
        closeout._normalize_document_change_v3(exact_document, "docs")
        oversized_document = document_change(data=b"x" * (2 * 1024 * 1024 + 1))
        with self.assertRaises(closeout.InitCloseoutError) as caught:
            closeout._normalize_document_change_v3(oversized_document, "docs")
        self.assertEqual(caught.exception.classification, "capacity-exceeded")

        two_mebibytes = b"x" * (2 * 1024 * 1024)
        aggregate_maximum = [
            document_change(path="docs/one.md", data=two_mebibytes),
            document_change(path="docs/two.md", data=two_mebibytes),
        ]
        closeout.validate_public_request(
            request_v3(document_changes=aggregate_maximum),
            "preview",
        )
        self.assert_invalid(
            request_v3(
                document_changes=aggregate_maximum
                + [document_change(path="docs/three.md", data=b"x")]
            )
        )

        sixteen_ids = [f"docs/source-{index:02d}.md#<whole-file>" for index in range(16)]
        closeout._normalize_document_change_v3(
            document_change(source_item_ids=sixteen_ids),
            "docs",
        )
        with self.assertRaises(closeout.InitCloseoutError):
            closeout._normalize_document_change_v3(
                document_change(
                    source_item_ids=sixteen_ids
                    + ["docs/source-overflow.md#<whole-file>"]
                ),
                "docs",
            )

        reason_at_maximum = document_change()
        reason_at_maximum["reason"] = "x" * 512
        closeout._normalize_document_change_v3(reason_at_maximum, "docs")
        reason_over_maximum = copy.deepcopy(reason_at_maximum)
        reason_over_maximum["reason"] += "x"
        with self.assertRaises(closeout.InitCloseoutError):
            closeout._normalize_document_change_v3(reason_over_maximum, "docs")

        operations = [
            document_change(path=f"docs/new-{index:02d}.md") for index in range(64)
        ]
        closeout.validate_public_request(
            request_v3(document_changes=operations),
            "preview",
        )
        self.assert_invalid(request_v3(document_changes=operations + [document_change(path="docs/overflow.md")]))

        destructive = [
            document_change(
                "DELETE",
                f"docs/old-{index:02d}.md",
                source_item_ids=[f"docs/old-{index:02d}.md#<whole-file>"],
            )
            for index in range(32)
        ]
        closeout.validate_public_request(
            request_v3(document_changes=destructive),
            "preview",
        )
        self.assert_invalid(
            request_v3(
                document_changes=destructive
                + [
                    document_change(
                        "DELETE",
                        "docs/old-overflow.md",
                        source_item_ids=["docs/old-overflow.md#<whole-file>"],
                    )
                ]
            )
        )

        dispositions = [
            whole_file_disposition(f"docs/page-{index:03d}.md")
            for index in range(256)
        ]
        closeout.validate_public_request(
            request_v3(evidence=evidence_v3(dispositions=dispositions)),
            "preview",
        )
        self.assert_invalid(
            request_v3(
                evidence=evidence_v3(
                    dispositions=dispositions
                    + [whole_file_disposition("docs/overflow.md")]
                )
            )
        )

    def test_response_variants_have_exact_fields(self):
        corpus = {
            "coverage_version": "init-corpus-v1",
            "coverage_mode": "selected-scope-exact",
            "ordering_version": "repo-relative-casefold-v1",
            "selected_scope": "docs",
            "write_boundary": "docs",
            "path_count": 1,
            "paths_digest": "sha256:" + "1" * 64,
        }
        prepared = {
            "preview_id": "INIT-ABCDEF012345",
            "manifest_sha256": "a" * 64,
            "approval": "Approve $docs init preview INIT-ABCDEF012345 with manifest "
            + "a" * 64,
            "plan": {"transaction_id": "TXN-ABCDEF0123456789"},
            "selected_scope": "docs",
            "corpus_transition": {"starting": corpus, "result": corpus},
            "disposition_summary": {"RETAIN": 1},
            "document_change_count": 0,
            "source_receipt": {"files": 1},
        }
        preview = closeout.preview_response(prepared)
        self.assertEqual(
            set(preview),
            {
                "schema_version",
                "status",
                "writes",
                "preview_id",
                "manifest_sha256",
                "transaction_id",
                "approval",
                "selected_scope",
                "corpus_transition",
                "disposition_summary",
                "document_change_count",
                "source_files_revalidated",
                "successful_event_recorded",
            },
        )
        self.assertEqual(preview["schema_version"], 3)

        failure = closeout._failure_response(
            prepared,
            status="stale-preview",
            classification="corpus-drift",
            boundary="approval-revalidation",
        )
        self.assertEqual(
            failure["rollback"],
            {
                "required": False,
                "complete": True,
                "documents": "not-required",
                "controls": "not-required",
                "cleanup": "not-required",
            },
        )
        self.assertEqual(
            set(failure),
            {
                "schema_version",
                "status",
                "classification",
                "boundary",
                "writes",
                "partial_state",
                "rollback",
                "successful_event_recorded",
                "preview_id",
                "manifest_sha256",
            },
        )


if __name__ == "__main__":
    unittest.main()
