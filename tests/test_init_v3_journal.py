import copy
import errno
import hashlib
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / "skills" / "docs" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from _docs_checker import init_closeout as closeout
from _docs_checker import lifecycle
from _docs_checker import lifecycle_io
from tests.init_v3_fixture import (
    document_change,
    evidence_v3,
    request_v3,
    whole_file_disposition,
)
from tests.test_init_v3_matrix_recovery import git_recovery, initialize_git


ABSENT = "sha256:ABSENT"
JOURNAL_FIELDS = {
    "schema_version",
    "journal_version",
    "transaction_id",
    "transaction_digest",
    "authorization_projection",
    "phase",
    "control_directory_preexisted",
    "recovery_container_preexisted",
    "created_parent_identities",
    "parent_facts",
    "entries",
    "event_commit",
}
ENTRY_FIELDS = {
    "index",
    "plane",
    "operation",
    "path",
    "role",
    "start",
    "result",
    "status",
}
PARENT_FIELDS = {"path", "starting_kind", "device", "inode"}
ABSENT_START_FIELDS = {"kind", "digest", "bytes", "mode", "mtime_ns", "backup"}
FILE_START_FIELDS = ABSENT_START_FIELDS
ABSENT_RESULT_FIELDS = {"kind", "digest", "bytes", "staged"}
FILE_RESULT_FIELDS = ABSENT_RESULT_FIELDS
EVENT_COMMIT_FIELDS = {"path", "starting_digest", "result_digest"}


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


def target_snapshot(root, plan):
    paths = {
        item["path"]
        for field in ("document_operations", "control_operations")
        for item in plan[field]
    }
    result = {}
    for relative in paths:
        target = Path(root) / relative
        result[relative] = target.read_bytes() if target.is_file() else None
    return result


def tree_snapshot(root):
    root = Path(root)
    snapshot = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        metadata = path.lstat()
        if path.is_symlink():
            snapshot[relative] = ("symlink", os.readlink(path))
        elif path.is_file():
            snapshot[relative] = (
                "file",
                path.read_bytes(),
                stat.S_IMODE(metadata.st_mode),
                metadata.st_mtime_ns,
            )
        elif path.is_dir():
            snapshot[relative] = (
                "directory",
                metadata.st_dev,
                metadata.st_ino,
            )
    return snapshot


def recovery_root(root, plan):
    return Path(root) / ".diataxis" / "recovery" / plan["transaction_id"]


def prepared_fixture(root, *, with_documents=False):
    case = InitV3JournalPreparationTests(methodName="runTest")
    return case.preview(root, with_documents=with_documents)


def journal_entry(plan, *, role=None, path=None):
    entries = plan["journal_models"]["prepared"]["entries"]
    return next(
        entry
        for entry in entries
        if (role is None or entry["role"] == role)
        and (path is None or entry["path"] == path)
    )


def install_journal_entry(root, recovery, entry):
    target = Path(root) / entry["path"]
    result = entry["result"]
    if result["kind"] == "absent":
        try:
            target.unlink()
        except FileNotFoundError:
            pass
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((Path(recovery) / result["staged"]).read_bytes())
    entry["status"] = "installed"


def write_journal(recovery, journal):
    (Path(recovery) / "journal.json").write_bytes(canonical_bytes(journal))


def windows_error(number):
    error = OSError(f"WinError {number}")
    error.winerror = number
    return error


class InitV3JournalPreparationTests(unittest.TestCase):
    def api(self, module, name):
        self.assertTrue(
            hasattr(module, name),
            f"Task 5 recovery-journal API is missing: {module.__name__}.{name}",
        )
        return getattr(module, name)

    def preview(self, root, *, with_documents=False):
        root = Path(root)
        (root / "docs").mkdir(parents=True)
        (root / "docs" / "README.md").write_bytes(b"# Documentation\n")
        (root / "AGENTS.md").write_bytes(b"# Repository agents\n")
        initialize_git(root)
        evidence = evidence_v3()
        evidence["source_changes"]["agents_orientation"] = True
        document_changes = []
        if with_documents:
            source = b"# Documentation\n"
            archive_path = "docs/archive/README.md"
            item = whole_file_disposition(
                "docs/README.md",
                source,
                disposition="ARCHIVED",
                target=archive_path,
                recovery={
                    "kind": "archive",
                    "mode": "planned",
                    "path": archive_path,
                    "digest": digest(source),
                },
            )
            evidence["dispositions"] = [item]
            evidence["map_path"] = archive_path
            evidence["hot_path_bytes"] = {
                "before": {"value": 0, "unit": "bytes", "provenance": []},
                "after": {
                    "value": len(source),
                    "unit": "bytes",
                    "provenance": [
                        {
                            "route": archive_path,
                            "bytes": len(source),
                            "source": "filesystem-stat",
                        }
                    ],
                },
            }
            document_changes = [
                document_change(
                    "CREATE",
                    archive_path,
                    source,
                    source_item_ids=[item["item_id"]],
                ),
                document_change(
                    "DELETE",
                    "docs/README.md",
                    source_item_ids=[item["item_id"]],
                ),
            ]
        try:
            prepared = closeout.prepare_initialization_closeout(
                root,
                request_v3(evidence=evidence, document_changes=document_changes),
            )
        except closeout.InitCloseoutError as exc:
            self.fail(
                "Task 5 preview cannot yet authorize document operations: "
                f"{exc.status}/{exc.classification}"
            )
        plan = prepared["plan"]
        for field in (
            "document_operations",
            "control_operations",
            "corpus_transition",
            "transaction_digest",
            "journal_models",
            "journal_bytes",
            "recovery_files",
        ):
            self.assertTrue(
                field in plan,
                f"Task 5 preview plan is missing {field}",
            )
        return prepared

    def valid_operations(self):
        body = b"# New\n"
        document = {
            "operation": "CREATE",
            "path": "docs/new.md",
            "role": "document-result",
            "starting_digest": ABSENT,
            "result_digest": digest(body),
            "result_bytes": body,
            "source_item_ids": [],
            "recovery_binding": None,
        }
        control = {
            "operation": "CONTROL_REPLACE",
            "path": ".diataxis/state.json",
            "role": "state",
            "starting_digest": ABSENT,
            "result_digest": digest(b"{}\n"),
        }
        return document, control

    def test_document_and_control_unions_reject_cross_plane_paths_fields_and_roles(self):
        normalize = self.api(lifecycle, "_normalize_transaction_operations_v3")
        document, control = self.valid_operations()
        normalized = normalize([document], [control], "docs")
        self.assertEqual(normalized["document_operations"], [document])
        self.assertEqual(normalized["control_operations"], [control])

        invalid_variants = []
        value = copy.deepcopy(document)
        value["path"] = ".diataxis/state.json"
        invalid_variants.append(([value], [control]))
        value = copy.deepcopy(document)
        value["role"] = "state"
        invalid_variants.append(([value], [control]))
        value = copy.deepcopy(document)
        value.pop("recovery_binding")
        invalid_variants.append(([value], [control]))
        value = copy.deepcopy(control)
        value["path"] = "docs/new.md"
        invalid_variants.append(([document], [value]))
        value = copy.deepcopy(control)
        value["role"] = "document-result"
        invalid_variants.append(([document], [value]))
        value = copy.deepcopy(control)
        value["result_bytes"] = b"{}\n"
        invalid_variants.append(([document], [value]))
        value = copy.deepcopy(control)
        value["operation"] = "CREATE"
        invalid_variants.append(([document], [value]))

        for documents, controls in invalid_variants:
            with self.subTest(documents=documents, controls=controls):
                with self.assertRaises(ValueError):
                    normalize(documents, controls, "docs")

    def test_document_union_intrinsically_reserves_root_agents_case_insensitively(self):
        normalize = self.api(lifecycle, "_normalize_transaction_operations_v3")
        document, _ = self.valid_operations()
        document["path"] = "README.md"
        self.assertEqual(
            normalize([document], [], ".")["document_operations"],
            [document],
        )
        document["path"] = "AgEnTs.Md"
        with self.assertRaises(ValueError):
            normalize([document], [], ".")

    def test_authorization_binds_both_corpora_operations_recovery_control_and_order(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plan = self.preview(root, with_documents=True)["plan"]
            validate = self.api(lifecycle_io, "_validate_plan_authorization")
            validate(root, plan)

            mutations = []
            for side in ("starting", "result"):
                candidate = copy.deepcopy(plan)
                candidate["corpus_transition"][side]["paths_digest"] = digest(side.encode())
                mutations.append(candidate)
            candidate = copy.deepcopy(plan)
            candidate["document_operations"][0]["result_digest"] = digest(b"tampered")
            mutations.append(candidate)
            candidate = copy.deepcopy(plan)
            candidate["document_operations"][-1]["recovery_binding"] = digest(
                b"different-recovery"
            )
            mutations.append(candidate)
            candidate = copy.deepcopy(plan)
            candidate["document_operations"] = list(
                reversed(candidate["document_operations"])
            )
            mutations.append(candidate)
            candidate = copy.deepcopy(plan)
            candidate["control_operations"][0]["result_digest"] = digest(b"tampered")
            mutations.append(candidate)
            candidate = copy.deepcopy(plan)
            candidate["control_operations"] = list(reversed(candidate["control_operations"]))
            mutations.append(candidate)
            candidate = copy.deepcopy(plan)
            candidate["event"]["worktree_identity"] = "0" * 64
            mutations.append(candidate)

            for candidate in mutations:
                with self.subTest(mutation=len(mutations)):
                    with self.assertRaises(ValueError):
                        validate(root, candidate)

    def test_transaction_id_is_the_declared_prefix_of_the_complete_authorization_digest(self):
        with tempfile.TemporaryDirectory() as td:
            plan = self.preview(Path(td), with_documents=True)["plan"]
            self.assertRegex(plan["transaction_digest"], r"^sha256:[0-9a-f]{64}$")
            self.assertEqual(
                plan["transaction_id"],
                "TXN-"
                + plan["transaction_digest"].removeprefix("sha256:")[:16].upper(),
            )

    def test_preview_encodes_exact_preparing_and_prepared_journal_models(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plan = self.preview(root)["plan"]
            self.assertEqual(set(plan["journal_models"]), {"preparing", "prepared"})
            self.assertEqual(set(plan["journal_bytes"]), {"preparing", "prepared"})

            for phase in ("preparing", "prepared"):
                journal = plan["journal_models"][phase]
                self.assertEqual(set(journal), JOURNAL_FIELDS)
                self.assertEqual(journal["schema_version"], 3)
                self.assertEqual(journal["journal_version"], "init-recovery-v1")
                self.assertEqual(journal["transaction_id"], plan["transaction_id"])
                self.assertEqual(journal["transaction_digest"], plan["transaction_digest"])
                self.assertEqual(journal["phase"], phase)
                self.assertEqual(plan["journal_bytes"][phase], canonical_bytes(journal))
                self.assertNotIn(b"result_bytes", plan["journal_bytes"][phase])
                self.assertLessEqual(
                    len(plan["journal_bytes"][phase]),
                    lifecycle_io.INIT_RECOVERY_JOURNAL_MAX_BYTES,
                )
                self.assertIs(
                    type(journal["control_directory_preexisted"]),
                    bool,
                )
                self.assertIs(
                    type(journal["recovery_container_preexisted"]),
                    bool,
                )
                self.assertEqual(
                    journal["authorization_projection"],
                    lifecycle_io._plan_authorization_semantics(plan, root),
                )
                self.assertEqual(set(journal["event_commit"]), EVENT_COMMIT_FIELDS)
                event_entry = next(
                    entry for entry in journal["entries"] if entry["role"] == "event"
                )
                self.assertEqual(
                    journal["event_commit"],
                    {
                        "path": event_entry["path"],
                        "starting_digest": event_entry["start"]["digest"],
                        "result_digest": event_entry["result"]["digest"],
                    },
                )

                self.assertEqual(
                    journal["parent_facts"],
                    sorted(journal["parent_facts"], key=lambda item: item["path"].casefold()),
                )
                for fact in journal["parent_facts"]:
                    self.assertEqual(set(fact), PARENT_FIELDS)
                    self.assertIn(fact["starting_kind"], {"directory", "absent"})
                    if fact["starting_kind"] == "directory":
                        self.assertGreater(fact["device"], 0)
                        self.assertGreater(fact["inode"], 0)
                    else:
                        self.assertIsNone(fact["device"])
                        self.assertIsNone(fact["inode"])

                self.assertEqual(
                    [entry["index"] for entry in journal["entries"]],
                    list(range(len(journal["entries"]))),
                )
                for entry in journal["entries"]:
                    self.assertEqual(set(entry), ENTRY_FIELDS)
                    self.assertIn(entry["plane"], {"document", "control"})
                    self.assertIn(
                        entry["operation"],
                        {"CREATE", "REPLACE", "DELETE", "CONTROL_REPLACE"},
                    )
                    self.assertEqual(entry["status"], "pending")
                    self.assertEqual(set(entry["start"]), FILE_START_FIELDS)
                    self.assertEqual(set(entry["result"]), FILE_RESULT_FIELDS)
                    if entry["start"]["kind"] == "absent":
                        self.assertEqual(entry["start"]["digest"], ABSENT)
                        self.assertIsNone(entry["start"]["backup"])
                    else:
                        self.assertRegex(entry["start"]["backup"], r"^backups/\d{4}\.bin$")
                    if entry["result"]["kind"] == "absent":
                        self.assertEqual(entry["result"]["digest"], ABSENT)
                        self.assertIsNone(entry["result"]["staged"])
                    else:
                        self.assertRegex(entry["result"]["staged"], r"^results/\d{4}\.bin$")

            self.assertEqual(
                plan["journal_models"]["preparing"]["entries"],
                plan["journal_models"]["prepared"]["entries"],
            )
            preparing = copy.deepcopy(plan["journal_models"]["preparing"])
            prepared = copy.deepcopy(plan["journal_models"]["prepared"])
            preparing.pop("phase")
            prepared.pop("phase")
            self.assertEqual(preparing, prepared)
            self.assertTrue(
                all(
                    entry["status"] == "pending"
                    for entry in prepared["entries"]
                )
            )

    def test_backup_result_and_journal_caps_accept_maximum_and_reject_maximum_plus_one(self):
        validate = self.api(lifecycle_io, "_validate_recovery_capacity_v3")
        self.assertEqual(lifecycle_io.INIT_RECOVERY_BACKUP_MAX_BYTES, 8 * 1024 * 1024)
        self.assertEqual(lifecycle_io.INIT_RECOVERY_BACKUP_MAX_FILES, 64)
        self.assertEqual(lifecycle_io.INIT_RECOVERY_RESULT_MAX_FILES, 80)
        self.assertEqual(
            lifecycle_io.INIT_RECOVERY_DOCUMENT_RESULT_MAX_BYTES,
            4 * 1024 * 1024,
        )
        self.assertEqual(lifecycle_io.INIT_RECOVERY_JOURNAL_MAX_BYTES, 1024 * 1024)

        backups = {
            f"backups/{index:04d}.bin": b""
            for index in range(lifecycle_io.INIT_RECOVERY_BACKUP_MAX_FILES)
        }
        per_document = 2 * 1024 * 1024
        for index in range(4):
            backups[f"backups/{index:04d}.bin"] = b"b" * per_document
        results = {
            f"results/{index:04d}.bin": b""
            for index in range(lifecycle_io.INIT_RECOVERY_RESULT_MAX_FILES)
        }
        journals = {
            phase: b"j" * lifecycle_io.INIT_RECOVERY_JOURNAL_MAX_BYTES
            for phase in ("preparing", "prepared")
        }
        validate({**backups, **results}, journals)

        invalid = []
        candidate = dict(backups)
        candidate["backups/0004.bin"] = b"x"
        invalid.append(({**candidate, **results}, journals))
        candidate = dict(backups)
        candidate[f"backups/{len(backups):04d}.bin"] = b""
        invalid.append(({**candidate, **results}, journals))
        candidate = dict(results)
        candidate[f"results/{len(results):04d}.bin"] = b""
        invalid.append(({**backups, **candidate}, journals))
        candidate = dict(journals)
        candidate["prepared"] += b"x"
        invalid.append(({**backups, **results}, candidate))

        for recovery_files, journal_bytes in invalid:
            with self.subTest(
                backup_count=sum(path.startswith("backups/") for path in recovery_files),
                result_count=sum(path.startswith("results/") for path in recovery_files),
                prepared_bytes=len(journal_bytes["prepared"]),
            ):
                with self.assertRaises(ValueError):
                    validate(recovery_files, journal_bytes)

    def test_recovery_journal_rejects_oversize_role_body_before_any_body_read(self):
        load_journal = self.api(lifecycle_io, "_load_journal_v3")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plan = self.preview(root)["plan"]
            lifecycle_io._prepare_recovery_area_v3(root, plan)
            recovery = recovery_root(root, plan)
            journal = copy.deepcopy(plan["journal_models"]["prepared"])
            manifest = journal_entry(plan, role="manifest")
            self.assertEqual(journal["entries"][0]["path"], manifest["path"])
            journal["entries"][0]["result"]["bytes"] = (
                lifecycle_io.MAX_MANIFEST_BYTES + 1
            )
            write_journal(recovery, journal)

            with mock.patch.object(
                lifecycle_io, "_read_recovery_body_v3"
            ) as body_reader:
                with self.assertRaises(ValueError):
                    load_journal(root, recovery)
            body_reader.assert_not_called()

    def test_foreign_existing_target_device_fails_before_recovery_body_write(self):
        prepare_area = self.api(lifecycle_io, "_prepare_recovery_area_v3")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plan = self.preview(root)["plan"]
            before = target_snapshot(root, plan)
            target = root / "AGENTS.md"
            recovery = recovery_root(root, plan)
            real_stat = Path.stat

            def foreign_target_after_recovery_bootstrap(path, *args, **kwargs):
                observed = real_stat(path, *args, **kwargs)
                if Path(path) == target and os.path.lexists(recovery):
                    values = list(observed)
                    values[2] = observed.st_dev + 1
                    return os.stat_result(values)
                return observed

            with mock.patch.object(
                Path, "stat", foreign_target_after_recovery_bootstrap
            ), mock.patch.object(
                lifecycle_io, "_write_flushed_file_v3"
            ) as writer:
                with self.assertRaises(OSError) as raised:
                    prepare_area(root, plan)
            self.assertEqual(raised.exception.errno, errno.EXDEV)
            writer.assert_not_called()
            self.assertEqual(target_snapshot(root, plan), before)

    def test_same_device_parent_identity_and_reparse_checks_precede_preparation(self):
        prepare_area = self.api(lifecycle_io, "_prepare_recovery_area_v3")

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plan = self.preview(root)["plan"]
            before = target_snapshot(root, plan)
            real_stat = Path.stat

            def different_recovery_device(path, *args, **kwargs):
                observed = real_stat(path, *args, **kwargs)
                if Path(path) == recovery_root(root, plan):
                    values = list(observed)
                    values[2] = observed.st_dev + 1
                    return os.stat_result(values)
                return observed

            with mock.patch.object(Path, "stat", different_recovery_device), mock.patch.object(
                lifecycle_io, "_write_flushed_file_v3"
            ) as writer:
                with self.assertRaises(OSError) as raised:
                    prepare_area(root, plan)
            self.assertEqual(raised.exception.errno, errno.EXDEV)
            writer.assert_not_called()
            self.assertEqual(target_snapshot(root, plan), before)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plan = self.preview(root)["plan"]
            before = target_snapshot(root, plan)
            (root / ".diataxis").mkdir()
            with mock.patch.object(
                lifecycle_io, "_validate_plan_authorization", return_value=None
            ), mock.patch.object(lifecycle_io, "_write_flushed_file_v3") as writer:
                with self.assertRaises(ValueError):
                    prepare_area(root, plan)
            writer.assert_not_called()
            self.assertEqual(target_snapshot(root, plan), before)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plan = self.preview(root)["plan"]
            reparse = root / ".diataxis"
            reparse.mkdir()
            real_is_symlink = Path.is_symlink

            def mark_reparse(path):
                return Path(path) == reparse or real_is_symlink(path)

            with mock.patch.object(
                lifecycle_io, "_validate_plan_authorization", return_value=None
            ), mock.patch.object(
                Path, "is_symlink", mark_reparse
            ), mock.patch.object(lifecycle_io, "_write_flushed_file_v3") as writer:
                with self.assertRaises((OSError, ValueError)):
                    prepare_area(root, plan)
            writer.assert_not_called()

    def test_final_preparation_revalidation_rejects_post_flush_parent_identity_drift(self):
        prepare_area = self.api(lifecycle_io, "_prepare_recovery_area_v3")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plan = self.preview(root, with_documents=True)["plan"]
            before = target_snapshot(root, plan)
            real_write = lifecycle_io._write_flushed_file_v3
            swapped = False

            def swap_parent_after_prepared_flush(path, data, *, exclusive):
                nonlocal swapped
                result = real_write(path, data, exclusive=exclusive)
                if (
                    not swapped
                    and Path(path).name == "journal.next"
                    and json.loads(data).get("phase") == "prepared"
                ):
                    swapped = True
                    original = root / "docs"
                    moved = root / "docs-original"
                    original.rename(moved)
                    original.mkdir()
                    (original / "README.md").write_bytes(
                        (moved / "README.md").read_bytes()
                    )
                    (moved / "README.md").unlink()
                    moved.rmdir()
                return result

            with mock.patch.object(
                lifecycle_io,
                "_write_flushed_file_v3",
                side_effect=swap_parent_after_prepared_flush,
            ):
                with self.assertRaises(ValueError):
                    prepare_area(root, plan)

            self.assertTrue(swapped)
            self.assertEqual(target_snapshot(root, plan), before)
            self.assertFalse(recovery_root(root, plan).exists())

    def test_final_preparation_revalidation_rejects_post_flush_target_device_drift(self):
        prepare_area = self.api(lifecycle_io, "_prepare_recovery_area_v3")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plan = self.preview(root)["plan"]
            before = target_snapshot(root, plan)
            recovery = recovery_root(root, plan)
            real_write = lifecycle_io._write_flushed_file_v3
            real_stat = Path.stat
            real_fstat = os.fstat
            drifted = False

            class ForeignDevice:
                def __init__(self, metadata):
                    self.metadata = metadata

                @property
                def st_dev(self):
                    return self.metadata.st_dev + 1

                def __getattr__(self, name):
                    return getattr(self.metadata, name)

            def drift_after_prepared_flush(path, data, *, exclusive):
                nonlocal drifted
                result = real_write(path, data, exclusive=exclusive)
                if (
                    Path(path).name == "journal.next"
                    and json.loads(data).get("phase") == "prepared"
                ):
                    drifted = True
                return result

            def drifted_target_stat(path, *args, **kwargs):
                observed = real_stat(path, *args, **kwargs)
                candidate = Path(path).absolute()
                if (
                    drifted
                    and candidate.is_relative_to(root.absolute())
                    and not candidate.is_relative_to(recovery.absolute())
                    and stat.S_ISREG(observed.st_mode)
                ):
                    return ForeignDevice(observed)
                return observed

            def drifted_target_fstat(descriptor):
                observed = real_fstat(descriptor)
                if drifted and stat.S_ISREG(observed.st_mode):
                    return ForeignDevice(observed)
                return observed

            with mock.patch.object(
                lifecycle_io,
                "_write_flushed_file_v3",
                side_effect=drift_after_prepared_flush,
            ), mock.patch.object(
                Path,
                "stat",
                autospec=True,
                side_effect=drifted_target_stat,
            ), mock.patch.object(
                lifecycle_io.os,
                "fstat",
                side_effect=drifted_target_fstat,
            ):
                with self.assertRaises(OSError) as raised:
                    prepare_area(root, plan)

            self.assertTrue(drifted)
            self.assertEqual(raised.exception.errno, errno.EXDEV)
            self.assertEqual(target_snapshot(root, plan), before)
            self.assertFalse(recovery.exists())

    def test_start_capture_never_binds_digest_a_to_backup_bytes_b(self):
        capture = self.api(lifecycle_io, "_operation_start_v3")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plan = self.preview(root)["plan"]
            operation = {
                "plane": "control",
                **next(
                    operation
                    for operation in plan["control_operations"]
                    if operation["path"] == "AGENTS.md"
                ),
            }
            target = root / "AGENTS.md"
            real_open = Path.open
            swapped = False

            class SwapAfterRead:
                def __init__(self, handle):
                    self.handle = handle

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    self.handle.close()

                def fileno(self):
                    return self.handle.fileno()

                def read(self, size=-1):
                    nonlocal swapped
                    data = self.handle.read(size)
                    if not swapped:
                        swapped = True
                        with open(target, "wb") as replacement:
                            replacement.write(b"different second-read bytes\n")
                    return data

            def open_then_swap(path, mode="r", *args, **kwargs):
                handle = real_open(path, mode, *args, **kwargs)
                if Path(path) == target and mode == "rb":
                    return SwapAfterRead(handle)
                return handle

            recovery_files = {}
            with mock.patch.object(
                Path,
                "open",
                autospec=True,
                side_effect=open_then_swap,
            ):
                with self.assertRaises(ValueError):
                    capture(root, operation, 0, recovery_files)
            self.assertTrue(swapped)
            self.assertEqual(recovery_files, {})

    def test_recovery_area_contains_only_guard_journal_backups_and_results(self):
        prepare_area = self.api(lifecycle_io, "_prepare_recovery_area_v3")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plan = self.preview(root)["plan"]
            before = target_snapshot(root, plan)
            prepared_recovery = prepare_area(root, plan)
            self.assertEqual(target_snapshot(root, plan), before)

            recovery = root / ".diataxis" / "recovery" / plan["transaction_id"]
            self.assertEqual(
                {item.name for item in recovery.iterdir()},
                {".gitignore", "journal.json", "backups", "results"},
            )
            self.assertEqual((recovery / ".gitignore").read_bytes(), b"*\n")
            self.assertEqual(
                (recovery / "journal.json").read_bytes(),
                canonical_bytes(prepared_recovery["journal"]),
            )
            actual_files = {
                path.relative_to(recovery).as_posix(): path.read_bytes()
                for path in recovery.rglob("*")
                if path.is_file() and path.name not in {".gitignore", "journal.json"}
            }
            self.assertEqual(actual_files, plan["recovery_files"])
            self.assertEqual(
                list(root.rglob(f".docs-txn-{plan['transaction_id'][4:]}-*.tmp")),
                [],
            )

    def test_every_preparation_flush_failure_preserves_all_authorized_targets(self):
        prepare_area = self.api(lifecycle_io, "_prepare_recovery_area_v3")
        with tempfile.TemporaryDirectory() as td:
            seed = Path(td)
            plan = self.preview(seed)["plan"]
            backup_count = sum(
                entry["start"]["kind"] == "file"
                for entry in plan["journal_models"]["prepared"]["entries"]
            )
            result_count = sum(
                entry["result"]["kind"] == "file"
                for entry in plan["journal_models"]["prepared"]["entries"]
            )
            flushes = 3 + backup_count + result_count

        for ordinal in range(1, flushes + 1):
            with self.subTest(flush_ordinal=ordinal), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                plan = self.preview(root)["plan"]
                before = target_snapshot(root, plan)
                calls = 0
                real_fsync = os.fsync

                def injected_fsync(descriptor):
                    nonlocal calls
                    calls += 1
                    if calls == ordinal:
                        raise OSError(f"injected flush failure {ordinal}")
                    return real_fsync(descriptor)

                with mock.patch.object(lifecycle_io.os, "fsync", injected_fsync):
                    with self.assertRaises(OSError):
                        prepare_area(root, plan)
                self.assertEqual(calls, ordinal)
                self.assertEqual(target_snapshot(root, plan), before)


class InitV3JournalApplyTests(unittest.TestCase):
    def api(self, module, name):
        self.assertTrue(
            hasattr(module, name),
            f"Task 6 transaction API is missing: {module.__name__}.{name}",
        )
        return getattr(module, name)

    def prepared(self, root, *, with_documents=False):
        return prepared_fixture(root, with_documents=with_documents)

    def apply(self, root, plan):
        return lifecycle_io.apply_verified_closeout(
            root,
            plan,
            approved_transaction=plan["transaction_id"],
            verification=lambda: True,
        )

    def authorized_paths(self, plan):
        return {
            operation["path"]
            for field in ("document_operations", "control_operations")
            for operation in plan[field]
        }

    def relative_if_authorized(self, root, plan, target):
        try:
            relative = Path(target).absolute().relative_to(Path(root).absolute()).as_posix()
        except (OSError, ValueError):
            return None
        return relative if relative in self.authorized_paths(plan) else None

    def doctor_preview(self, root):
        return lifecycle_io.preview_state_conflict_recovery(root)

    def doctor_apply(self, root, preview):
        return lifecycle_io.apply_state_conflict_recovery(
            root,
            preview,
            approved_preview=preview["approval"],
            verification=None,
        )

    def trace_public_apply_reads(
        self,
        root,
        evidence,
        *,
        selected_route,
        document_changes=None,
    ):
        from _docs_checker import discovery

        preview_request = request_v3(
            evidence=evidence,
            document_changes=document_changes,
        )
        preview = closeout.prepare_initialization_closeout(root, preview_request)
        apply_request = request_v3(
            "apply",
            evidence=evidence,
            document_changes=document_changes,
            approval=preview["approval"],
        )
        real_open = Path.open
        real_closeout_scan = closeout.scan_selected_document_corpus
        real_lifecycle_scan = lifecycle_io.scan_selected_document_corpus
        binary_opens = []
        metadata_scans = {"closeout": 0, "lifecycle": 0}

        def path_key(path):
            return os.path.normcase(os.path.abspath(os.fspath(path)))

        def observe_open(path, mode="r", *args, **kwargs):
            if mode == "rb":
                binary_opens.append(path_key(path))
            return real_open(path, mode, *args, **kwargs)

        def observe_closeout_scan(*args, **kwargs):
            metadata_scans["closeout"] += 1
            return real_closeout_scan(*args, **kwargs)

        def observe_lifecycle_scan(*args, **kwargs):
            metadata_scans["lifecycle"] += 1
            return real_lifecycle_scan(*args, **kwargs)

        with mock.patch.object(
            Path,
            "open",
            observe_open,
        ), mock.patch.object(
            closeout,
            "scan_selected_document_corpus",
            side_effect=observe_closeout_scan,
        ), mock.patch.object(
            lifecycle_io,
            "scan_selected_document_corpus",
            side_effect=observe_lifecycle_scan,
        ), mock.patch.object(
            discovery,
            "discover_init_scope",
            side_effect=AssertionError("continuation discovery must not replay"),
        ):
            prepared = closeout.prepare_initialization_closeout(root, apply_request)
            response = closeout.apply_response(
                root,
                prepared,
                apply_request["approval"],
            )

        selected_key = path_key(Path(root) / selected_route)
        return {
            "response": response,
            "prepared": prepared,
            "binary_opens": binary_opens,
            "selected_route_opens": binary_opens.count(selected_key),
            "metadata_scans": metadata_scans,
            "path_key": path_key,
        }

    def interrupted(self, root, plan, *, installed_paths, phase="installing"):
        prepared_recovery = lifecycle_io._prepare_recovery_area_v3(root, plan)
        recovery = recovery_root(root, plan)
        journal = prepared_recovery["journal"]
        journal["phase"] = phase
        for entry in journal["entries"]:
            if entry["path"] in installed_paths:
                install_journal_entry(root, recovery, entry)
        for fact in journal["parent_facts"]:
            target = root if fact["path"] == "." else root / fact["path"]
            if fact["starting_kind"] == "absent" and target.is_dir():
                metadata = target.stat()
                journal["created_parent_identities"][fact["path"]] = {
                    "device": metadata.st_dev,
                    "inode": metadata.st_ino,
                }
        write_journal(recovery, journal)
        return recovery, journal

    def forge_document_create(self, recovery, entry, *, path, data, role="document-result"):
        entry.update(
            {
                "plane": "document",
                "operation": "CREATE",
                "path": path,
                "role": role,
                "start": {
                    "kind": "absent",
                    "digest": ABSENT,
                    "bytes": 0,
                    "mode": None,
                    "mtime_ns": None,
                    "backup": None,
                },
                "result": {
                    "kind": "file",
                    "digest": digest(data),
                    "bytes": len(data),
                    "staged": f"results/{entry['index']:04d}.bin",
                },
                "status": "pending",
            }
        )
        (Path(recovery) / entry["result"]["staged"]).write_bytes(data)

    def reorder_journal(self, recovery, journal, order):
        entries = journal["entries"]
        bodies = {}
        for entry in entries:
            for side, pointer_field in (("start", "backup"), ("result", "staged")):
                value = entry[side]
                if value["kind"] == "file":
                    bodies[(entry["index"], side)] = (
                        Path(recovery) / value[pointer_field]
                    ).read_bytes()
        reordered = [copy.deepcopy(entries[index]) for index in order]
        for index, entry in enumerate(reordered):
            old_index = entry["index"]
            entry["index"] = index
            for side, pointer_field, directory in (
                ("start", "backup", "backups"),
                ("result", "staged", "results"),
            ):
                value = entry[side]
                if value["kind"] != "file":
                    continue
                pointer = f"{directory}/{index:04d}.bin"
                value[pointer_field] = pointer
                (Path(recovery) / pointer).write_bytes(bodies[(old_index, side)])
        journal["entries"] = reordered

    def test_every_target_is_revalidated_immediately_before_its_individual_operation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plan = self.prepared(root)["plan"]
            event_path = journal_entry(plan, role="event")["path"]
            real_replace = lifecycle_io.os.replace
            authorized_replaces = []
            injected = False

            def drift_before_later_operation(source, target):
                nonlocal injected
                relative = self.relative_if_authorized(root, plan, target)
                result = real_replace(source, target)
                if relative is not None:
                    authorized_replaces.append(relative)
                    if not injected:
                        injected = True
                        (root / "AGENTS.md").write_bytes(b"third-party drift\n")
                return result

            with mock.patch.object(
                lifecycle_io.os, "replace", side_effect=drift_before_later_operation
            ):
                result = self.apply(root, plan)

            self.assertTrue(injected)
            self.assertEqual(result["status"], "closeout-failed")
            self.assertFalse(result["successful_event_recorded"])
            self.assertNotIn("AGENTS.md", authorized_replaces)
            self.assertNotIn(event_path, authorized_replaces)
            self.assertEqual((root / "AGENTS.md").read_bytes(), b"third-party drift\n")
            self.assertTrue(recovery_root(root, plan).exists())

    def test_active_target_is_revalidated_after_staging_and_before_replace(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plan = self.prepared(root)["plan"]
            agents = journal_entry(plan, path="AGENTS.md")
            install_name = f"{agents['index']:04d}.install"
            real_verify = lifecycle_io._verify_exact_file_v3
            injected = False

            def drift_after_install_staging(path, expected):
                nonlocal injected
                result = real_verify(path, expected)
                if Path(path).name == install_name:
                    injected = True
                    (root / "AGENTS.md").write_bytes(b"third-party drift\n")
                return result

            with mock.patch.object(
                lifecycle_io,
                "_verify_exact_file_v3",
                side_effect=drift_after_install_staging,
            ):
                result = self.apply(root, plan)

            self.assertTrue(injected)
            self.assertEqual(result["status"], "closeout-failed")
            self.assertFalse(result["successful_event_recorded"])
            self.assertEqual((root / "AGENTS.md").read_bytes(), b"third-party drift\n")
            self.assertTrue(recovery_root(root, plan).exists())

    def test_rollback_target_is_revalidated_after_restore_staging_before_replace(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plan = self.prepared(root, with_documents=True)["plan"]
            recovery, journal = self.interrupted(
                root,
                plan,
                installed_paths={"docs/archive/README.md", "docs/README.md"},
            )
            source = next(
                entry
                for entry in journal["entries"]
                if entry["path"] == "docs/README.md"
            )
            restore_name = f"{source['index']:04d}.restore"
            preview = self.doctor_preview(root)
            self.assertEqual(preview["action"], "rollback")
            real_verify = lifecycle_io._verify_exact_file_v3
            injected = False

            def drift_after_restore_staging(path, expected):
                nonlocal injected
                result = real_verify(path, expected)
                if Path(path).name == restore_name:
                    injected = True
                    (root / "docs" / "README.md").write_bytes(
                        b"third-party recovery-window edit\n"
                    )
                return result

            with mock.patch.object(
                lifecycle_io,
                "_verify_exact_file_v3",
                side_effect=drift_after_restore_staging,
            ):
                result = self.doctor_apply(root, preview)

            self.assertTrue(injected)
            self.assertEqual(result["status"], "recovery-failed")
            self.assertFalse(result["successful_event_recorded"])
            self.assertEqual(
                (root / "docs" / "README.md").read_bytes(),
                b"third-party recovery-window edit\n",
            )
            self.assertTrue(recovery.exists())

    def test_recorded_parent_identity_is_revalidated_between_operations_and_retries(self):
        for injection in ("between-operations", "before-retry"):
            with self.subTest(injection=injection), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                plan = self.prepared(root, with_documents=True)["plan"]
                manifest_path = journal_entry(plan, role="manifest")["path"]
                archive_path = "docs/archive/README.md"
                event_path = journal_entry(plan, role="event")["path"]
                real_replace = lifecycle_io.os.replace
                swapped = False
                archive_attempts = 0

                def swap_docs_parent():
                    nonlocal swapped
                    original = root / "docs"
                    moved = root / "docs-original"
                    original.rename(moved)
                    original.mkdir()
                    (original / "README.md").write_bytes(
                        (moved / "README.md").read_bytes()
                    )
                    (moved / "README.md").unlink()
                    archive = moved / "archive"
                    if archive.is_dir():
                        archive.rmdir()
                    moved.rmdir()
                    swapped = True

                def swap_ancestor(source, target):
                    nonlocal archive_attempts
                    relative = self.relative_if_authorized(root, plan, target)
                    if injection == "before-retry" and relative == archive_path:
                        archive_attempts += 1
                        if archive_attempts == 1:
                            swap_docs_parent()
                            raise windows_error(32)
                    result = real_replace(source, target)
                    if (
                        injection == "between-operations"
                        and relative == manifest_path
                    ):
                        swap_docs_parent()
                    return result

                with mock.patch.object(
                    lifecycle_io.os,
                    "replace",
                    side_effect=swap_ancestor,
                ), mock.patch("time.sleep"):
                    result = self.apply(root, plan)

                self.assertTrue(swapped)
                self.assertEqual(result["status"], "closeout-failed")
                self.assertFalse(result["successful_event_recorded"])
                self.assertFalse((root / archive_path).exists())
                self.assertFalse((root / event_path).exists())
                if injection == "before-retry":
                    self.assertEqual(archive_attempts, 1)

    def test_live_entry_observation_rejects_path_replacement_during_read(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plan = self.prepared(root)["plan"]
            entry = journal_entry(plan, role="agents")
            target = root / entry["path"]
            real_stat = Path.stat

            class ReplacedPathIdentity:
                def __init__(self, metadata):
                    self.metadata = metadata

                @property
                def st_ino(self):
                    return self.metadata.st_ino + 1

                def __getattr__(self, name):
                    return getattr(self.metadata, name)

            def replaced_path_stat(path, *args, **kwargs):
                observed = real_stat(path, *args, **kwargs)
                if Path(path) == target:
                    return ReplacedPathIdentity(observed)
                return observed

            with mock.patch.object(
                Path,
                "stat",
                autospec=True,
                side_effect=replaced_path_stat,
            ):
                _, classification = lifecycle_io._classify_live_entry_v3(root, entry)

            self.assertEqual(classification, "third")

    def test_pre_event_verification_scans_actual_result_corpus_and_rolls_back_rogue_markdown(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plan = self.prepared(root)["plan"]
            event_path = journal_entry(plan, role="event")["path"]
            real_replace = lifecycle_io.os.replace
            rogue = root / "docs" / "rogue.md"
            injected = False

            def inject_rogue_before_event(source, target):
                nonlocal injected
                relative = self.relative_if_authorized(root, plan, target)
                result = real_replace(source, target)
                if relative == "AGENTS.md" and not injected:
                    rogue.write_bytes(b"# Unapproved document\n")
                    injected = True
                return result

            with mock.patch.object(
                lifecycle_io.os,
                "replace",
                side_effect=inject_rogue_before_event,
            ):
                result = self.apply(root, plan)

            self.assertTrue(injected)
            self.assertEqual(result["status"], "closeout-failed")
            self.assertTrue(result["rollback"]["complete"])
            self.assertFalse(result["successful_event_recorded"])
            self.assertFalse((root / event_path).exists())
            self.assertEqual(rogue.read_bytes(), b"# Unapproved document\n")

    def test_post_call_exception_reconciles_intended_start_or_third_state_without_blind_retry(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plan = self.prepared(root)["plan"]
            selected = journal_entry(plan, role="manifest")["path"]
            real_replace = lifecycle_io.os.replace
            attempts = 0

            def install_then_raise(source, target):
                nonlocal attempts
                relative = self.relative_if_authorized(root, plan, target)
                if relative == selected and attempts == 0:
                    attempts += 1
                    real_replace(source, target)
                    raise OSError("post-call exception after intended result")
                return real_replace(source, target)

            with mock.patch.object(
                lifecycle_io.os, "replace", side_effect=install_then_raise
            ):
                result = self.apply(root, plan)
            self.assertEqual(attempts, 1)
            self.assertEqual(result["status"], "applied")
            self.assertTrue(result["successful_event_recorded"])

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plan = self.prepared(root)["plan"]
            selected = journal_entry(plan, role="manifest")["path"]
            real_replace = lifecycle_io.os.replace
            attempts = 0

            def unchanged_start_then_raise(source, target):
                nonlocal attempts
                if self.relative_if_authorized(root, plan, target) == selected:
                    attempts += 1
                    raise OSError("non-retryable unchanged-start exception")
                return real_replace(source, target)

            with mock.patch.object(
                lifecycle_io.os, "replace", side_effect=unchanged_start_then_raise
            ):
                result = self.apply(root, plan)
            self.assertEqual(attempts, 1)
            self.assertEqual(result["status"], "closeout-failed")
            self.assertFalse(result["successful_event_recorded"])

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plan = self.prepared(root)["plan"]
            selected = journal_entry(plan, role="manifest")["path"]
            real_replace = lifecycle_io.os.replace
            attempts = 0

            def third_state_then_raise(source, target):
                nonlocal attempts
                target = Path(target)
                if self.relative_if_authorized(root, plan, target) == selected:
                    attempts += 1
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(b"third state\n")
                    raise OSError("ambiguous post-call exception")
                return real_replace(source, target)

            with mock.patch.object(
                lifecycle_io.os, "replace", side_effect=third_state_then_raise
            ):
                result = self.apply(root, plan)
            self.assertEqual(attempts, 1)
            self.assertEqual(result["status"], "closeout-failed")
            self.assertFalse(result["successful_event_recorded"])
            self.assertEqual((root / selected).read_bytes(), b"third state\n")
            self.assertTrue(recovery_root(root, plan).exists())

    def test_windows_32_33_retries_at_most_three_total_attempts_only_at_unchanged_start(self):
        cases = ((32, 2, True), (33, 3, False))
        for number, failures, succeeds in cases:
            with self.subTest(winerror=number), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                plan = self.prepared(root)["plan"]
                selected = journal_entry(plan, role="manifest")["path"]
                real_replace = lifecycle_io.os.replace
                attempts = 0

                def sharing_violation(source, target):
                    nonlocal attempts
                    if self.relative_if_authorized(root, plan, target) == selected:
                        attempts += 1
                        if attempts <= failures:
                            raise windows_error(number)
                    return real_replace(source, target)

                with mock.patch.object(
                    lifecycle_io.os, "replace", side_effect=sharing_violation
                ), mock.patch("time.sleep") as sleep:
                    result = self.apply(root, plan)

                self.assertEqual(attempts, 3)
                self.assertEqual(
                    sleep.call_args_list,
                    [mock.call(0.1), mock.call(0.1)],
                )
                self.assertEqual(result["status"] == "applied", succeeds)
                self.assertEqual(result["successful_event_recorded"], succeeds)

    def test_install_order_is_manifest_archives_documents_controls_deletes_verify_event(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plan = self.prepared(root, with_documents=True)["plan"]
            entries = plan["journal_models"]["prepared"]["entries"]
            by_role = {entry["role"]: entry["path"] for entry in entries if entry["plane"] == "control"}
            expected = [
                by_role["manifest"],
                "docs/archive/README.md",
                by_role["state"],
                by_role["findings"],
                by_role["agents"],
                "docs/README.md",
                by_role["event"],
            ]
            observed = []
            real_replace = lifecycle_io.os.replace
            real_unlink = lifecycle_io.os.unlink

            def observe_replace(source, target):
                relative = self.relative_if_authorized(root, plan, target)
                if relative is not None:
                    observed.append(relative)
                return real_replace(source, target)

            def observe_unlink(target, *args, **kwargs):
                relative = self.relative_if_authorized(root, plan, target)
                if relative is not None:
                    observed.append(relative)
                return real_unlink(target, *args, **kwargs)

            with mock.patch.object(
                lifecycle_io.os, "replace", side_effect=observe_replace
            ), mock.patch.object(
                lifecycle_io.os, "unlink", side_effect=observe_unlink
            ):
                result = self.apply(root, plan)

            self.assertEqual(result["status"], "applied")
            self.assertEqual(observed, expected)

    def test_pre_event_verification_writes_verified_phase_then_event_is_commit_point(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plan = self.prepared(root)["plan"]
            event_path = journal_entry(plan, role="event")["path"]
            real_replace = lifecycle_io.os.replace
            at_commit = None

            def inspect_event_commit(source, target):
                nonlocal at_commit
                if self.relative_if_authorized(root, plan, target) == event_path:
                    at_commit = json.loads(
                        (recovery_root(root, plan) / "journal.json").read_text(
                            encoding="utf-8"
                        )
                    )
                return real_replace(source, target)

            with mock.patch.object(
                lifecycle_io.os, "replace", side_effect=inspect_event_commit
            ):
                result = self.apply(root, plan)

            self.assertEqual(result["status"], "applied")
            self.assertIsNotNone(at_commit)
            self.assertEqual(at_commit["phase"], "verified")
            self.assertEqual(
                [
                    entry["status"]
                    for entry in at_commit["entries"]
                    if entry["role"] != "event"
                ],
                ["installed"] * (len(at_commit["entries"]) - 1),
            )
            event_entry = next(
                entry for entry in at_commit["entries"] if entry["role"] == "event"
            )
            self.assertEqual(event_entry["status"], "pending")
            self.assertEqual(
                at_commit["event_commit"],
                {
                    "path": event_entry["path"],
                    "starting_digest": event_entry["start"]["digest"],
                    "result_digest": event_entry["result"]["digest"],
                },
            )

    def test_event_boundary_revalidates_every_installed_result_after_terminal(self):
        for drift in ("third", "start"):
            with self.subTest(drift=drift), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                prepared = self.prepared(root, with_documents=True)
                plan = prepared["plan"]
                changed = journal_entry(
                    plan,
                    path="docs/archive/README.md",
                )
                event = journal_entry(plan, role="event")
                real_terminal = lifecycle_io._write_terminal_marker_v3

                def terminal_then_drift(*args, **kwargs):
                    result = real_terminal(*args, **kwargs)
                    target = root / changed["path"]
                    if drift == "third":
                        target.write_bytes(b"drift after terminal before event\n")
                    else:
                        target.unlink()
                    return result

                with mock.patch.object(
                    lifecycle_io,
                    "_write_terminal_marker_v3",
                    side_effect=terminal_then_drift,
                ):
                    response = closeout.apply_response(
                        root,
                        prepared,
                        prepared["approval"],
                    )

                self.assertEqual(response["status"], "closeout-failed")
                self.assertFalse(response["successful_event_recorded"])
                self.assertFalse((root / event["path"]).exists())
                if drift == "third":
                    self.assertFalse(response["rollback"]["complete"])
                    self.assertEqual(response["rollback"]["documents"], "not-run")
                    self.assertEqual(response["rollback"]["controls"], "not-run")
                    self.assertEqual(response["rollback"]["cleanup"], "incomplete")
                    self.assertTrue(recovery_root(root, plan).exists())
                else:
                    self.assertTrue(response["rollback"]["complete"])
                    self.assertFalse(recovery_root(root, plan).exists())

    def test_event_boundary_revalidates_installed_control_after_terminal(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            prepared = self.prepared(root)
            plan = prepared["plan"]
            event = journal_entry(plan, role="event")
            real_terminal = lifecycle_io._write_terminal_marker_v3

            def terminal_then_drift(*args, **kwargs):
                result = real_terminal(*args, **kwargs)
                (root / "AGENTS.md").write_bytes(b"control drift before event\n")
                return result

            with mock.patch.object(
                lifecycle_io,
                "_write_terminal_marker_v3",
                side_effect=terminal_then_drift,
            ):
                response = closeout.apply_response(
                    root,
                    prepared,
                    prepared["approval"],
                )

            self.assertEqual(response["status"], "closeout-failed")
            self.assertFalse(response["successful_event_recorded"])
            self.assertFalse((root / event["path"]).exists())
            self.assertFalse(response["rollback"]["complete"])
            self.assertEqual(response["rollback"]["documents"], "not-run")
            self.assertEqual(response["rollback"]["controls"], "not-run")
            self.assertEqual(response["rollback"]["cleanup"], "incomplete")
            self.assertTrue(recovery_root(root, plan).exists())

    def test_verified_journal_is_never_rewritten_after_event_commit(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plan = self.prepared(root)["plan"]
            event_path = journal_entry(plan, role="event")["path"]
            real_replace = lifecycle_io.os.replace
            real_write_journal = lifecycle_io._write_active_journal_v3
            event_committed = False
            post_event_journal_writes = 0

            def observe_replace(source, target):
                nonlocal event_committed
                result = real_replace(source, target)
                if self.relative_if_authorized(root, plan, target) == event_path:
                    event_committed = True
                return result

            def observe_journal_write(recovery, journal):
                nonlocal post_event_journal_writes
                if event_committed:
                    post_event_journal_writes += 1
                return real_write_journal(recovery, journal)

            with mock.patch.object(
                lifecycle_io.os,
                "replace",
                side_effect=observe_replace,
            ), mock.patch.object(
                lifecycle_io,
                "_write_active_journal_v3",
                side_effect=observe_journal_write,
            ):
                result = self.apply(root, plan)

            self.assertEqual(result["status"], "applied")
            self.assertTrue(event_committed)
            self.assertEqual(post_event_journal_writes, 0)

    def test_post_commit_cleanup_failure_returns_committed_cleanup_incomplete_without_rollback(self):
        cleanup = self.api(lifecycle_io, "_cleanup_recovery_area_v3")
        rollback = self.api(lifecycle_io, "_rollback_recovery_v3")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plan = self.prepared(root)["plan"]
            with mock.patch.object(
                lifecycle_io,
                cleanup.__name__,
                side_effect=OSError("post-commit cleanup failure"),
            ), mock.patch.object(lifecycle_io, rollback.__name__) as rollback_call:
                result = self.apply(root, plan)

            self.assertEqual(result["status"], "closeout-committed-cleanup-incomplete")
            self.assertTrue(result["successful_event_recorded"])
            rollback_call.assert_not_called()
            event = journal_entry(plan, role="event")
            self.assertEqual(digest((root / event["path"]).read_bytes()), event["result"]["digest"])
            self.assertTrue(recovery_root(root, plan).exists())

    def test_committed_cleanup_failure_has_the_exact_init_envelope_and_finalize_binding(self):
        cleanup = self.api(lifecycle_io, "_cleanup_recovery_area_v3")
        expected_fields = {
            "schema_version",
            "status",
            "preview_id",
            "manifest_sha256",
            "transaction_id",
            "event_id",
            "corpus_transition",
            "verification",
            "rollback",
            "recovery",
            "writes",
            "partial_state",
            "user_action",
            "successful_event_recorded",
        }
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            prepared = self.prepared(root)
            with mock.patch.object(
                lifecycle_io,
                cleanup.__name__,
                side_effect=OSError("post-commit cleanup failure"),
            ):
                response = closeout.apply_response(root, prepared, prepared["approval"])

            self.assertEqual(set(response), expected_fields)
            self.assertEqual(response["schema_version"], 3)
            self.assertEqual(response["status"], "closeout-committed-cleanup-incomplete")
            self.assertEqual(response["preview_id"], prepared["preview_id"])
            self.assertEqual(response["manifest_sha256"], prepared["manifest_sha256"])
            self.assertEqual(response["transaction_id"], prepared["plan"]["transaction_id"])
            self.assertEqual(response["corpus_transition"], prepared["corpus_transition"])
            self.assertEqual(
                response["verification"],
                {
                    "exact_installed_bytes": True,
                    "event_last": True,
                    "result_corpus": True,
                    "local_map_ignored": "not-applicable",
                },
            )
            self.assertEqual(
                response["rollback"],
                {
                    "required": False,
                    "complete": True,
                    "documents": "not-required",
                    "controls": "not-required",
                    "cleanup": "incomplete",
                },
            )
            self.assertEqual(response["recovery"]["action"], "finalize")
            self.assertRegex(response["recovery"]["journal_digest"], r"^sha256:[0-9a-f]{64}$")
            self.assertRegex(
                response["recovery"]["reconciled_state_digest"],
                r"^sha256:[0-9a-f]{64}$",
            )
            self.assertEqual(response["writes"], "committed")
            self.assertEqual(response["partial_state"], "committed")
            self.assertEqual(response["user_action"], "run-doctor")
            self.assertTrue(response["successful_event_recorded"])

    def test_doctor_rejects_control_and_private_paths_forged_as_documents_without_writes(self):
        for relative, control_role in (
            (".local/private.md", "agents"),
            ("AGENTS.md", "agents"),
        ):
            with self.subTest(path=relative), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                plan = self.prepared(root)["plan"]
                lifecycle_io._prepare_recovery_area_v3(root, plan)
                recovery = recovery_root(root, plan)
                journal = copy.deepcopy(plan["journal_models"]["prepared"])
                victim = root / relative
                victim.parent.mkdir(parents=True, exist_ok=True)
                if not victim.exists():
                    victim.write_bytes(b"private user data\n")
                victim_bytes = victim.read_bytes()

                valid_preview = self.doctor_preview(root)
                self.assertEqual(valid_preview["status"], "approval-required")
                self.assertEqual(valid_preview["action"], "rollback")

                forged = next(
                    entry for entry in journal["entries"] if entry["role"] == control_role
                )
                self.forge_document_create(
                    recovery,
                    forged,
                    path=relative,
                    data=victim_bytes,
                )
                write_journal(recovery, journal)
                before = tree_snapshot(root)

                forged_preview = self.doctor_preview(root)

                self.assertEqual(forged_preview["status"], "state-conflict")
                self.assertEqual(
                    forged_preview["classification"],
                    "invalid-recovery-journal",
                )
                self.assertEqual(forged_preview["writes"], 0)
                self.assertEqual(tree_snapshot(root), before)

                response = lifecycle_io.apply_state_conflict_recovery(
                    root,
                    valid_preview,
                    approved_preview=valid_preview["approval"],
                    verification=None,
                )
                self.assertEqual(response["status"], "recovery-failed")
                self.assertEqual(response["writes"], 0)
                self.assertEqual(response["partial_state"], "none")
                self.assertEqual(tree_snapshot(root), before)
                self.assertEqual(victim.read_bytes(), victim_bytes)

    def test_journal_loader_rejects_operation_role_state_and_order_tampering(self):
        load_journal = self.api(lifecycle_io, "_load_journal_v3")

        def run_case(mutate):
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                plan = self.prepared(root, with_documents=True)["plan"]
                lifecycle_io._prepare_recovery_area_v3(root, plan)
                recovery = recovery_root(root, plan)
                journal = copy.deepcopy(plan["journal_models"]["prepared"])
                mutate(recovery, journal)
                write_journal(recovery, journal)
                with self.assertRaises(ValueError):
                    load_journal(root, recovery)

        def invalid_role(recovery, journal):
            entry = next(
                item
                for item in journal["entries"]
                if item["role"] == "recovery-archive"
            )
            entry["role"] = "document-source"

        def invalid_state(recovery, journal):
            entry = next(
                item
                for item in journal["entries"]
                if item["role"] == "recovery-archive"
            )
            entry["result"] = {
                "kind": "absent",
                "digest": ABSENT,
                "bytes": 0,
                "staged": None,
            }

        def invalid_order(recovery, journal):
            event_index = next(
                index
                for index, entry in enumerate(journal["entries"])
                if entry["role"] == "event"
            )
            order = list(range(len(journal["entries"])))
            order[event_index], order[event_index - 1] = (
                order[event_index - 1],
                order[event_index],
            )
            self.reorder_journal(recovery, journal, order)

        for label, mutate in (
            ("role", invalid_role),
            ("state", invalid_state),
            ("order", invalid_order),
        ):
            with self.subTest(tamper=label):
                run_case(mutate)

    def test_journal_loader_rejects_rewritten_backup_not_bound_to_transaction(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plan = self.prepared(root, with_documents=True)["plan"]
            installed_paths = {"docs/archive/README.md", "docs/README.md"}
            recovery, journal = self.interrupted(
                root,
                plan,
                installed_paths=installed_paths,
            )
            source = next(
                entry
                for entry in journal["entries"]
                if entry["path"] == "docs/README.md"
            )
            original_transaction_digest = journal["transaction_digest"]
            forged = b"# Forged rollback bytes\n"
            (recovery / source["start"]["backup"]).write_bytes(forged)
            source["start"]["digest"] = digest(forged)
            source["start"]["bytes"] = len(forged)
            write_journal(recovery, journal)
            before = tree_snapshot(root)

            preview = self.doctor_preview(root)

            self.assertEqual(journal["transaction_digest"], original_transaction_digest)
            self.assertEqual(preview["status"], "state-conflict")
            self.assertEqual(preview["classification"], "invalid-recovery-journal")
            self.assertEqual(preview["action"], "none")
            self.assertEqual(preview["writes"], 0)
            self.assertEqual(tree_snapshot(root), before)
            self.assertTrue(recovery.exists())

    def test_success_preserves_a_preexisting_empty_recovery_container(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            container = root / ".diataxis" / "recovery"
            container.mkdir(parents=True)
            plan = self.prepared(root)["plan"]

            result = self.apply(root, plan)

            self.assertEqual(result["status"], "applied")
            self.assertTrue(container.is_dir())
            self.assertEqual(list(container.iterdir()), [])

    def test_rollback_preflight_third_state_writes_nothing_and_preserves_all_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plan = self.prepared(root, with_documents=True)["plan"]
            installed_path = "docs/archive/README.md"
            recovery, _ = self.interrupted(
                root,
                plan,
                installed_paths={installed_path},
            )
            preview = self.doctor_preview(root)
            self.assertEqual(preview["action"], "rollback")
            (root / installed_path).write_bytes(b"third state\n")
            before = tree_snapshot(root)

            response = self.doctor_apply(root, preview)

            self.assertEqual(response["status"], "recovery-failed")
            self.assertEqual(response["writes"], 0)
            self.assertEqual(response["partial_state"], "none")
            self.assertEqual(tree_snapshot(root), before)
            self.assertTrue((recovery / "journal.json").exists())

    def test_reverse_rollback_restores_bytes_mode_mtime_absences_and_reports_three_planes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plan = self.prepared(root, with_documents=True)["plan"]
            recovery, journal = self.interrupted(
                root,
                plan,
                installed_paths={
                    entry["path"]
                    for entry in plan["journal_models"]["prepared"]["entries"]
                    if entry["role"] != "event"
                },
            )
            starts = {
                entry["path"]: copy.deepcopy(entry["start"])
                for entry in journal["entries"]
            }
            backups = {
                entry["path"]: (
                    None
                    if entry["start"]["backup"] is None
                    else (recovery / entry["start"]["backup"]).read_bytes()
                )
                for entry in journal["entries"]
            }
            preview = self.doctor_preview(root)
            self.assertEqual(preview["action"], "rollback")

            response = self.doctor_apply(root, preview)

            self.assertEqual(response["status"], "recovered")
            self.assertEqual(response["action"], "rollback")
            self.assertEqual(
                set(response["outcomes"]),
                {"documents", "controls", "cleanup"},
            )
            self.assertIn(response["outcomes"]["documents"], {"complete", "not-required"})
            self.assertIn(response["outcomes"]["controls"], {"complete", "not-required"})
            self.assertIn(response["outcomes"]["cleanup"], {"complete", "not-required"})
            for relative, start in starts.items():
                target = root / relative
                with self.subTest(path=relative):
                    if start["kind"] == "absent":
                        self.assertFalse(target.exists())
                    else:
                        metadata = target.stat()
                        self.assertEqual(target.read_bytes(), backups[relative])
                        self.assertEqual(stat.S_IMODE(metadata.st_mode), start["mode"])
                        self.assertEqual(metadata.st_mtime_ns, start["mtime_ns"])
            self.assertFalse(recovery.exists())

    def test_absent_empty_and_identity_changed_created_directories_are_handled_exactly(self):
        cleanup = self.api(lifecycle_io, "_remove_empty_created_directories_v3")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            stable = root / "stable"
            nonempty = root / "nonempty"
            changed = root / "changed"
            for directory in (stable, nonempty, changed):
                directory.mkdir()
            (nonempty / "user.txt").write_bytes(b"owned\n")
            records = {}
            for directory in (stable, nonempty, changed):
                metadata = directory.stat()
                records[directory.name] = {
                    "device": metadata.st_dev,
                    "inode": metadata.st_ino,
                }
            changed.rmdir()
            changed.mkdir()

            complete = cleanup(root, records)

            self.assertFalse(stable.exists())
            self.assertTrue(nonempty.is_dir())
            self.assertEqual((nonempty / "user.txt").read_bytes(), b"owned\n")
            self.assertTrue(changed.is_dir())
            self.assertFalse(complete)

    def test_all_retain_public_apply_reads_each_route_at_every_safety_boundary_without_replay(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            evidence = evidence_v3()
            (root / "docs").mkdir(parents=True)
            (root / "docs" / "README.md").write_bytes(b"# Documentation\n")
            initialize_git(root)

            trace = self.trace_public_apply_reads(
                root,
                evidence=evidence,
                selected_route="docs/README.md",
            )

            self.assertEqual(trace["response"]["status"], "applied")
            self.assertEqual(trace["selected_route_opens"], 6)
            self.assertEqual(
                trace["metadata_scans"],
                {"closeout": 1, "lifecycle": 4},
            )

    def test_same_path_replace_public_apply_accounts_for_live_and_recovery_body_reads(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = b"# Documentation\n"
            result = b"# Rewritten documentation\n"
            selected_route = "docs/README.md"
            target = root / selected_route
            target.parent.mkdir(parents=True)
            target.write_bytes(source)
            initialize_git(root)
            disposition = whole_file_disposition(
                selected_route,
                source,
                disposition="MIGRATED",
                target=selected_route,
                recovery=git_recovery(root, selected_route, source),
            )
            change = document_change(
                "REPLACE",
                selected_route,
                result,
                source_item_ids=[disposition["item_id"]],
            )
            evidence = evidence_v3(dispositions=[disposition])
            evidence["hot_path_bytes"]["after"]["value"] = len(result)
            evidence["hot_path_bytes"]["after"]["provenance"][0]["bytes"] = len(
                result
            )

            trace = self.trace_public_apply_reads(
                root,
                evidence=evidence,
                selected_route=selected_route,
                document_changes=[change],
            )

            self.assertEqual(trace["response"]["status"], "applied")
            self.assertEqual(trace["selected_route_opens"], 16)
            self.assertEqual(
                trace["metadata_scans"],
                {"closeout": 1, "lifecycle": 4},
            )
            document_entry = next(
                entry
                for entry in trace["prepared"]["plan"]["journal_models"][
                    "prepared"
                ]["entries"]
                if entry["plane"] == "document" and entry["path"] == selected_route
            )
            recovery_root = (
                root
                / ".diataxis"
                / "recovery"
                / trace["prepared"]["plan"]["transaction_id"]
            )
            expected_recovery_reads = {
                trace["path_key"](
                    recovery_root / document_entry["start"]["backup"]
                ): 1,
                trace["path_key"](
                    recovery_root / document_entry["result"]["staged"]
                ): 2,
                trace["path_key"](
                    recovery_root
                    / "results"
                    / f"{document_entry['index']:04d}.install"
                ): 1,
            }
            actual_recovery_reads = {
                path: trace["binary_opens"].count(path)
                for path in expected_recovery_reads
            }
            self.assertEqual(
                actual_recovery_reads,
                expected_recovery_reads,
            )
            self.assertEqual(sum(actual_recovery_reads.values()), 4)


if __name__ == "__main__":
    unittest.main()
