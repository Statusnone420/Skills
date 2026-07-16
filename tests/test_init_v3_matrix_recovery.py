import copy
import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / "skills" / "docs" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from _docs_checker import discovery
from _docs_checker import init_closeout as closeout
from tests.init_v3_fixture import (
    document_change,
    empty_adoption_evidence_v3,
    request_v3,
    whole_file_disposition,
)


def write_documents(root, values):
    for relative, data in values.items():
        target = Path(root) / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


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
        "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
    }


def scan(root):
    return discovery.scan_selected_document_corpus(
        root,
        "docs",
        "selected-scope-exact",
    )


class InitV3MatrixRecoveryTests(unittest.TestCase):
    def derive(self, root, dispositions, changes, acceptance=None):
        return closeout.derive_document_transition_v3(
            root,
            scan(root),
            dispositions,
            changes,
            acceptance,
        )

    def test_retain_authorizes_no_bytes_and_unresolved_requires_user_action(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = b"# Retained\n"
            write_documents(root, {"docs/README.md": data})
            retained = whole_file_disposition("docs/README.md", data)
            transition = self.derive(root, [retained], [])
            self.assertEqual(transition["document_results"], [])
            self.assertEqual(transition["operations"], [])
            self.assertEqual(transition["changed_paths"], [])

            unresolved = {**retained, "disposition": "UNRESOLVED"}
            with self.assertRaises(closeout.InitCloseoutError) as caught:
                self.derive(root, [unresolved], [])
            self.assertEqual(caught.exception.status, "requires-user-action")
            self.assertEqual(caught.exception.classification, "unresolved-disposition")

    def test_empty_adoption_create_requires_the_checker_derived_map_path(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            starting = discovery.scan_selected_document_corpus(
                root,
                ".",
                "empty-adoption",
            )
            change = document_change(
                "CREATE",
                "README.md",
                b"# Adopted documentation\n",
                source_item_ids=[],
            )

            with self.assertRaises(closeout.InitCloseoutError) as caught:
                closeout.derive_document_transition_v3(root, starting, [], [change])
            self.assertEqual(caught.exception.classification, "orphan-document-operation")

            wrong_path = copy.deepcopy(starting)
            wrong_path["empty_adoption_path"] = "docs/README.md"
            with self.assertRaises(closeout.InitCloseoutError) as caught:
                closeout.derive_document_transition_v3(
                    root,
                    wrong_path,
                    [],
                    [change],
                )
            self.assertEqual(caught.exception.classification, "orphan-document-operation")

            authorized = copy.deepcopy(starting)
            authorized["empty_adoption_path"] = "README.md"
            try:
                transition = closeout.derive_document_transition_v3(
                    root,
                    authorized,
                    [],
                    [change],
                )
            except closeout.InitCloseoutError as exc:
                transition = exc.classification
            self.assertIsInstance(transition, dict)
            self.assertEqual(
                transition["document_results"],
                [
                    {
                        "path": "README.md",
                        "operation": "CREATE",
                        "role": "document-result",
                        "starting_digest": "sha256:ABSENT",
                        "result_digest": "sha256:"
                        + hashlib.sha256(b"# Adopted documentation\n").hexdigest(),
                        "bytes": len(b"# Adopted documentation\n"),
                        "source_item_ids": [],
                    }
                ],
            )

    def test_empty_adoption_request_accepts_the_root_scope(self):
        request = request_v3(
            evidence=empty_adoption_evidence_v3(),
            document_changes=[
                document_change(
                    "CREATE",
                    "README.md",
                    b"# Adopted documentation\n",
                    source_item_ids=[],
                )
            ],
        )
        try:
            validated = closeout.validate_public_request(request, "preview")
        except closeout.InitCloseoutError as exc:
            validated = exc.classification
        self.assertIsInstance(validated, dict)
        self.assertEqual(validated["evidence"]["selected_scope"], ".")

    def test_empty_document_source_is_read_once_per_transition(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = b""
            write_documents(root, {"docs/empty.md": source})
            initialize_git(root)
            item = whole_file_disposition(
                "docs/empty.md",
                source,
                disposition="MIGRATED",
                target="docs/empty.md",
                recovery=git_recovery(root, "docs/empty.md", source),
            )
            change = document_change(
                "REPLACE",
                "docs/empty.md",
                b"# Filled\n",
                source_item_ids=[item["item_id"]],
            )

            with mock.patch.object(
                closeout,
                "_read_document_bytes_v3",
                wraps=closeout._read_document_bytes_v3,
            ) as read_document:
                transition = self.derive(root, [item], [change])

            self.assertEqual(read_document.call_count, 1)
            self.assertEqual(transition["operations"][0]["operation"], "REPLACE")

    def test_migration_group_derives_create_and_source_deletes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source_a = b"# A\n"
            source_b = b"# B\n"
            write_documents(
                root,
                {"docs/a.md": source_a, "docs/b.md": source_b},
            )
            initialize_git(root)
            item_a = whole_file_disposition(
                "docs/a.md",
                source_a,
                disposition="MIGRATED",
                target="docs/merged.md",
                recovery=git_recovery(root, "docs/a.md", source_a),
            )
            item_b = whole_file_disposition(
                "docs/b.md",
                source_b,
                disposition="MIGRATED",
                target="docs/merged.md",
                recovery=git_recovery(root, "docs/b.md", source_b),
            )
            ids = sorted([item_a["item_id"], item_b["item_id"]])
            result = b"# Merged\n"
            changes = [
                document_change(
                    "DELETE",
                    "docs/a.md",
                    source_item_ids=[item_a["item_id"]],
                ),
                document_change(
                    "DELETE",
                    "docs/b.md",
                    source_item_ids=[item_b["item_id"]],
                ),
                document_change(
                    "CREATE",
                    "docs/merged.md",
                    result,
                    source_item_ids=ids,
                ),
            ]
            transition = self.derive(root, [item_a, item_b], changes)
            self.assertEqual(
                [item["path"] for item in transition["document_results"]],
                ["docs/a.md", "docs/b.md", "docs/merged.md"],
            )
            self.assertEqual(
                [item["operation"] for item in transition["document_results"]],
                ["DELETE", "DELETE", "CREATE"],
            )
            self.assertEqual(transition["operations"][-1]["result_bytes"], result)

    def test_dedup_target_is_unchanged_and_archive_is_byte_exact(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            duplicate = b"# Duplicate\n"
            canonical = b""
            write_documents(
                root,
                {
                    "docs/canonical.md": canonical,
                    "docs/duplicate.md": duplicate,
                },
            )
            initialize_git(root)
            canonical_item = whole_file_disposition("docs/canonical.md", canonical)
            duplicate_item = whole_file_disposition(
                "docs/duplicate.md",
                duplicate,
                disposition="DEDUPLICATED",
                target="docs/canonical.md",
                target_digest="sha256:" + hashlib.sha256(canonical).hexdigest(),
                recovery=git_recovery(root, "docs/duplicate.md", duplicate),
            )
            with mock.patch.object(
                closeout,
                "_read_document_bytes_v3",
                wraps=closeout._read_document_bytes_v3,
            ) as read_document:
                transition = self.derive(
                    root,
                    [canonical_item, duplicate_item],
                    [
                        document_change(
                            "DELETE",
                            "docs/duplicate.md",
                            source_item_ids=[duplicate_item["item_id"]],
                        )
                    ],
                )
            self.assertEqual(read_document.call_count, 2)
            self.assertEqual(len(transition["document_results"]), 1)
            self.assertEqual(transition["document_results"][0]["path"], "docs/duplicate.md")
            self.assertNotIn("docs/canonical.md", transition["changed_paths"])

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = b"# Archive me\r\n"
            write_documents(root, {"docs/old.md": source})
            item = whole_file_disposition(
                "docs/old.md",
                source,
                disposition="ARCHIVED",
                target="docs/archive/old.md",
                recovery={
                    "kind": "archive",
                    "mode": "planned",
                    "path": "docs/archive/old.md",
                    "digest": "sha256:" + hashlib.sha256(source).hexdigest(),
                },
            )
            transition = self.derive(
                root,
                [item],
                [
                    document_change(
                        "DELETE",
                        "docs/old.md",
                        source_item_ids=[item["item_id"]],
                    ),
                    document_change(
                        "CREATE",
                        "docs/archive/old.md",
                        source,
                        source_item_ids=[item["item_id"]],
                    ),
                ],
            )
            create = next(
                operation
                for operation in transition["operations"]
                if operation["operation"] == "CREATE"
            )
            self.assertEqual(create["result_bytes"], source)

    def test_orphan_mapping_and_dirty_git_recovery_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = b"# Source\n"
            write_documents(root, {"docs/source.md": data})
            initialize_git(root)
            item = whole_file_disposition(
                "docs/source.md",
                data,
                disposition="DISCARDED",
                recovery=git_recovery(root, "docs/source.md", data),
            )
            valid_delete = document_change(
                "DELETE",
                "docs/source.md",
                source_item_ids=[item["item_id"]],
            )
            transition = self.derive(root, [item], [valid_delete])
            self.assertEqual(transition["document_results"][0]["operation"], "DELETE")

            orphan = document_change("CREATE", "docs/orphan.md", b"# Orphan\n")
            with self.assertRaises(closeout.InitCloseoutError):
                self.derive(root, [item], [valid_delete, orphan])

            wrong_ids = copy.deepcopy(valid_delete)
            wrong_ids["source_item_ids"] = ["docs/other.md#<whole-file>"]
            with self.assertRaises(closeout.InitCloseoutError):
                self.derive(root, [item], [wrong_ids])

            (root / "docs" / "source.md").write_bytes(b"# Dirty\n")
            dirty_item = copy.deepcopy(item)
            dirty_item["source_digest"] = "sha256:" + hashlib.sha256(b"# Dirty\n").hexdigest()
            with self.assertRaises(closeout.InitCloseoutError) as caught:
                self.derive(root, [dirty_item], [valid_delete])
            self.assertEqual(caught.exception.classification, "recovery-mismatch")

            current_blob = git_text(
                root,
                "hash-object",
                "-w",
                "docs/source.md",
            )
            path_mismatch = copy.deepcopy(dirty_item)
            path_mismatch["recovery"] = {
                "kind": "git",
                "commit": git_text(root, "rev-parse", "HEAD"),
                "blob": current_blob,
                "digest": path_mismatch["source_digest"],
            }
            with self.assertRaises(closeout.InitCloseoutError) as caught:
                self.derive(root, [path_mismatch], [valid_delete])
            self.assertEqual(caught.exception.classification, "recovery-mismatch")

    def test_no_git_hard_delete_requires_exact_acceptance_and_normalizes_new_preview(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = b"# No repository recovery\n"
            write_documents(root, {"docs/delete.md": data})
            item = whole_file_disposition(
                "docs/delete.md",
                data,
                disposition="DISCARDED",
                recovery={"kind": "hard-delete-request"},
            )
            change = document_change(
                "DELETE",
                "docs/delete.md",
                source_item_ids=[item["item_id"]],
            )
            with self.assertRaises(closeout.InitCloseoutError) as caught:
                self.derive(root, [item], [change])
            self.assertEqual(caught.exception.status, "risk-acceptance-required")
            discard_set_id = caught.exception.details["discard_set_id"]
            acceptance_text = caught.exception.details["acceptance"]

            with self.assertRaises(closeout.InitCloseoutError):
                self.derive(
                    root,
                    [item],
                    [change],
                    {
                        "discard_set_id": discard_set_id,
                        "acceptance": acceptance_text + " altered",
                    },
                )

            transition = self.derive(
                root,
                [item],
                [change],
                {
                    "discard_set_id": discard_set_id,
                    "acceptance": acceptance_text,
                },
            )
            recovery = transition["dispositions"][0]["recovery"]
            self.assertEqual(recovery["kind"], "accepted-hard-delete")
            self.assertEqual(recovery["discard_set_id"], discard_set_id)
            self.assertRegex(recovery["acceptance_digest"], r"^sha256:[0-9a-f]{64}$")

    def test_no_git_archive_recovery_normalizes_discard_to_archive(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = b"# Preserve exactly\r\n"
            write_documents(root, {"docs/source.md": data})
            recovery = {
                "kind": "archive",
                "mode": "planned",
                "path": "docs/archive/source.md",
                "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
            }
            item = whole_file_disposition(
                "docs/source.md",
                data,
                disposition="DISCARDED",
                recovery=recovery,
            )
            transition = self.derive(
                root,
                [item],
                [
                    document_change(
                        "DELETE",
                        "docs/source.md",
                        source_item_ids=[item["item_id"]],
                    ),
                    document_change(
                        "CREATE",
                        "docs/archive/source.md",
                        data,
                        source_item_ids=[item["item_id"]],
                    ),
                ],
            )
            normalized = transition["dispositions"][0]
            self.assertEqual(normalized["disposition"], "ARCHIVED")
            self.assertEqual(normalized["target"], recovery["path"])

    def test_archive_recovery_target_cannot_be_its_source_or_another_delete(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = b"# Same path is not recovery\n"
            write_documents(root, {"docs/source.md": data})
            item = whole_file_disposition(
                "docs/source.md",
                data,
                disposition="ARCHIVED",
                target="docs/source.md",
                recovery={
                    "kind": "archive",
                    "mode": "existing",
                    "path": "docs/source.md",
                    "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
                },
            )
            with self.assertRaises(closeout.InitCloseoutError) as caught:
                self.derive(
                    root,
                    [item],
                    [
                        document_change(
                            "DELETE",
                            "docs/source.md",
                            source_item_ids=[item["item_id"]],
                        )
                    ],
                )
            self.assertEqual(
                caught.exception.classification,
                "archive-recovery-collision",
            )

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = b"# Existing archive target\n"
            write_documents(
                root,
                {
                    "docs/source.md": data,
                    "docs/recovery.md": data,
                },
            )
            initialize_git(root)
            source = whole_file_disposition(
                "docs/source.md",
                data,
                disposition="ARCHIVED",
                target="docs/recovery.md",
                recovery={
                    "kind": "archive",
                    "mode": "existing",
                    "path": "docs/recovery.md",
                    "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
                },
            )
            recovery = whole_file_disposition(
                "docs/recovery.md",
                data,
                disposition="DISCARDED",
                recovery=git_recovery(root, "docs/recovery.md", data),
            )
            with self.assertRaises(closeout.InitCloseoutError) as caught:
                self.derive(
                    root,
                    [source, recovery],
                    [
                        document_change(
                            "DELETE",
                            "docs/source.md",
                            source_item_ids=[source["item_id"]],
                        ),
                        document_change(
                            "DELETE",
                            "docs/recovery.md",
                            source_item_ids=[recovery["item_id"]],
                        ),
                    ],
                )
            self.assertEqual(
                caught.exception.classification,
                "archive-recovery-collision",
            )


if __name__ == "__main__":
    unittest.main()
