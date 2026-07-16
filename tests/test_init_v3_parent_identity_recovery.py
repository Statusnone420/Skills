import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / "skills" / "docs" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from _docs_checker import lifecycle_io
from tests.test_init_v3_journal import (
    journal_entry,
    prepared_fixture,
    recovery_root,
    tree_snapshot,
)


def _interrupt_after_non_event_install(root, plan):
    real_install = lifecycle_io._install_entry_v3

    def interrupt_before_event(*args, **kwargs):
        entry = args[3]
        if entry["role"] == "event":
            raise KeyboardInterrupt
        return real_install(*args, **kwargs)

    with mock.patch.object(
        lifecycle_io,
        "_install_entry_v3",
        side_effect=interrupt_before_event,
    ):
        try:
            lifecycle_io.apply_verified_closeout(
                root,
                plan,
                approved_transaction=plan["transaction_id"],
                verification=lambda: True,
            )
        except KeyboardInterrupt:
            pass
        else:
            raise AssertionError("test setup did not interrupt before event commit")
    return recovery_root(root, plan)


def _replace_directory_identity(path):
    path = Path(path)
    original = path.with_name(f"{path.name}-original")
    before = path.stat()
    path.rename(original)
    shutil.copytree(original, path, copy_function=shutil.copy2)
    shutil.rmtree(original)
    after = path.stat()
    if (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino):
        raise AssertionError("test setup did not replace the directory identity")
    return before, after


class InitV3ParentIdentityRecoveryTests(unittest.TestCase):
    def doctor_apply(self, root, preview):
        return lifecycle_io.apply_state_conflict_recovery(
            root,
            preview,
            approved_preview=preview["approval"],
            verification=None,
        )

    def test_bootstrapped_control_parent_rejects_created_identity_drift(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plan = prepared_fixture(root, with_documents=True)["plan"]
            lifecycle_io._prepare_recovery_area_v3(root, plan)
            recovery = recovery_root(root, plan)
            control = root / ".diataxis"
            before = control.stat()
            created = {
                ".diataxis": {
                    "device": before.st_dev,
                    "inode": before.st_ino,
                }
            }

            moved = root / ".diataxis-original"
            control.rename(moved)
            control.mkdir()
            (moved / "recovery").rename(control / "recovery")
            moved.rmdir()
            sentinel = control / "replacement-owner.txt"
            sentinel.write_bytes(b"user-owned replacement\n")
            after = control.stat()
            self.assertNotEqual(
                (before.st_dev, before.st_ino),
                (after.st_dev, after.st_ino),
            )

            with self.assertRaisesRegex(ValueError, "parent"):
                lifecycle_io._revalidate_recorded_parent_facts_v3(
                    root,
                    plan["journal_models"]["prepared"]["parent_facts"],
                    recovery,
                    control_directory_preexisted=False,
                    created_directories=created,
                )

            self.assertEqual(sentinel.read_bytes(), b"user-owned replacement\n")

    def test_doctor_rejects_stale_approval_after_same_byte_parent_swap_without_writes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plan = prepared_fixture(root, with_documents=True)["plan"]
            recovery = _interrupt_after_non_event_install(root, plan)
            preview = lifecycle_io.preview_state_conflict_recovery(root)
            self.assertEqual(preview["status"], "approval-required")
            self.assertEqual(preview["action"], "rollback")

            _replace_directory_identity(root / "docs")
            sentinel = root / "docs" / "replacement-owner.txt"
            sentinel.write_bytes(b"user-owned replacement\n")
            before_apply = tree_snapshot(root)

            response = self.doctor_apply(root, preview)

            self.assertEqual(response["status"], "recovery-failed")
            self.assertEqual(response["classification"], "recovery-approval-drift")
            self.assertEqual(response["writes"], 0)
            self.assertEqual(response["partial_state"], "none")
            self.assertEqual(tree_snapshot(root), before_apply)
            self.assertEqual(sentinel.read_bytes(), b"user-owned replacement\n")
            self.assertTrue((recovery / "journal.json").is_file())

    def test_rollback_stops_after_parent_swap_between_operations_and_reports_one_write(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plan = prepared_fixture(root, with_documents=True)["plan"]
            recovery = _interrupt_after_non_event_install(root, plan)
            preview = lifecycle_io.preview_state_conflict_recovery(root)
            self.assertEqual(preview["action"], "rollback")
            source_entry = journal_entry(plan, path="docs/README.md")
            archive_entry = journal_entry(plan, path="docs/archive/README.md")
            agents_entry = journal_entry(plan, role="agents")
            source_start = (recovery / source_entry["start"]["backup"]).read_bytes()
            agents_result = (recovery / agents_entry["result"]["staged"]).read_bytes()
            archive_result = (recovery / archive_entry["result"]["staged"]).read_bytes()
            real_replace = lifecycle_io.os.replace
            swapped = False

            def swap_after_first_rollback_write(source, target):
                nonlocal swapped
                result = real_replace(source, target)
                if Path(target) == root / "docs" / "README.md" and not swapped:
                    _replace_directory_identity(root / "docs")
                    (root / "docs" / "replacement-owner.txt").write_bytes(
                        b"user-owned replacement\n"
                    )
                    swapped = True
                return result

            with mock.patch.object(
                lifecycle_io.os,
                "replace",
                side_effect=swap_after_first_rollback_write,
            ):
                response = self.doctor_apply(root, preview)

            self.assertTrue(swapped)
            self.assertEqual(response["status"], "recovery-failed")
            self.assertEqual(response["writes"], 1)
            self.assertEqual(response["partial_state"], "possible")
            self.assertEqual((root / "docs" / "README.md").read_bytes(), source_start)
            self.assertEqual(
                (root / "docs" / "archive" / "README.md").read_bytes(),
                archive_result,
            )
            self.assertEqual((root / "AGENTS.md").read_bytes(), agents_result)
            self.assertEqual(
                (root / "docs" / "replacement-owner.txt").read_bytes(),
                b"user-owned replacement\n",
            )
            self.assertTrue((recovery / "journal.json").is_file())


if __name__ == "__main__":
    unittest.main()
