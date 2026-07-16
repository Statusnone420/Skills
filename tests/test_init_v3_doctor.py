import copy
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / "skills" / "docs" / "scripts"
CHECKER = SCRIPTS / "check.py"
sys.path.insert(0, str(SCRIPTS))

from _docs_checker import init_closeout as closeout
from _docs_checker import lifecycle_io
from _docs_checker import paths
from tests.test_init_v3_journal import (
    ABSENT,
    digest,
    install_journal_entry,
    prepared_fixture,
    recovery_root,
    tree_snapshot,
    windows_error,
    write_journal,
)


PREVIEW_FIELDS = {
    "schema_version",
    "mode",
    "status",
    "action",
    "transaction_id",
    "journal_digest",
    "reconciled_state_digest",
    "counts",
    "outcomes",
    "writes",
    "approval",
    "successful_event_recorded",
}
CONFLICT_FIELDS = {
    "schema_version",
    "mode",
    "status",
    "classification",
    "boundary",
    "action",
    "transaction_id",
    "journal_digest",
    "reconciled_state_digest",
    "counts",
    "outcomes",
    "writes",
    "successful_event_recorded",
}
SUCCESS_FIELDS = {
    "schema_version",
    "mode",
    "status",
    "action",
    "transaction_id",
    "journal_digest",
    "reconciled_state_digest",
    "counts",
    "outcomes",
    "writes",
    "successful_event_recorded",
}
FAILURE_FIELDS = SUCCESS_FIELDS | {"classification", "boundary", "partial_state"}
COUNT_FIELDS = {"documents", "controls", "cleanup"}
OUTCOME_FIELDS = COUNT_FIELDS


class InitV3DoctorTests(unittest.TestCase):
    def api(self, module, name):
        self.assertTrue(
            hasattr(module, name),
            f"Task 6 Doctor API is missing: {module.__name__}.{name}",
        )
        return getattr(module, name)

    def preview(self, root):
        return lifecycle_io.preview_state_conflict_recovery(root)

    def apply(self, root, preview, *, approval=None):
        return lifecycle_io.apply_state_conflict_recovery(
            root,
            preview,
            approved_preview=preview["approval"] if approval is None else approval,
            verification=None,
        )

    def make_state(self, root, state):
        prepared = prepared_fixture(root)
        plan = prepared["plan"]
        recovery = recovery_root(root, plan)
        if state == "bootstrap":
            recovery.mkdir(parents=True)
            (recovery / ".gitignore").write_bytes(b"*\n")
            (recovery / "backups").mkdir()
            (recovery / "results").mkdir()
            return prepared, recovery

        prepared_recovery = lifecycle_io._prepare_recovery_area_v3(root, plan)
        if state == "prepared":
            return prepared, recovery
        if state == "preparing":
            journal = copy.deepcopy(prepared_recovery["journal"])
            journal["phase"] = "preparing"
            write_journal(recovery, journal)
            return prepared, recovery

        journal = prepared_recovery["journal"]
        if state == "installing":
            journal["phase"] = "installing"
            first = next(entry for entry in journal["entries"] if entry["role"] != "event")
            install_journal_entry(root, recovery, first)
        elif state in {"verified", "committed"}:
            journal["phase"] = "verified"
            for entry in journal["entries"]:
                if entry["role"] != "event":
                    install_journal_entry(root, recovery, entry)
            if state == "committed":
                event = next(entry for entry in journal["entries"] if entry["role"] == "event")
                install_journal_entry(root, recovery, event)
                event["status"] = "pending"
        else:
            raise AssertionError(f"unknown fixture state {state}")
        for fact in journal["parent_facts"]:
            target = root if fact["path"] == "." else root / fact["path"]
            if fact["starting_kind"] == "absent" and target.is_dir():
                metadata = target.stat()
                journal["created_parent_identities"][fact["path"]] = {
                    "device": metadata.st_dev,
                    "inode": metadata.st_ino,
                }
        write_journal(recovery, journal)
        if state in {"verified", "committed"}:
            lifecycle_io._write_terminal_marker_v3(
                root,
                recovery,
                journal,
                digest((recovery / "journal.json").read_bytes()),
            )
        return prepared, recovery

    def assert_exact_preview(self, response, *, action, committed):
        self.assertEqual(set(response), PREVIEW_FIELDS)
        self.assertEqual(response["schema_version"], 3)
        self.assertEqual(response["mode"], "state-conflict-recovery")
        self.assertEqual(response["status"], "approval-required")
        self.assertEqual(response["action"], action)
        self.assertRegex(response["transaction_id"], r"^TXN-[0-9A-F]{16}$")
        self.assertRegex(
            response["journal_digest"],
            r"^sha256:(?:[0-9a-f]{64}|ABSENT)$",
        )
        self.assertRegex(
            response["reconciled_state_digest"],
            r"^sha256:[0-9a-f]{64}$",
        )
        self.assertEqual(set(response["counts"]), COUNT_FIELDS)
        self.assertTrue(
            all(type(value) is int and value >= 0 for value in response["counts"].values())
        )
        self.assertEqual(
            response["outcomes"],
            {"documents": "not-run", "controls": "not-run", "cleanup": "not-run"},
        )
        self.assertEqual(response["writes"], 0)
        journal_token = response["journal_digest"].removeprefix("sha256:")
        state_token = response["reconciled_state_digest"].removeprefix("sha256:")
        self.assertEqual(
            response["approval"],
            "Approve $docs doctor recovery "
            f"{response['transaction_id']} with journal {journal_token} "
            f"state {state_token} action {action}",
        )
        self.assertIs(response["successful_event_recorded"], committed)
        self.assertNotIn("reconciled_states", response)

    def test_doctor_classifies_bootstrap_preparing_prepared_installing_verified_and_committed_states(self):
        expected = {
            "bootstrap": ("cleanup", False),
            "preparing": ("cleanup", False),
            "prepared": ("rollback", False),
            "installing": ("rollback", False),
            "verified": ("rollback", False),
            "committed": ("finalize", True),
        }
        for state, (action, committed) in expected.items():
            with self.subTest(state=state), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                prepared, recovery = self.make_state(root, state)
                before = tree_snapshot(root)

                response = self.preview(root)

                self.assert_exact_preview(response, action=action, committed=committed)
                self.assertEqual(response["transaction_id"], prepared["plan"]["transaction_id"])
                if state == "bootstrap":
                    self.assertEqual(response["journal_digest"], ABSENT)
                else:
                    self.assertEqual(
                        response["journal_digest"],
                        digest((recovery / "journal.json").read_bytes()),
                    )
                self.assertEqual(tree_snapshot(root), before)

                with mock.patch.object(
                    closeout,
                    "scan_selected_document_corpus",
                    side_effect=AssertionError("Init preflight must route to Doctor without corpus traversal"),
                ):
                    preflight = closeout.inspect_initialization_preflight(root)
                self.assertEqual(preflight["status"], "state-conflict")
                self.assertEqual(preflight["user_action"], "run-doctor")
                self.assertEqual(preflight["candidate_traversal"], 0)
                self.assertEqual(preflight["content_reads"], 0)

    def test_doctor_cleans_partial_first_journal_bootstrap_without_target_writes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, recovery = self.make_state(root, "bootstrap")
            (recovery / "journal.next").write_bytes(b'{"partial":')
            before_targets = {
                path.relative_to(root).as_posix(): path.read_bytes()
                for path in root.rglob("*")
                if path.is_file() and ".diataxis/recovery/" not in path.as_posix()
            }

            preview = self.preview(root)

            self.assert_exact_preview(preview, action="cleanup", committed=False)
            recovered = self.apply(root, preview)
            self.assertEqual(recovered["status"], "recovered")
            self.assertEqual(recovered["action"], "cleanup")
            self.assertFalse(recovery.exists())
            after_targets = {
                path.relative_to(root).as_posix(): path.read_bytes()
                for path in root.rglob("*")
                if path.is_file() and ".diataxis/recovery/" not in path.as_posix()
            }
            self.assertEqual(after_targets, before_targets)

    def test_doctor_rejects_missing_tampered_or_reparse_recovery_guard_without_writes(self):
        for variant in ("missing", "tampered", "reparse"):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                _, recovery = self.make_state(root, "prepared")
                guard = recovery / ".gitignore"
                if variant == "missing":
                    guard.unlink()
                elif variant == "tampered":
                    guard.write_bytes(b"!results/**\n")
                before = tree_snapshot(root)

                if variant == "reparse":
                    real_is_reparse = paths._is_reparse

                    def mark_guard_reparse(path):
                        return Path(path).absolute() == guard.absolute() or real_is_reparse(path)

                    with mock.patch.object(
                        paths,
                        "_is_reparse",
                        side_effect=mark_guard_reparse,
                    ):
                        conflict = self.preview(root)
                else:
                    conflict = self.preview(root)

                self.assertEqual(set(conflict), CONFLICT_FIELDS)
                self.assertEqual(conflict["status"], "state-conflict")
                self.assertEqual(conflict["classification"], "invalid-recovery-layout")
                self.assertEqual(conflict["boundary"], "recovery-layout")
                self.assertEqual(conflict["writes"], 0)
                self.assertNotIn("approval", conflict)
                self.assertEqual(tree_snapshot(root), before)

    def test_guard_only_cleanup_retry_preserves_preexisting_recovery_container(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            prepared = prepared_fixture(root)
            container = root / ".diataxis" / "recovery"
            container.mkdir(parents=True)
            recovery = container / prepared["plan"]["transaction_id"]
            recovery.mkdir()
            (recovery / ".gitignore").write_bytes(b"*\n")
            preview = self.preview(root)
            self.assert_exact_preview(preview, action="cleanup", committed=False)
            real_unlink = lifecycle_io._unlink_cleanup_child_v3

            def stop_at_guard(pin, name):
                if name == ".gitignore":
                    raise OSError("injected guard cleanup interruption")
                return real_unlink(pin, name)

            with mock.patch.object(
                lifecycle_io,
                "_unlink_cleanup_child_v3",
                side_effect=stop_at_guard,
            ):
                failed = self.apply(root, preview)

            self.assertEqual(failed["status"], "recovery-failed")
            self.assertEqual(failed["writes"], 1)
            tombstone = container / f"{prepared['plan']['transaction_id']}.cleanup"
            self.assertEqual((tombstone / ".gitignore").read_bytes(), b"*\n")
            ignored = subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "check-ignore",
                    "-q",
                    "--no-index",
                    "--",
                    (tombstone / "results" / "0000.bin").relative_to(root).as_posix(),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(ignored.returncode, 0, ignored.stderr)
            status = subprocess.run(
                ["git", "-C", str(root), "status", "--short", "--untracked-files=all"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(status.returncode, 0, status.stderr)
            self.assertNotIn(".diataxis/recovery/", status.stdout.replace("\\", "/"))

            retry = self.preview(root)
            self.assert_exact_preview(retry, action="cleanup", committed=False)
            recovered = self.apply(root, retry)
            self.assertEqual(recovered["status"], "recovered")
            self.assertFalse(tombstone.exists())
            self.assertTrue(container.is_dir())
            self.assertEqual(list(container.iterdir()), [])

    def test_checker_exposes_exact_doctor_recovery_preview_and_apply(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, recovery = self.make_state(root, "installing")
            preview_process = subprocess.run(
                [
                    sys.executable,
                    str(CHECKER),
                    str(root),
                    "--doctor-recovery-preview",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(preview_process.returncode, 0, preview_process.stderr)
            preview = json.loads(preview_process.stdout)
            self.assertEqual(preview["status"], "approval-required")
            self.assertEqual(preview["action"], "rollback")

            apply_process = subprocess.run(
                [
                    sys.executable,
                    str(CHECKER),
                    str(root),
                    "--doctor-recovery-apply",
                    preview["approval"],
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(apply_process.returncode, 0, apply_process.stderr)
            applied = json.loads(apply_process.stdout)
            self.assertEqual(applied["status"], "recovered")
            self.assertEqual(applied["action"], "rollback")
            self.assertFalse(recovery.exists())

    @unittest.skipUnless(sys.platform == "win32", "Windows PowerShell transport test")
    def test_documented_doctor_apply_command_preserves_exact_approval_in_powershell(self):
        doctor = (SCRIPTS.parent / "references" / "doctor.md").read_text(encoding="utf-8")
        documented = re.search(
            r"--doctor-recovery-apply (?P<quote>['\"])<exact-approval-string>(?P=quote)",
            doctor,
        )
        self.assertIsNotNone(documented, "Doctor must document one quoted approval argument")

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, recovery = self.make_state(root, "installing")
            preview = self.preview(root)

            def powershell_literal(value):
                return "'" + str(value).replace("'", "''") + "'"

            approval = (
                documented.group("quote")
                + preview["approval"]
                + documented.group("quote")
            )
            command = " ".join(
                (
                    "&",
                    powershell_literal(sys.executable),
                    powershell_literal(CHECKER),
                    powershell_literal(root),
                    "--doctor-recovery-apply",
                    approval,
                )
            )
            process = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(process.returncode, 0, process.stderr or process.stdout)
            applied = json.loads(process.stdout)
            self.assertEqual(applied["status"], "recovered")
            self.assertEqual(applied["action"], "rollback")
            self.assertFalse(recovery.exists())

    def test_doctor_recovery_approval_binds_transaction_journal_digest_action_and_reconciled_states(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, recovery = self.make_state(root, "prepared")
            preview = self.preview(root)
            before = tree_snapshot(root)
            tokens = preview["approval"].split()
            invalid = []
            candidate = list(tokens)
            candidate[4] = "TXN-0000000000000000"
            invalid.append(" ".join(candidate))
            candidate = list(tokens)
            candidate[7] = "0" * 64
            invalid.append(" ".join(candidate))
            candidate = list(tokens)
            candidate[9] = "1" * 64
            invalid.append(" ".join(candidate))
            candidate = list(tokens)
            candidate[11] = "finalize"
            invalid.append(" ".join(candidate))

            for approval in invalid:
                with self.subTest(approval=approval):
                    with self.assertRaises(ValueError):
                        self.apply(root, preview, approval=approval)
                    self.assertEqual(tree_snapshot(root), before)

            journal = json.loads((recovery / "journal.json").read_text(encoding="utf-8"))
            selected = journal["entries"][0]
            target = root / selected["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"live state changed after approval\n")
            drifted = tree_snapshot(root)

            response = self.apply(root, preview)

            self.assertEqual(response["status"], "recovery-failed")
            self.assertEqual(response["writes"], 0)
            self.assertEqual(response["partial_state"], "none")
            self.assertEqual(tree_snapshot(root), drifted)

    def test_doctor_rejects_structured_action_substitution_with_genuine_approval(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, recovery = self.make_state(root, "installing")
            preview = self.preview(root)
            self.assertEqual(preview["action"], "rollback")
            forged = copy.deepcopy(preview)
            forged["action"] = "cleanup"
            before = tree_snapshot(root)

            response = self.apply(root, forged)

            self.assertEqual(response["status"], "recovery-failed")
            self.assertEqual(response["classification"], "recovery-approval-drift")
            self.assertEqual(response["writes"], 0)
            self.assertEqual(response["partial_state"], "none")
            self.assertEqual(tree_snapshot(root), before)
            self.assertTrue(recovery.exists())

    def test_doctor_rollback_guard_tamper_after_revalidation_writes_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, recovery = self.make_state(root, "installing")
            preview = self.preview(root)
            self.assertEqual(preview["action"], "rollback")
            journal = json.loads((recovery / "journal.json").read_bytes())
            installed = next(
                entry for entry in journal["entries"] if entry["status"] == "installed"
            )
            target = root / installed["path"]
            target_before = target.read_bytes() if target.is_file() else None
            event = next(
                entry for entry in journal["entries"] if entry["role"] == "event"
            )
            event_path = root / event["path"]
            event_before = event_path.read_bytes() if event_path.is_file() else None
            guard = recovery / ".gitignore"
            real_load = lifecycle_io._load_journal_v3
            load_calls = 0

            def load_then_tamper(*args, **kwargs):
                nonlocal load_calls
                loaded = real_load(*args, **kwargs)
                load_calls += 1
                if load_calls == 2:
                    guard.write_bytes(b"!\n")
                return loaded

            with mock.patch.object(
                lifecycle_io,
                "_load_journal_v3",
                side_effect=load_then_tamper,
            ):
                response = self.apply(root, preview)

            self.assertEqual(load_calls, 2)
            self.assertEqual(response["status"], "recovery-failed")
            self.assertEqual(response["writes"], 0)
            self.assertEqual(response["partial_state"], "none")
            self.assertFalse(response["successful_event_recorded"])
            self.assertEqual(
                target.read_bytes() if target.is_file() else None,
                target_before,
            )
            self.assertEqual(
                event_path.read_bytes() if event_path.is_file() else None,
                event_before,
            )
            self.assertEqual(guard.read_bytes(), b"!\n")

    def test_doctor_preview_conflict_success_and_failure_envelopes_have_exact_fields(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, recovery = self.make_state(root, "prepared")
            journal = json.loads((recovery / "journal.json").read_text(encoding="utf-8"))
            selected = journal["entries"][0]
            target = root / selected["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"third state\n")
            before = tree_snapshot(root)

            conflict = self.preview(root)

            self.assertEqual(set(conflict), CONFLICT_FIELDS)
            self.assertEqual(conflict["schema_version"], 3)
            self.assertEqual(conflict["mode"], "state-conflict-recovery")
            self.assertEqual(conflict["status"], "state-conflict")
            self.assertEqual(conflict["action"], "none")
            self.assertEqual(conflict["writes"], 0)
            self.assertFalse(conflict["successful_event_recorded"])
            self.assertEqual(set(conflict["counts"]), COUNT_FIELDS)
            self.assertEqual(set(conflict["outcomes"]), OUTCOME_FIELDS)
            self.assertTrue(
                all(
                    outcome in {"not-run", "complete", "incomplete", "unknown"}
                    for outcome in conflict["outcomes"].values()
                )
            )
            self.assertNotIn("approval", conflict)
            self.assertEqual(tree_snapshot(root), before)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, recovery = self.make_state(root, "bootstrap")
            preview = self.preview(root)

            success = self.apply(root, preview)

            self.assertEqual(set(success), SUCCESS_FIELDS)
            self.assertEqual(success["schema_version"], 3)
            self.assertEqual(success["mode"], "state-conflict-recovery")
            self.assertEqual(success["status"], "recovered")
            self.assertEqual(success["action"], "cleanup")
            self.assertIs(type(success["writes"]), int)
            self.assertGreaterEqual(success["writes"], 0)
            self.assertEqual(set(success["counts"]), COUNT_FIELDS)
            self.assertTrue(
                all(
                    outcome in {"complete", "not-required"}
                    for outcome in success["outcomes"].values()
                )
            )
            self.assertFalse(success["successful_event_recorded"])
            self.assertFalse(recovery.exists())

        cleanup = self.api(lifecycle_io, "_cleanup_recovery_area_v3")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, recovery = self.make_state(root, "bootstrap")
            preview = self.preview(root)
            before = tree_snapshot(root)
            with mock.patch.object(
                lifecycle_io,
                cleanup.__name__,
                side_effect=OSError("injected recovery cleanup failure"),
            ):
                failure = self.apply(root, preview)

            self.assertEqual(set(failure), FAILURE_FIELDS)
            self.assertEqual(failure["schema_version"], 3)
            self.assertEqual(failure["mode"], "state-conflict-recovery")
            self.assertEqual(failure["status"], "recovery-failed")
            self.assertEqual(failure["action"], "cleanup")
            self.assertIn(failure["writes"], {0, "unknown"})
            self.assertIn(failure["partial_state"], {"none", "possible", "committed"})
            self.assertFalse(failure["successful_event_recorded"])
            self.assertEqual(set(failure["counts"]), COUNT_FIELDS)
            self.assertEqual(set(failure["outcomes"]), OUTCOME_FIELDS)
            self.assertTrue(
                all(
                    outcome in {"not-run", "complete", "incomplete", "unknown"}
                    for outcome in failure["outcomes"].values()
                )
            )
            self.assertEqual(tree_snapshot(root), before)
            self.assertTrue(recovery.exists())

    def test_partial_finalize_cleanup_is_bounded_reported_and_restart_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            prepared, recovery = self.make_state(root, "committed")
            preview = self.preview(root)
            self.assertEqual(preview["action"], "finalize")
            real_unlink = lifecycle_io._unlink_cleanup_child_v3
            calls = 0

            def delete_one_then_share_violate(pin, name):
                nonlocal calls
                calls += 1
                if calls == 1:
                    return real_unlink(pin, name)
                raise windows_error(32)

            with mock.patch.object(
                lifecycle_io,
                "_unlink_cleanup_child_v3",
                side_effect=delete_one_then_share_violate,
            ), mock.patch("time.sleep"):
                failed = self.apply(root, preview)

            self.assertEqual(failed["status"], "recovery-failed")
            self.assertGreaterEqual(failed["writes"], 1)
            self.assertEqual(failed["partial_state"], "committed")
            self.assertTrue(failed["successful_event_recorded"])
            tombstone = recovery.with_name(
                f"{prepared['plan']['transaction_id']}.finalize"
            )
            self.assertFalse(recovery.exists())
            self.assertTrue(tombstone.is_dir())

            retry = self.preview(root)
            self.assertEqual(retry["status"], "approval-required")
            self.assertEqual(retry["action"], "finalize")
            self.assertTrue(retry["successful_event_recorded"])
            recovered = self.apply(root, retry)
            self.assertEqual(recovered["status"], "recovered")
            self.assertEqual(recovered["action"], "finalize")
            self.assertTrue(recovered["successful_event_recorded"])
            self.assertFalse(tombstone.exists())
            self.assertFalse((root / ".diataxis" / "recovery").exists())

    def test_cleanup_rejects_windows_reparse_children_without_deleting_them(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, recovery = self.make_state(root, "bootstrap")
            preview = self.preview(root)
            external = root / "outside-recovery" / "0000.bin"
            external.parent.mkdir()
            external.write_bytes(b"external sentinel\n")
            before = tree_snapshot(root)
            reparse = (recovery / "backups").absolute()
            real_is_reparse = paths._is_reparse

            def mark_reparse(path):
                return Path(path).absolute() == reparse or real_is_reparse(path)

            with mock.patch.object(
                paths,
                "_is_reparse",
                side_effect=mark_reparse,
            ):
                failed = self.apply(root, preview)

            self.assertEqual(failed["status"], "recovery-failed")
            self.assertEqual(failed["writes"], 0)
            self.assertEqual(failed["partial_state"], "none")
            self.assertEqual(tree_snapshot(root), before)
            self.assertEqual(external.read_bytes(), b"external sentinel\n")
            self.assertTrue(recovery.exists())

    def test_cleanup_revalidates_each_artifact_immediately_before_deletion(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            prepared, recovery = self.make_state(root, "preparing")
            body_relative = min(prepared["plan"]["recovery_files"])
            body = recovery / body_relative
            self.assertTrue(body.is_file())
            preview = self.preview(root)
            self.assertEqual(preview["action"], "cleanup")
            real_is_reparse = paths._is_reparse
            tombstone = recovery.with_name(
                f"{prepared['plan']['transaction_id']}.cleanup"
            )

            def swap_after_layout_validation(path):
                candidate = Path(path)
                if (
                    candidate.name == body.name
                    and candidate.parent.name == body.parent.name
                    and tombstone.exists()
                ):
                    return True
                return real_is_reparse(path)

            with mock.patch.object(
                paths,
                "_is_reparse",
                side_effect=swap_after_layout_validation,
            ):
                failed = self.apply(root, preview)

            self.assertEqual(failed["status"], "recovery-failed")
            self.assertEqual(failed["writes"], 1)
            self.assertEqual(failed["partial_state"], "possible")
            self.assertTrue((tombstone / body_relative).is_file())

            retry = self.preview(root)
            self.assertEqual(retry["status"], "approval-required")
            self.assertEqual(retry["action"], "cleanup")
            recovered = self.apply(root, retry)
            self.assertEqual(recovered["status"], "recovered")
            self.assertEqual(recovered["action"], "cleanup")
            self.assertFalse(tombstone.exists())

    def test_cleanup_rejects_case_insensitive_tombstone_collisions(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            prepared, recovery = self.make_state(root, "bootstrap")
            transaction_id = prepared["plan"]["transaction_id"]
            collision = recovery.with_name(f"{transaction_id}.CLEANUP")
            collision.mkdir()
            before = tree_snapshot(root)

            with self.assertRaises(ValueError):
                lifecycle_io._cleanup_recovery_area_v3(
                    root,
                    recovery,
                    action="cleanup",
                )

            self.assertEqual(tree_snapshot(root), before)

    def test_cleanup_reports_rename_when_first_pinned_directory_removal_fails(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            prepared, recovery = self.make_state(root, "bootstrap")
            preview = self.preview(root)
            with mock.patch.object(
                lifecycle_io,
                "_remove_pinned_directory_v3",
                side_effect=OSError("injected pinned-directory cleanup failure"),
            ):
                failed = self.apply(root, preview)

            self.assertEqual(failed["status"], "recovery-failed")
            self.assertEqual(failed["writes"], 1)
            self.assertEqual(failed["partial_state"], "possible")
            tombstone = recovery.with_name(
                f"{prepared['plan']['transaction_id']}.cleanup"
            )
            self.assertTrue(tombstone.is_dir())


if __name__ == "__main__":
    unittest.main()
