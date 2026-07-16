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

from _docs_checker import init_closeout as closeout
from _docs_checker import lifecycle_io
from tests.test_init_v3_journal import prepared_fixture, recovery_root


class InitV3TerminalCleanupTests(unittest.TestCase):
    def _commit_without_cleanup(self, root, *, with_documents=False):
        prepared = prepared_fixture(root, with_documents=with_documents)
        observed = {}
        real_terminal_write = lifecycle_io._write_terminal_marker_v3

        def observe_terminal_before_event(*args, **kwargs):
            journal = args[2]
            event = next(entry for entry in journal["entries"] if entry["role"] == "event")
            _, classification = lifecycle_io._classify_live_entry_v3(root, event)
            self.assertEqual(classification, "start")
            observed["terminal_writes"] = observed.get("terminal_writes", 0) + 1
            return real_terminal_write(*args, **kwargs)

        def stop_cleanup(cleanup_root, recovery, *, action):
            terminal = Path(recovery) / "terminal.json"
            observed["exists"] = terminal.is_file()
            observed["bytes"] = terminal.read_bytes() if terminal.is_file() else None
            raise OSError("injected cleanup stop")

        with mock.patch.object(
            lifecycle_io,
            "_write_terminal_marker_v3",
            side_effect=observe_terminal_before_event,
        ), mock.patch.object(
            lifecycle_io,
            "_cleanup_recovery_area_v3",
            side_effect=stop_cleanup,
        ):
            response = lifecycle_io.apply_verified_closeout(
                root,
                prepared["plan"],
                approved_transaction=prepared["plan"]["transaction_id"],
                verification=lambda: True,
            )
        self.assertEqual(response["status"], "closeout-committed-cleanup-incomplete")
        self.assertTrue(response["successful_event_recorded"])
        self.assertEqual(observed.get("terminal_writes"), 1)
        return prepared, recovery_root(root, prepared["plan"]), observed

    def test_terminal_marker_is_canonical_body_free_and_exists_before_event_cleanup(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            prepared, recovery, observed = self._commit_without_cleanup(
                root,
                with_documents=True,
            )

            self.assertTrue(observed["exists"])
            marker = json.loads(observed["bytes"])
            self.assertEqual(
                observed["bytes"],
                (
                    json.dumps(
                        marker,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=True,
                        allow_nan=False,
                    )
                    + "\n"
                ).encode("utf-8"),
            )
            self.assertEqual(marker["transaction_id"], prepared["plan"]["transaction_id"])
            self.assertEqual(marker["transaction_digest"], prepared["plan"]["transaction_digest"])
            self.assertIn("created_parent_identities", marker)
            self.assertTrue(marker["created_parent_identities"])
            serialized = observed["bytes"]
            for forbidden in (b'"backup"', b'"staged"', b'"result_bytes"'):
                self.assertNotIn(forbidden, serialized)
            self.assertTrue((recovery / "terminal.json").is_file())

    def test_finalize_revalidates_event_and_every_result_before_any_cleanup_write(self):
        for target_kind in ("event", "document-result"):
            with self.subTest(target=target_kind), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                prepared, recovery, _ = self._commit_without_cleanup(
                    root,
                    with_documents=True,
                )
                plan = prepared["plan"]
                if target_kind == "event":
                    entry = next(
                        item
                        for item in plan["journal_models"]["prepared"]["entries"]
                        if item["role"] == "event"
                    )
                else:
                    entry = next(
                        item
                        for item in plan["journal_models"]["prepared"]["entries"]
                        if item["role"] == "recovery-archive"
                    )
                target = root / entry["path"]
                target.write_bytes(b"tampered after committed cleanup interruption\n")
                before = {
                    path.relative_to(root).as_posix(): path.read_bytes()
                    for path in root.rglob("*")
                    if path.is_file()
                }

                preview = lifecycle_io.preview_state_conflict_recovery(root)

                self.assertEqual(preview["status"], "state-conflict")
                self.assertEqual(preview["writes"], 0)
                self.assertTrue((recovery / "terminal.json").is_file())
                after = {
                    path.relative_to(root).as_posix(): path.read_bytes()
                    for path in root.rglob("*")
                    if path.is_file()
                }
                self.assertEqual(after, before)

    def test_active_journal_with_terminal_requires_exact_marker_binding(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, recovery, _ = self._commit_without_cleanup(root)
            terminal = recovery / "terminal.json"
            marker = json.loads(terminal.read_bytes())
            marker["journal_digest"] = "sha256:" + "0" * 64
            terminal.write_bytes(
                (
                    json.dumps(
                        marker,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=True,
                        allow_nan=False,
                    )
                    + "\n"
                ).encode("utf-8")
            )

            preview = lifecycle_io.preview_state_conflict_recovery(root)

            self.assertEqual(preview["status"], "state-conflict")
            self.assertEqual(preview["classification"], "invalid-recovery-journal")
            self.assertEqual(preview["writes"], 0)
            self.assertTrue(terminal.is_file())

    def test_recovery_discovery_reads_at_most_three_children(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            container = root / ".diataxis" / "recovery"
            container.mkdir(parents=True)
            children = [container / f"TXN-{index:016X}" for index in range(4)]
            for child in children:
                child.mkdir()
            real_iterdir = Path.iterdir
            yielded = 0

            def guarded_iterdir(path):
                nonlocal yielded
                if Path(path) != container:
                    yield from real_iterdir(path)
                    return
                for child in children:
                    yielded += 1
                    if yielded > 3:
                        raise AssertionError("recovery discovery was unbounded")
                    yield child

            with mock.patch.object(Path, "iterdir", guarded_iterdir):
                with self.assertRaises(ValueError):
                    lifecycle_io._find_recovery_root_v3(root)

            self.assertEqual(yielded, 3)

    def test_recovery_discovery_rejects_active_beside_terminal_without_writes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            container = root / ".diataxis" / "recovery"
            container.mkdir(parents=True)
            active = container / "TXN-0000000000000001"
            terminal = container / "TXN-0000000000000002.finalize"
            active.mkdir()
            terminal.mkdir()
            before = sorted(
                path.relative_to(root).as_posix() for path in root.rglob("*")
            )

            with self.assertRaisesRegex(ValueError, "ambiguous"):
                lifecycle_io._find_recovery_root_v3(root)
            preview = lifecycle_io._preview_journal_recovery_v3(root)

            self.assertEqual(preview["status"], "state-conflict")
            self.assertEqual(preview["action"], "none")
            self.assertEqual(preview["boundary"], "recovery-discovery")
            self.assertEqual(preview["writes"], 0)
            self.assertEqual(
                sorted(path.relative_to(root).as_posix() for path in root.rglob("*")),
                before,
            )

    @unittest.skipUnless(os.name == "nt", "Windows directory-handle contract")
    def test_windows_cleanup_tree_pins_root_and_children_against_replacement(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            prepared = prepared_fixture(root)
            lifecycle_io._prepare_recovery_area_v3(root, prepared["plan"])
            recovery = recovery_root(root, prepared["plan"])
            external = root / "outside"
            external.mkdir()
            sentinel = external / "sentinel.txt"
            sentinel.write_bytes(b"external user data\n")

            tree = lifecycle_io._open_cleanup_tree_v3(
                recovery.parent,
                recovery,
                "cleanup",
            )
            try:
                tombstone = tree["path"]
                for candidate in (
                    tombstone,
                    tombstone / "backups",
                    tombstone / "results",
                ):
                    replacement = candidate.with_name(candidate.name + ".swapped")
                    with self.assertRaises(OSError) as raised:
                        os.replace(candidate, replacement)
                    self.assertIn(getattr(raised.exception, "winerror", None), {32, 33})
            finally:
                lifecycle_io._close_cleanup_tree_v3(tree)

            self.assertEqual(sentinel.read_bytes(), b"external user data\n")

    def test_partial_finalize_preserves_terminal_last_and_doctor_retry_is_exact(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            prepared, recovery, _ = self._commit_without_cleanup(
                root,
                with_documents=True,
            )
            preview = lifecycle_io.preview_state_conflict_recovery(root)
            self.assertEqual(preview["action"], "finalize")
            real_unlink = lifecycle_io._unlink_cleanup_child_v3
            calls = 0

            def delete_one_then_stop(pin, name):
                nonlocal calls
                calls += 1
                if calls == 1:
                    return real_unlink(pin, name)
                error = OSError("injected sharing violation")
                error.winerror = 32
                raise error

            with mock.patch.object(
                lifecycle_io,
                "_unlink_cleanup_child_v3",
                side_effect=delete_one_then_stop,
            ), mock.patch("time.sleep"):
                failed = lifecycle_io.apply_state_conflict_recovery(
                    root,
                    preview,
                    approved_preview=preview["approval"],
                    verification=None,
                )

            self.assertEqual(failed["status"], "recovery-failed")
            self.assertTrue(failed["successful_event_recorded"])
            tombstone = recovery.with_name(
                f"{prepared['plan']['transaction_id']}.finalize"
            )
            self.assertTrue((tombstone / "terminal.json").is_file())

            retry = lifecycle_io.preview_state_conflict_recovery(root)
            self.assertEqual(retry["status"], "approval-required")
            self.assertEqual(retry["action"], "finalize")
            recovered = lifecycle_io.apply_state_conflict_recovery(
                root,
                retry,
                approved_preview=retry["approval"],
                verification=None,
            )
            self.assertEqual(recovered["status"], "recovered")
            self.assertFalse(tombstone.exists())
            self.assertFalse((root / ".diataxis" / "recovery").exists())

    def test_markerless_cleanup_tombstone_with_intact_journal_is_retryable(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            prepared = prepared_fixture(root)
            lifecycle_io._prepare_recovery_area_v3(root, prepared["plan"])
            recovery = recovery_root(root, prepared["plan"])

            with mock.patch.object(
                lifecycle_io,
                "_unlink_cleanup_child_v3",
                side_effect=OSError("injected cleanup stop"),
            ):
                with self.assertRaises(lifecycle_io._V3CleanupFailure):
                    lifecycle_io._cleanup_recovery_area_v3(
                        root,
                        recovery,
                        action="cleanup",
                    )

            tombstone = recovery.with_name(
                f"{prepared['plan']['transaction_id']}.cleanup"
            )
            self.assertTrue((tombstone / "journal.json").is_file())
            preview = lifecycle_io.preview_state_conflict_recovery(root)
            self.assertEqual(preview["status"], "approval-required")
            self.assertEqual(preview["action"], "cleanup")

            recovered = lifecycle_io.apply_state_conflict_recovery(
                root,
                preview,
                approved_preview=preview["approval"],
                verification=None,
            )

            self.assertEqual(recovered["status"], "recovered")
            self.assertFalse(tombstone.exists())

    def test_markerless_empty_finalize_requires_live_commit_validation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            prepared, recovery, _ = self._commit_without_cleanup(root)
            preview = lifecycle_io.preview_state_conflict_recovery(root)
            event_entry = next(
                entry
                for entry in prepared["plan"]["journal_models"]["prepared"]["entries"]
                if entry["role"] == "event"
            )
            event_path = root / event_entry["path"]
            committed_event = event_path.read_bytes()

            with mock.patch.object(
                lifecycle_io,
                "_remove_pinned_directory_v3",
                side_effect=OSError("injected empty-directory cleanup failure"),
            ):
                failed = lifecycle_io.apply_state_conflict_recovery(
                    root,
                    preview,
                    approved_preview=preview["approval"],
                    verification=None,
                )

            self.assertEqual(failed["status"], "recovery-failed")
            tombstone = recovery.with_name(
                f"{prepared['plan']['transaction_id']}.finalize"
            )
            self.assertTrue(tombstone.is_dir())
            self.assertFalse((tombstone / "terminal.json").exists())

            event_path.write_bytes(b"corrupt committed event\n")
            blocked = lifecycle_io.preview_state_conflict_recovery(root)
            self.assertEqual(blocked["status"], "state-conflict")
            self.assertEqual(blocked["writes"], 0)
            self.assertTrue(tombstone.is_dir())

            event_path.write_bytes(committed_event)
            retry = lifecycle_io.preview_state_conflict_recovery(root)
            self.assertEqual(retry["status"], "approval-required")
            self.assertEqual(retry["action"], "finalize")
            recovered = lifecycle_io.apply_state_conflict_recovery(
                root,
                retry,
                approved_preview=retry["approval"],
                verification=None,
            )
            self.assertEqual(recovered["status"], "recovered")
            self.assertFalse(tombstone.exists())

    def test_cleanup_suffix_cannot_be_renamed_into_false_finalize_truth(self):
        from tests.test_init_v3_doctor import InitV3DoctorTests

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fixture = InitV3DoctorTests(methodName="runTest")
            prepared, recovery = fixture.make_state(root, "bootstrap")
            preview = lifecycle_io.preview_state_conflict_recovery(root)
            with mock.patch.object(
                lifecycle_io,
                "_remove_pinned_directory_v3",
                side_effect=OSError("injected empty-directory cleanup failure"),
            ):
                failed = lifecycle_io.apply_state_conflict_recovery(
                    root,
                    preview,
                    approved_preview=preview["approval"],
                    verification=None,
                )
            self.assertEqual(failed["status"], "recovery-failed")
            cleanup = recovery.with_name(
                f"{prepared['plan']['transaction_id']}.cleanup"
            )
            finalize = recovery.with_name(
                f"{prepared['plan']['transaction_id']}.finalize"
            )
            cleanup.rename(finalize)

            spoofed = lifecycle_io.preview_state_conflict_recovery(root)

            self.assertEqual(spoofed["status"], "state-conflict")
            self.assertFalse(spoofed["successful_event_recorded"])
            self.assertEqual(spoofed["writes"], 0)
            self.assertTrue(finalize.is_dir())

    def test_posix_cleanup_capability_gate_matches_rename_primitive(self):
        supported = {
            lifecycle_io.os.open,
            lifecycle_io.os.stat,
            lifecycle_io.os.unlink,
            lifecycle_io.os.rmdir,
            lifecycle_io.os.rename,
        }
        with mock.patch.object(
            lifecycle_io.os,
            "supports_dir_fd",
            supported,
        ), mock.patch.object(
            lifecycle_io.os,
            "O_DIRECTORY",
            0,
            create=True,
        ), mock.patch.object(
            lifecycle_io.os,
            "O_NOFOLLOW",
            0,
            create=True,
        ):
            self.assertTrue(lifecycle_io._posix_cleanup_supported_v3())

    @unittest.skipIf(os.name == "nt", "POSIX dir-fd anchoring contract")
    def test_posix_results_symlink_swap_stays_anchored_and_preserves_external_sentinel(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            prepared = prepared_fixture(root)
            lifecycle_io._prepare_recovery_area_v3(root, prepared["plan"])
            recovery = recovery_root(root, prepared["plan"])
            tree = lifecycle_io._open_cleanup_tree_v3(
                recovery.parent,
                recovery,
                "cleanup",
            )
            external = root / "external"
            external.mkdir()
            sentinel = external / "sentinel.txt"
            sentinel.write_bytes(b"external user data\n")
            moved_results = root / "authorized-results"
            try:
                (tree["path"] / "results").rename(moved_results)
                os.symlink(external, tree["path"] / "results", target_is_directory=True)
                names = lifecycle_io._list_cleanup_entries_v3(
                    tree["children"]["results"],
                    82,
                )
                self.assertTrue(names)
                lifecycle_io._unlink_cleanup_child_v3(
                    tree["children"]["results"],
                    names[0],
                )
                self.assertEqual(sentinel.read_bytes(), b"external user data\n")
            finally:
                lifecycle_io._close_cleanup_tree_v3(tree)

    @unittest.skipIf(os.name == "nt", "POSIX directory-pin removal contract")
    def test_posix_directory_pin_remains_open_through_rmdir(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            prepared = prepared_fixture(root)
            lifecycle_io._prepare_recovery_area_v3(root, prepared["plan"])
            recovery = recovery_root(root, prepared["plan"])
            tree = lifecycle_io._open_cleanup_tree_v3(
                recovery.parent,
                recovery,
                "cleanup",
            )
            results = tree["children"]["results"]
            for name in lifecycle_io._list_cleanup_entries_v3(results, 82):
                lifecycle_io._unlink_cleanup_child_v3(results, name)
            child_fd = results["fd"]
            calls = []
            real_close = lifecycle_io.os.close
            real_rmdir = lifecycle_io.os.rmdir

            def observe_close(fd):
                if fd == child_fd:
                    calls.append("close")
                return real_close(fd)

            def observe_rmdir(*args, **kwargs):
                calls.append("rmdir")
                return real_rmdir(*args, **kwargs)

            try:
                with mock.patch.object(
                    lifecycle_io.os,
                    "close",
                    side_effect=observe_close,
                ), mock.patch.object(
                    lifecycle_io.os,
                    "rmdir",
                    side_effect=observe_rmdir,
                ):
                    lifecycle_io._remove_pinned_directory_v3(
                        tree["root"],
                        "results",
                        results,
                    )
                self.assertEqual(calls[:2], ["rmdir", "close"])
            finally:
                lifecycle_io._close_cleanup_tree_v3(tree)


if __name__ == "__main__":
    unittest.main()
