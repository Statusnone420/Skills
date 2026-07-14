import json
import errno
import hashlib
import os
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / "skills" / "docs" / "scripts"
TOOLS = ROOT / "tools"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(TOOLS))

import check as docs_checker
from _docs_checker import discovery as docs_discovery
import trajectory_discovery_capture
import trajectory_discovery_contract
import trajectory_routes
from _docs_checker.continuation import (
    decode_continuation_token,
    encode_continuation_token,
)


def discover_v2(root, explicit_scope=None, continuation=None):
    return docs_checker.discover_init_scope(
        root,
        explicit_scope,
        continuation,
        contract_version=2,
    )


class Task51ReproducedDefectTests(unittest.TestCase):
    def test_root_maintained_documents_are_selected_for_auto_and_explicit_root(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "README.md").write_text("# Repository\n", encoding="utf-8")
            (root / "PLAN.md").write_text("# Plan\n", encoding="utf-8")

            automatic = discover_v2(root)
            explicit = discover_v2(root, explicit_scope=".")

            for payload in (automatic, explicit):
                with self.subTest(requested_scope=payload["requested_scope"]):
                    self.assertNotEqual(payload["status"], "no-candidates")
                    self.assertEqual(payload["selected_scope"], ".")
                    self.assertEqual(payload["inspected_scope"], ".")
                    self.assertEqual(
                        [item["path"] for item in payload["scope_metadata"]["paths"]],
                        ["PLAN.md", "README.md"],
                    )
                    self.assertEqual(payload["content_reads"], 0)
                    self.assertEqual(payload["adoption_preview"]["writes"], 0)

            self.assertEqual(automatic["selection_reason"], "sole-root-document-scope")
            self.assertEqual(explicit["selection_reason"], "explicit-scope")

    def test_selected_corpus_exposes_executable_exact_once_continuation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            docs = root / "docs"
            docs.mkdir()
            for index in range(13):
                (docs / f"{index:02d}.md").write_text("x", encoding="utf-8")

            first = discover_v2(root, explicit_scope="docs")
            cursor = first["continuation"]["cursor"]
            second = discover_v2(
                root,
                explicit_scope="docs",
                continuation=cursor,
            )

            first_paths = [item["path"] for item in first["content_batch"]["paths"]]
            second_paths = [item["path"] for item in second["content_batch"]["paths"]]
            self.assertEqual(first["continuation"]["status"], "available")
            self.assertIsInstance(cursor, dict)
            self.assertEqual(second["continuation"]["status"], "complete")
            self.assertEqual(len(first_paths), 12)
            self.assertEqual(len(second_paths), 1)
            self.assertEqual(len(set(first_paths + second_paths)), 13)
            self.assertEqual(first_paths + second_paths, sorted(first_paths + second_paths))
            self.assertEqual(first["content_reads"], 0)
            self.assertEqual(second["content_reads"], 0)

    def test_continuation_receipt_exposes_shell_safe_token_and_total_batches(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            docs = root / "docs"
            docs.mkdir()
            for index in range(13):
                (docs / f"{index:02d}.md").write_text("x", encoding="utf-8")

            first = discover_v2(root, explicit_scope="docs")
            continuation = first["continuation"]
            self.assertEqual(
                set(continuation),
                {
                    "schema_version",
                    "status",
                    "batch",
                    "cursor",
                    "token",
                    "total_batches",
                    "rejection",
                    "fresh_preview_required",
                },
            )
            self.assertEqual(continuation["status"], "available")
            self.assertIsInstance(continuation["token"], str)
            self.assertEqual(
                decode_continuation_token(continuation["token"]),
                continuation["cursor"],
            )
            self.assertEqual(continuation["total_batches"], 2)
            self.assertFalse(first["requires_user_action"])
            self.assertEqual(first["user_action"], "continue-init-inspection")

            second = discover_v2(
                root,
                explicit_scope="docs",
                continuation=continuation["cursor"],
            )
            self.assertEqual(second["continuation"]["status"], "complete")
            self.assertIsNone(second["continuation"]["token"])
            self.assertEqual(second["continuation"]["total_batches"], 2)

    def test_continuation_token_codec_rejects_malformed_and_non_exact_inputs(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            docs = root / "docs"
            docs.mkdir()
            for index in range(13):
                (docs / f"{index:02d}.md").write_text("x", encoding="utf-8")
            cursor = discover_v2(root, "docs")["continuation"]["cursor"]

            token = encode_continuation_token(cursor)
            self.assertEqual(decode_continuation_token(token), cursor)

            class DictSubclass(dict):
                pass

            class StringSubclass(str):
                pass

            malformed = [
                token + "=",
                token[:4] + "!" + token[5:],
                "A" * 8193,
                StringSubclass(token),
            ]
            for candidate in malformed:
                with self.subTest(candidate=repr(candidate)):
                    with self.assertRaisesRegex(
                        ValueError,
                        "^content continuation token is invalid$",
                    ):
                        decode_continuation_token(candidate)

            with self.assertRaisesRegex(
                ValueError,
                "^content continuation cursor is invalid$",
            ):
                encode_continuation_token(DictSubclass(cursor))

            import base64

            for raw in (
                b"[]",
                b'{"a":1,"a":2}',
                b"\xff",
                b"NaN",
            ):
                candidate = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
                with self.subTest(raw=raw):
                    with self.assertRaisesRegex(
                        ValueError,
                        "^content continuation token is invalid$",
                    ):
                        decode_continuation_token(candidate)

    def test_root_enumeration_permission_failure_returns_sanitized_incomplete_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            raw_message = f"SECRET-TOKEN at {root}"
            with mock.patch.object(
                docs_discovery.os,
                "scandir",
                side_effect=PermissionError(raw_message),
            ):
                payload = discover_v2(root)

            self.assertEqual(payload["status"], "stopped")
            self.assertIsNone(payload["selected_scope"])
            self.assertEqual(payload["candidates"], [])
            self.assertEqual(payload["content_batch"]["paths"], [])
            self.assertTrue(payload["content_batch"]["blocked_by_metadata"])
            self.assertEqual(payload["completeness"]["status"], "incomplete")
            self.assertEqual(
                payload["completeness"]["errors"],
                [
                    {
                        "operation": "scandir",
                        "path": ".",
                        "phase": "candidate",
                        "depth": None,
                        "blocks_completeness": True,
                        "blocks_selection": True,
                        "blocks_content_planning": True,
                    }
                ],
            )
            serialized = json.dumps(payload, sort_keys=True)
            self.assertNotIn("SECRET-TOKEN", serialized)
            self.assertNotIn(str(root), json.dumps(payload["completeness"]))


class Task51ReceiptV2CompatibilityTests(unittest.TestCase):
    def test_real_v1_receipt_remains_exact_and_v2_roundtrips_through_task6(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            docs = root / "docs"
            docs.mkdir()
            for index in range(13):
                (docs / f"{index:02d}.md").write_text("x", encoding="utf-8")

            v1 = docs_checker.discover_init_scope(root, "docs", contract_version=1)
            self.assertEqual(v1["schema_version"], 1)
            self.assertEqual(
                set(v1),
                trajectory_discovery_capture.DOCTOR_DISCOVERY_RECEIPT_FIELDS | {"root"},
            )
            v1_action = trajectory_discovery_capture.build_doctor_discovery_action(v1)
            v1_errors = []
            trajectory_discovery_contract.validate_doctor_discovery_action(
                v1_action,
                v1_errors,
            )
            self.assertEqual(v1_errors, [])

            v2 = docs_checker.discover_init_scope(root, "docs", contract_version=2)
            roundtripped = json.loads(json.dumps(v2, allow_nan=False))
            v2_action = trajectory_discovery_capture.build_doctor_discovery_action(roundtripped)
            v2_errors = []
            trajectory_discovery_contract.validate_doctor_discovery_action(
                v2_action,
                v2_errors,
            )
            self.assertEqual(v2_errors, [])
            self.assertEqual(
                trajectory_routes.validate_doctor_route([v2_action], scope="docs"),
                [],
            )

    def test_unknown_receipt_version_fails_explicit_dispatch(self):
        with tempfile.TemporaryDirectory() as td, self.assertRaisesRegex(
            ValueError,
            "contract version",
        ):
            docs_checker.discover_init_scope(Path(td), contract_version=99)

    def test_v2_no_doc_adoption_preview_is_a_valid_terminal_task6_route(self):
        with tempfile.TemporaryDirectory() as td:
            payload = discover_v2(Path(td))
            action = trajectory_discovery_capture.build_doctor_discovery_action(payload)
            errors = []
            context = trajectory_discovery_contract.validate_doctor_discovery_action(
                action,
                errors,
            )

            self.assertEqual(payload["status"], "adoption-preview")
            self.assertEqual(errors, [])
            self.assertEqual(context["selected_scope"], ".")
            self.assertEqual(
                trajectory_routes.validate_doctor_route([action], scope="."),
                [],
            )

    def test_root_documents_are_separate_evidence_without_changing_directory_ranking(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "README.md").write_text("# Root\n", encoding="utf-8")
            (root / "docs").mkdir()
            (root / "docs" / "README.md").write_text("# Docs\n", encoding="utf-8")

            v1 = docs_checker.discover_init_scope(root, contract_version=1)
            v2 = docs_checker.discover_init_scope(root, contract_version=2)

            self.assertEqual([item["path"] for item in v1["candidates"]], ["docs"])
            self.assertEqual(v1["selected_scope"], "docs")
            self.assertNotIn("root_documents", v1)
            self.assertEqual([item["path"] for item in v2["candidates"]], ["docs"])
            self.assertEqual(v2["selected_scope"], "docs")
            self.assertEqual(
                [item["path"] for item in v2["root_documents"]["paths"]],
                ["README.md"],
            )

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "README.md").write_text("# Root\n", encoding="utf-8")
            automatic = docs_checker.discover_init_scope(root, contract_version=2)
            explicit = docs_checker.discover_init_scope(
                root,
                explicit_scope=".",
                contract_version=2,
            )
            self.assertEqual(automatic["selected_scope"], ".")
            self.assertEqual(explicit["selected_scope"], ".")

    def test_evaluation_is_root_evidence_without_distorting_directory_ranking(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "EVALUATION.md").write_text("# Evaluation\n", encoding="utf-8")

            root_only = discover_v2(root)
            self.assertEqual(root_only["selected_scope"], ".")
            self.assertEqual(root_only["status"], "ready")
            self.assertEqual(
                [item["path"] for item in root_only["root_documents"]["paths"]],
                ["EVALUATION.md"],
            )

            (root / "docs").mkdir()
            (root / "docs" / "README.md").write_text("# Docs\n", encoding="utf-8")
            v1 = docs_checker.discover_init_scope(root, contract_version=1)
            v2 = discover_v2(root)
            self.assertEqual([item["path"] for item in v1["candidates"]], ["docs"])
            self.assertEqual([item["path"] for item in v2["candidates"]], ["docs"])
            self.assertEqual(v2["selected_scope"], "docs")
            self.assertEqual(
                [item["path"] for item in v2["root_documents"]["paths"]],
                ["EVALUATION.md"],
            )

    def test_v2_api_and_cli_io_failures_never_expose_absolute_or_raw_os_text(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            raw = f"SECRET-CREDENTIAL at {root}"
            with mock.patch.object(
                docs_discovery.os,
                "scandir",
                side_effect=PermissionError(raw),
            ):
                payload = docs_checker.discover_init_scope(root, contract_version=2)
            serialized = json.dumps(payload, sort_keys=True)
            self.assertNotIn(str(root), serialized)
            self.assertNotIn("SECRET-CREDENTIAL", serialized)
            self.assertEqual(payload["root"], ".")

            stdout = __import__("io").StringIO()
            with mock.patch.object(
                docs_checker,
                "discover_init_scope",
                side_effect=PermissionError(errno.EACCES, raw),
            ), mock.patch.object(docs_checker.sys, "stdout", stdout):
                returncode = docs_checker.main(
                    [str(root), "--json", "--agent", "--init-discovery"]
                )
            cli_payload = json.loads(stdout.getvalue())
            self.assertEqual(returncode, 2)
            self.assertEqual(cli_payload["error"], "filesystem metadata unavailable")
            self.assertNotIn(str(root), stdout.getvalue())
            self.assertNotIn("SECRET-CREDENTIAL", stdout.getvalue())

            with mock.patch.object(
                docs_checker,
                "discover_init_scope",
                side_effect=OSError(9999, "programming misuse"),
            ):
                with self.assertRaises(OSError):
                    docs_checker.main(
                        [str(root), "--json", "--agent", "--init-discovery"]
                    )


class Task51RootAndContinuationTests(unittest.TestCase):
    def test_complete_no_doc_repository_returns_adoption_preview(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "application.py").write_text("print('ok')\n", encoding="utf-8")
            (root / "notes.md").write_text("# Not maintained truth\n", encoding="utf-8")

            payload = discover_v2(root)

            self.assertEqual(payload["status"], "adoption-preview")
            self.assertEqual(payload["selection_reason"], "no-maintained-documentation")
            self.assertEqual(payload["selected_scope"], ".")
            self.assertEqual(payload["inspected_scope"], ".")
            self.assertEqual(payload["scope_metadata"]["paths"], [])
            self.assertTrue(payload["scope_metadata"]["complete"])
            self.assertEqual(payload["completeness"]["status"], "complete")
            self.assertEqual(payload["user_action"], "review-no-doc-adoption-preview")
            self.assertEqual(payload["adoption_preview"]["writes"], 0)

    def test_mixed_root_and_docs_requires_choice_while_explicit_root_is_direct(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "docs").mkdir()
            (root / "docs" / "guide.md").write_text("# Guide\n", encoding="utf-8")
            (root / "README.md").write_text("# Root\n", encoding="utf-8")
            (root / "random.md").write_text("# Random\n", encoding="utf-8")

            automatic = discover_v2(root)
            explicit = discover_v2(root, explicit_scope="./")

            self.assertEqual(
                [item["path"] for item in automatic["candidates"]],
                ["docs"],
            )
            self.assertEqual(automatic["status"], "ready")
            self.assertEqual(automatic["selected_scope"], "docs")
            self.assertEqual(
                [item["path"] for item in automatic["root_documents"]["paths"]],
                ["README.md"],
            )
            self.assertEqual(explicit["selected_scope"], ".")
            self.assertEqual(explicit["selection_reason"], "explicit-scope")
            self.assertEqual(
                [item["path"] for item in explicit["scope_metadata"]["paths"]],
                ["README.md"],
            )

    def test_wide_root_stops_incomplete_without_partial_selection(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for index in range(129):
                (root / f"entry-{index:03d}").mkdir()

            payload = discover_v2(root)

            self.assertEqual(payload["status"], "stopped")
            self.assertIsNone(payload["selected_scope"])
            self.assertEqual(payload["completeness"]["status"], "incomplete")
            self.assertEqual(payload["physical_limit"]["kind"], "child_entries_per_container")

    def test_task6_unreadable_selected_scope_pauses_without_path_or_secret_leak(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "docs").mkdir()
            (root / "docs" / "guide.md").write_text("# Guide\n", encoding="utf-8")
            real_scandir = docs_discovery.os.scandir

            def unreadable(path):
                if Path(path).name.casefold() == "docs":
                    raise PermissionError(errno.EACCES, "PRIVATE_SCOPE_SECRET", str(path))
                return real_scandir(path)

            with mock.patch.object(
                docs_discovery.os,
                "scandir",
                side_effect=unreadable,
            ):
                payload = discover_v2(root, explicit_scope="docs")

            serialized = json.dumps(payload, sort_keys=True)
            self.assertEqual(payload["status"], "stopped")
            self.assertEqual(payload["selected_scope"], "docs")
            self.assertEqual(payload["completeness"]["status"], "incomplete")
            self.assertEqual(payload["content_batch"]["paths"], [])
            self.assertEqual(payload["continuation"]["status"], "blocked")
            self.assertEqual(payload["next_boundary"], [{"kind": "metadata-io", "path": "docs"}])
            self.assertNotIn(str(root), serialized)
            self.assertNotIn("PRIVATE_SCOPE_SECRET", serialized)

    def test_three_batches_cover_selected_corpus_exactly_once(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            docs = root / "docs"
            docs.mkdir()
            for index in reversed(range(31)):
                (docs / f"{index:02d}.md").write_text("x", encoding="utf-8")

            batches = []
            cursor = None
            while True:
                payload = discover_v2(
                    root,
                    explicit_scope="docs",
                    continuation=cursor,
                )
                batches.append([item["path"] for item in payload["content_batch"]["paths"]])
                cursor = payload["continuation"]["cursor"]
                if cursor is None:
                    self.assertEqual(payload["continuation"]["status"], "complete")
                    break

            flattened = [path for batch in batches for path in batch]
            self.assertEqual([len(batch) for batch in batches], [12, 12, 7])
            self.assertEqual(len(flattened), len(set(flattened)))
            self.assertEqual(flattened, [f"docs/{index:02d}.md" for index in range(31)])

    def test_tampered_and_stale_continuations_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            docs = root / "docs"
            docs.mkdir()
            for index in range(13):
                (docs / f"{index:02d}.md").write_text("x", encoding="utf-8")
            first = discover_v2(root, "docs")

            tampered = dict(first["continuation"]["cursor"])
            tampered["next_index"] += 1
            tampered_result = discover_v2(
                root,
                "docs",
                continuation=tampered,
            )
            self.assertEqual(tampered_result["continuation"]["status"], "rejected")
            self.assertEqual(tampered_result["content_batch"]["paths"], [])
            self.assertEqual(tampered_result["user_action"], "restart-fresh-discovery")

            (docs / "12.md").write_text("changed-size", encoding="utf-8")
            stale_result = discover_v2(
                root,
                "docs",
                continuation=first["continuation"]["cursor"],
            )
            self.assertEqual(stale_result["continuation"]["status"], "rejected")
            self.assertTrue(stale_result["continuation"]["fresh_preview_required"])
            self.assertEqual(stale_result["content_batch"]["paths"], [])

    def test_non_json_and_subclass_cursors_reject_without_raising(self):
        class DictSubclass(dict):
            pass

        class StringSubclass(str):
            pass

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            docs = root / "docs"
            docs.mkdir()
            for index in range(13):
                (docs / f"{index:02d}.md").write_text("x", encoding="utf-8")
            cursor = discover_v2(root, "docs")["continuation"]["cursor"]
            mutations = []
            non_json = dict(cursor)
            non_json["next_index"] = object()
            mutations.append(non_json)
            subclass_value = dict(cursor)
            subclass_value["selected_scope"] = StringSubclass("docs")
            mutations.extend((subclass_value, DictSubclass(cursor)))

            for candidate in mutations:
                with self.subTest(candidate_type=type(candidate).__name__):
                    result = discover_v2(
                        root,
                        "docs",
                        continuation=candidate,
                    )
                    self.assertEqual(result["continuation"]["status"], "rejected")
                    self.assertEqual(result["content_batch"]["paths"], [])

    def test_cursor_binds_v2_receipt_and_resume_reconsumes_counters_and_prunes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            docs = root / "docs"
            docs.mkdir()
            for index in range(13):
                (docs / f"{index:02d}.md").write_text("PRIVATE BODY", encoding="utf-8")
            (docs / ".cache" / "private.md").parent.mkdir()
            (docs / ".cache" / "private.md").write_text("SECRET", encoding="utf-8")

            first = discover_v2(root, "docs")
            cursor = first["continuation"]["cursor"]
            resumed = discover_v2(root, "docs", cursor)

            self.assertEqual(cursor["discovery_contract_version"], 2)
            self.assertGreater(resumed["observed"]["metadata_operations"], 0)
            self.assertLessEqual(
                resumed["observed"]["metadata_operations"],
                resumed["limits"]["metadata_operations"],
            )
            self.assertEqual(first["prunes"], resumed["prunes"])
            serialized = json.dumps(cursor, sort_keys=True)
            self.assertNotIn(str(root), serialized)
            self.assertNotIn("PRIVATE BODY", serialized)
            self.assertNotIn("SECRET", serialized)

            foreign = dict(cursor)
            foreign["discovery_contract_version"] = 1
            rejected = discover_v2(root, "docs", foreign)
            self.assertEqual(rejected["continuation"]["status"], "rejected")
            self.assertEqual(rejected["content_batch"]["paths"], [])


class Task51RecoverableMetadataFailureTests(unittest.TestCase):
    class _FailingEntry:
        def __init__(self, path, error):
            self.name = Path(path).name
            self.path = str(path)
            self._error = error

        def stat(self, *, follow_symlinks):
            raise self._error

    class _Entries:
        def __init__(self, entries):
            self._entries = iter(entries)

        def __enter__(self):
            return self._entries

        def __exit__(self, *_):
            return False

    def test_unreadable_candidate_discards_partial_ranking(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            docs = root / "docs"
            docs.mkdir()
            real_lstat = docs_discovery.os.lstat

            def unreadable(path):
                if Path(path) == docs:
                    raise PermissionError("PRIVATE credential path")
                return real_lstat(path)

            with mock.patch.object(docs_discovery.os, "lstat", side_effect=unreadable):
                payload = discover_v2(root)

            self.assertEqual(payload["status"], "stopped")
            self.assertEqual(payload["candidates"], [])
            self.assertIsNone(payload["selected_scope"])
            self.assertEqual(payload["completeness"]["errors"][0]["operation"], "lstat")
            self.assertEqual(payload["completeness"]["errors"][0]["path"], "docs")
            self.assertNotIn("PRIVATE", json.dumps(payload))

    def test_unreadable_explicit_descendant_blocks_content_planning(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            scope = root / "handbook"
            scope.mkdir()
            failing = self._FailingEntry(
                scope / "guide.md",
                PermissionError("SECRET descendant"),
            )
            real_scandir = docs_discovery.os.scandir

            def scandir(path):
                if Path(path) == scope:
                    return self._Entries([failing])
                return real_scandir(path)

            with mock.patch.object(docs_discovery.os, "scandir", side_effect=scandir):
                payload = discover_v2(root, "handbook")

            self.assertEqual(payload["status"], "stopped")
            self.assertEqual(payload["content_batch"]["paths"], [])
            self.assertTrue(payload["content_batch"]["blocked_by_metadata"])
            error = payload["completeness"]["errors"][0]
            self.assertEqual(error["operation"], "direntry-stat")
            self.assertEqual(error["path"], "handbook/guide.md")
            self.assertEqual(error["depth"], 0)

    def test_directory_disappearing_after_enumeration_is_incomplete(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            scope = root / "handbook"
            scope.mkdir()
            failing = self._FailingEntry(
                scope / "gone",
                FileNotFoundError("gone absolute private path"),
            )
            real_scandir = docs_discovery.os.scandir

            def scandir(path):
                if Path(path) == scope:
                    return self._Entries([failing])
                return real_scandir(path)

            with mock.patch.object(docs_discovery.os, "scandir", side_effect=scandir):
                payload = discover_v2(root, "handbook")

            self.assertEqual(payload["status"], "stopped")
            self.assertEqual(payload["completeness"]["status"], "incomplete")
            self.assertEqual(payload["content_batch"]["paths"], [])
            self.assertNotIn("absolute private", json.dumps(payload))

    def test_programming_errors_still_propagate(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(
            docs_discovery.os,
            "scandir",
            side_effect=RuntimeError("programming defect"),
        ):
            with self.assertRaisesRegex(RuntimeError, "programming defect"):
                discover_v2(Path(td))

    def test_expected_os_error_errno_is_sanitized_but_unknown_errno_propagates(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with mock.patch.object(
                docs_discovery.os,
                "scandir",
                side_effect=OSError(errno.EIO, "SECRET device path"),
            ):
                payload = discover_v2(root)
            self.assertEqual(payload["completeness"]["status"], "incomplete")
            self.assertNotIn("SECRET", json.dumps(payload))

            with mock.patch.object(
                docs_discovery.os,
                "scandir",
                side_effect=OSError(9999, "programming misuse"),
            ):
                with self.assertRaises(OSError):
                    discover_v2(root)


class Task51LocalKnowledgeTests(unittest.TestCase):
    REPOSITORY_ID = "a" * 64
    WORKTREE_ID = "b" * 64

    def _route(self, relative, content=None, **overrides):
        route = {
            "route": relative,
            "visibility": "local-only",
            "kind": "plan",
            "topics": ["release", "performance"],
            "aliases": ["0.3.0", "chat calm"],
            "authority": "authoritative",
            "status": "current",
            "preservation": "preserve-local-only",
            "last_verified_system": "0.1.0",
            "last_verified_rubric": "docs-health-v2",
        }
        route.update(overrides)
        return route

    def _write_map(self, root, routes):
        path = root / ".diataxis" / "local-map.json"
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "repository_identity": self.REPOSITORY_ID,
                    "worktree_identity": self.WORKTREE_ID,
                    "routes": routes,
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_ignored_cline_campaign_is_local_candidate_without_content_or_filename_leak(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            campaign = root / ".local" / "0.3.0-campaign"
            campaign.mkdir(parents=True)
            (campaign / "KICKOFF-PROMPT.md").write_text(
                "PRIVATE NINE PR CAMPAIGN CHAT CALM PERFORMANCE",
                encoding="utf-8",
            )
            (root / ".local" / "node_modules" / "private").mkdir(parents=True)
            (root / ".local" / "credentials" / "private").mkdir(parents=True)
            (root / ".gitignore").write_text(".local/\n", encoding="utf-8")

            with mock.patch.object(
                Path,
                "read_text",
                side_effect=AssertionError("documentation content read"),
            ), mock.patch.object(
                Path,
                "read_bytes",
                side_effect=AssertionError("documentation content read"),
            ):
                payload = discover_v2(root)

            self.assertEqual(payload["status"], "adoption-preview")
            self.assertEqual(payload["selected_scope"], ".")
            self.assertEqual(
                payload["local_knowledge"]["candidates"],
                [
                    {
                        "path": ".local/0.3.0-campaign",
                        "visibility": "local-only",
                        "source": "conventional-local-root",
                        "evidence": "documentation-shaped-directory",
                    }
                ],
            )
            self.assertEqual(payload["candidates"], [])
            serialized = json.dumps(payload)
            self.assertNotIn("KICKOFF-PROMPT.md", serialized)
            self.assertNotIn("NINE PR", serialized)
            self.assertFalse(payload["local_knowledge"]["absence_claim_allowed"])
            self.assertEqual(payload["content_reads"], 0)

    def test_exact_local_scope_works_with_and_without_git(self):
        for has_git in (False, True):
            with self.subTest(has_git=has_git), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                campaign = root / ".local" / "0.3.0-campaign"
                campaign.mkdir(parents=True)
                (campaign / "PLAN.md").write_text("# Plan\n", encoding="utf-8")
                if has_git:
                    (root / ".git").mkdir()

                payload = discover_v2(
                    root,
                    explicit_scope=".local/0.3.0-campaign",
                )

                self.assertEqual(payload["selected_scope"], ".local/0.3.0-campaign")
                self.assertEqual(
                    [item["path"] for item in payload["content_batch"]["paths"]],
                    [".local/0.3.0-campaign/PLAN.md"],
                )
                self.assertEqual(payload["local_knowledge"]["selected_visibility"], "local-only")

    def test_private_local_routes_do_not_compete_with_sole_shared_scope(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "docs").mkdir()
            (root / "docs" / "README.md").write_text("# Docs\n", encoding="utf-8")
            (root / ".local" / "alpha-campaign").mkdir(parents=True)
            (root / ".local" / "beta-decisions").mkdir(parents=True)

            payload = discover_v2(root)

            self.assertEqual(payload["status"], "ready")
            self.assertEqual(payload["selected_scope"], "docs")
            self.assertEqual(
                [item["path"] for item in payload["candidates"]],
                ["docs"],
            )
            self.assertEqual(
                [item["path"] for item in payload["local_knowledge"]["candidates"]],
                [".local/alpha-campaign", ".local/beta-decisions"],
            )

    def test_two_tied_shared_roots_still_require_one_choice(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "docs").mkdir(parents=True)
            (root / "documentation").mkdir(parents=True)

            payload = discover_v2(root)

            self.assertEqual(payload["status"], "choice-required")
            self.assertEqual(payload["recommended_scope"], "docs")
            self.assertEqual(
                [item["path"] for item in payload["candidates"]],
                ["docs", "documentation"],
            )
            self.assertEqual(payload["user_action"], "choose-explicit-scope")

    def test_local_only_repository_preserves_private_routes_without_absence_claim(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".local" / "alpha-campaign").mkdir(parents=True)
            (root / ".local" / "beta-decisions").mkdir(parents=True)

            payload = discover_v2(root)

            self.assertEqual(payload["status"], "adoption-preview")
            self.assertEqual(payload["selected_scope"], ".")
            self.assertEqual(payload["candidates"], [])
            self.assertEqual(
                [item["path"] for item in payload["local_knowledge"]["candidates"]],
                [".local/alpha-campaign", ".local/beta-decisions"],
            )
            self.assertFalse(payload["local_knowledge"]["absence_claim_allowed"])
            self.assertEqual(
                payload["user_action"],
                "review-no-doc-adoption-preview",
            )

    def test_local_candidate_probe_physically_avoids_dependency_cache_and_credentials(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".local" / "release-campaign").mkdir(parents=True)
            for relative in (
                ".local/node_modules/private",
                ".local/.cache/private",
                ".local/credentials/private",
                ".local/cache/private",
            ):
                (root / relative).mkdir(parents=True)
            real_scandir = os.scandir
            visited = []

            def tracked(path):
                visited.append(Path(path).absolute())
                return real_scandir(path)

            with mock.patch.object(os, "scandir", side_effect=tracked):
                payload = discover_v2(root)

            self.assertEqual(
                [item["path"] for item in payload["local_knowledge"]["candidates"]],
                [".local/release-campaign"],
            )
            blocked = [
                root / ".local" / "node_modules",
                root / ".local" / ".cache",
                root / ".local" / "credentials",
                root / ".local" / "cache",
            ]
            self.assertFalse(
                any(
                    visit == path or path in visit.parents
                    for visit in visited
                    for path in blocked
                )
            )

    def test_local_map_routes_alias_and_prevents_unopened_absence_claim(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            content = b"authoritative campaign"
            route_path = ".local/0.3.0-campaign/KICKOFF-PROMPT.md"
            path = root / Path(route_path)
            path.parent.mkdir(parents=True)
            path.write_bytes(content)
            self._write_map(root, [self._route(route_path, content)])

            inspected = docs_checker.inspect_local_map(
                root,
                repository_identity=self.REPOSITORY_ID,
                worktree_identity=self.WORKTREE_ID,
            )
            routed = docs_checker.route_local_knowledge(inspected, "0.3.0")

            self.assertEqual(inspected["status"], "present-uninspected")
            self.assertEqual(inspected["binding"], "matched")
            self.assertEqual(routed["status"], "present-uninspected")
            self.assertEqual(routed["routes"], [route_path])
            self.assertFalse(routed["absence_claim_allowed"])
            self.assertEqual(routed["uninspected_routes"], [route_path])
            self.assertFalse(routed["shared_health_impact"])

    def test_missing_clone_reports_declared_local_knowledge_unavailable(self):
        with tempfile.TemporaryDirectory() as td:
            result = docs_checker.inspect_local_map(Path(td), declared=True)
            routed = docs_checker.route_local_knowledge(result, "release")

            self.assertEqual(result["status"], "declared-local-knowledge-unavailable")
            self.assertEqual(routed["status"], "declared-local-knowledge-unavailable")
            self.assertFalse(routed["absence_claim_allowed"])
            self.assertFalse(result["shared_health_impact"])

    def test_local_map_validation_reads_only_allowlisted_routing_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            content = b"original authority"
            route_path = ".local/plan.md"
            path = root / Path(route_path)
            path.parent.mkdir(parents=True)
            path.write_bytes(content)
            self._write_map(root, [self._route(route_path, content)])
            real_open = open

            def guarded_open(candidate, *args, **kwargs):
                if Path(candidate) == path:
                    raise AssertionError("local documentation body read")
                return real_open(candidate, *args, **kwargs)

            with mock.patch("builtins.open", side_effect=guarded_open):
                result = docs_checker.inspect_local_map(root)

            self.assertEqual(result["status"], "present-uninspected")
            self.assertEqual(result["content_reads"], 0)
            self.assertEqual(result["evidence_reads"]["count"], 1)
            self.assertEqual(
                result["evidence_reads"]["sources"],
                [".diataxis/local-map.json"],
            )
            self.assertLessEqual(
                result["evidence_reads"]["bytes"],
                result["evidence_reads"]["byte_limit"],
            )

    def test_local_map_rejects_forbidden_or_unsafe_contract_fields(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            route = self._route(".local/plan.md", b"plan")
            route["prompt"] = "hidden reasoning"
            self._write_map(root, [route])

            with self.assertRaisesRegex(ValueError, "local map"):
                docs_checker.inspect_local_map(root)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            route = self._route("../private.md", b"plan")
            self._write_map(root, [route])
            with self.assertRaisesRegex(ValueError, "local map"):
                docs_checker.inspect_local_map(root)


class Task51ProtectedSurfaceTests(unittest.TestCase):
    def test_github_surface_policy_preserves_precedence_community_release_and_wiki(self):
        paths = [
            ".github/README.md",
            "README.md",
            "docs/README.md",
            "SECURITY.md",
            "CONTRIBUTING.md",
            "CODE_OF_CONDUCT.md",
            "SUPPORT.md",
            "GOVERNANCE.md",
            "CHANGELOG.md",
            "MIGRATING.md",
            "LICENSE",
            "CITATION.cff",
            "AGENTS.md",
            ".github/FUNDING.yml",
            ".github/CODEOWNERS",
            ".github/ISSUE_TEMPLATE/bug.yml",
            ".github/PULL_REQUEST_TEMPLATE.md",
            "mkdocs.yml",
            "docs/site/index.md",
        ]
        classified = docs_checker.classify_protected_surfaces(
            paths,
            host="github",
            references=[
                {
                    "source": "release.config.json",
                    "target": "CHANGELOG.md",
                    "kind": "automation",
                },
                {
                    "source": "package.json",
                    "target": "MIGRATING.md",
                    "kind": "tooling",
                },
            ],
            external_routes=[
                {
                    "route": "wiki",
                    "provider": "github",
                    "availability": "external-unavailable",
                }
            ],
        )

        by_path = {item["path"]: item for item in classified["items"]}
        self.assertEqual(classified["host"], "github")
        self.assertTrue(by_path[".github/README.md"]["surfaced"])
        self.assertFalse(by_path["README.md"]["surfaced"])
        self.assertFalse(by_path["docs/README.md"]["surfaced"])
        self.assertEqual(
            by_path["CHANGELOG.md"]["protection_reason"],
            "automation/tooling-consumed",
        )
        self.assertEqual(
            by_path["LICENSE"]["protection_reason"],
            "legal/community-governance",
        )
        self.assertEqual(
            by_path["AGENTS.md"]["protection_reason"],
            "repository-convention",
        )
        self.assertTrue(all(item["default_disposition"] == "retain" for item in by_path.values()))
        self.assertEqual(
            classified["external_routes"],
            [
                {
                    "route": "wiki",
                    "provider": "github",
                    "availability": "external-unavailable",
                    "protected": True,
                    "default_disposition": "retain",
                }
            ],
        )
        self.assertFalse(classified["healthy_placement_affects_score"])

    def test_filesystem_surface_inspection_is_metadata_only_and_avoids_noise(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for relative in (
                "README.md",
                "SECURITY.md",
                ".github/CONTRIBUTING.md",
                ".github/ISSUE_TEMPLATE/bug.yml",
                "docs/README.md",
                "docs/site/index.md",
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("PRIVATE BODY", encoding="utf-8")
            for relative in (
                ".git/private",
                "node_modules/private",
                ".cache/private",
                ".local/credentials/private",
                ".github/node_modules/private",
                "docs/.cache/private",
                "docs/credentials/private",
            ):
                (root / relative).mkdir(parents=True)

            real_scandir = os.scandir
            visited = []

            def tracked_scandir(path):
                visited.append(Path(path).absolute())
                return real_scandir(path)

            with mock.patch.object(Path, "read_text", side_effect=AssertionError("body read")), mock.patch.object(
                Path,
                "read_bytes",
                side_effect=AssertionError("body read"),
            ), mock.patch("builtins.open", side_effect=AssertionError("body read")), mock.patch.object(
                os,
                "scandir",
                side_effect=tracked_scandir,
            ):
                result = docs_checker.inspect_protected_surfaces(root, host="github")

            self.assertEqual(result["content_reads"], 0)
            self.assertEqual(result["evidence_reads"]["count"], 0)
            self.assertIn(".github/ISSUE_TEMPLATE/bug.yml", [item["path"] for item in result["items"]])
            forbidden = [
                root / ".git",
                root / "node_modules",
                root / ".cache",
                root / ".local",
                root / ".github" / "node_modules",
                root / "docs" / ".cache",
                root / "docs" / "credentials",
            ]
            self.assertFalse(
                any(
                    visit == blocked or blocked in visit.parents
                    for visit in visited
                    for blocked in forbidden
                )
            )

    def test_init_reports_root_protected_evidence_without_local_route_leakage(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "README.md").write_text("# Root\n", encoding="utf-8")
            (root / "SECURITY.md").write_text("# Security\n", encoding="utf-8")
            campaign = root / ".local" / "private-campaign"
            campaign.mkdir(parents=True)
            (campaign / "KICKOFF-PROMPT.md").write_text("PRIVATE TOPIC", encoding="utf-8")

            payload = discover_v2(root)
            protected_paths = [item["path"] for item in payload["protected_surfaces"]["items"]]
            self.assertEqual(protected_paths, ["README.md", "SECURITY.md"])
            shared_lane = json.dumps(payload["protected_surfaces"], sort_keys=True)
            self.assertNotIn(".local", shared_lane)
            self.assertNotIn("KICKOFF", shared_lane)
            self.assertNotIn("PRIVATE TOPIC", shared_lane)

    def test_surface_classifier_rejects_unsafe_and_non_exact_inputs(self):
        class StringSubclass(str):
            pass

        for paths in (["../README.md"], [StringSubclass("README.md")], "README.md"):
            with self.subTest(paths=paths), self.assertRaises(ValueError):
                docs_checker.classify_protected_surfaces(paths, host="unknown")

    def test_unknown_host_ordinary_docs_are_not_labeled_public(self):
        classified = docs_checker.classify_protected_surfaces(
            ["docs/guide.md"],
            host="unknown",
            complete=True,
        )
        self.assertEqual(classified["host"], "unknown")
        item = classified["items"][0]
        self.assertEqual(item["role"], "internal-documentation")
        self.assertEqual(item["protection_reason"], "ordinary-internal-documentation")
        self.assertFalse(item["protected"])
        self.assertEqual(item["default_disposition"], "eligible-with-disposition")
        self.assertFalse(classified["healthy_placement_affects_score"])

    def test_unknown_host_incomplete_evidence_retains_ordinary_docs_safely(self):
        classified = docs_checker.classify_protected_surfaces(
            ["docs/guide.md"],
            host="unknown",
            complete=False,
        )
        item = classified["items"][0]
        self.assertFalse(item["protected"])
        self.assertEqual(item["default_disposition"], "retain")
        self.assertEqual(classified["mutation_default"], "retain")
        self.assertFalse(classified["complete"])

    def test_evidence_specific_surfaces_remain_protected_on_unknown_host(self):
        classified = docs_checker.classify_protected_surfaces(
            [".github", "README.md", "SECURITY.md", "CONTRIBUTING.md"],
            host="unknown",
            complete=False,
        )
        by_path = {item["path"]: item for item in classified["items"]}
        self.assertTrue(all(by_path[path]["protected"] for path in by_path))
        self.assertEqual(by_path[".github"]["role"], "host-community-surface")

    def test_explicit_docs_scope_preserves_repository_level_github_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "docs").mkdir()
            (root / "docs" / "guide.md").write_text("# Guide\n", encoding="utf-8")
            (root / ".github" / "ISSUE_TEMPLATE").mkdir(parents=True)
            (root / ".github" / "ISSUE_TEMPLATE" / "bug.md").write_text(
                "# Bug\n",
                encoding="utf-8",
            )
            (root / "README.md").write_text("# Root\n", encoding="utf-8")

            payload = discover_v2(root, explicit_scope="docs")

        self.assertEqual(payload["protected_surfaces"]["host"], "github")
        paths = {item["path"] for item in payload["protected_surfaces"]["items"]}
        self.assertIn(".github", paths)
        self.assertIn("README.md", paths)

    def test_evaluation_is_a_protected_repository_convention_surface(self):
        classified = docs_checker.classify_protected_surfaces(
            ["EVALUATION.md"],
            host="unknown",
        )
        item = classified["items"][0]
        self.assertEqual(item["role"], "repository-evaluation")
        self.assertEqual(item["protection_reason"], "repository-convention")
        self.assertTrue(item["protected"])
        self.assertEqual(item["default_disposition"], "retain")
        self.assertFalse(classified["healthy_placement_affects_score"])

    def test_protected_change_preview_blocks_silent_mutation_but_allows_disposed_internal_restructure(self):
        classified = docs_checker.classify_protected_surfaces(
            ["README.md", "SECURITY.md", "docs/internal/a.md", "docs/internal/b.md"],
            host="github",
        )
        blocked = docs_checker.preview_protected_dispositions(
            classified,
            [{"path": "README.md", "action": "replace", "disposition": "stub"}],
        )
        self.assertEqual(blocked["status"], "blocked")
        self.assertIn("README.md", blocked["blocked_paths"])

        allowed = docs_checker.preview_protected_dispositions(
            classified,
            [
                {"path": "README.md", "action": "retain", "disposition": "front-door"},
                {"path": "SECURITY.md", "action": "retain", "disposition": "community"},
                {"path": "docs/internal/a.md", "action": "remove", "disposition": "merged:docs/internal/b.md"},
            ],
        )
        self.assertEqual(allowed["status"], "allowed-preview")
        self.assertEqual(allowed["writes"], 0)
        self.assertEqual(allowed["protected_paths_retained"], ["README.md", "SECURITY.md"])

    def test_unknown_protected_actions_and_malformed_authorizations_fail_closed(self):
        classified = docs_checker.classify_protected_surfaces(
            ["README.md", "docs/internal.md"],
            host="github",
        )
        for action in ("truncate", "obliterate"):
            with self.subTest(action=action), self.assertRaises(ValueError):
                docs_checker.preview_protected_dispositions(
                    classified,
                    [{"path": "README.md", "action": action, "disposition": "changed"}],
                )
        for authorizations in (
            "README.md",
            ["../README.md"],
            ["README.md", "README.md"],
        ):
            with self.subTest(authorizations=authorizations), self.assertRaises(ValueError):
                docs_checker.preview_protected_dispositions(
                    classified,
                    [{"path": "README.md", "action": "replace", "disposition": "stub"}],
                    exact_authorizations=authorizations,
                )

        authorized = docs_checker.preview_protected_dispositions(
            classified,
            [{"path": "README.md", "action": "replace", "disposition": "front-door"}],
            exact_authorizations=["README.md"],
        )
        unprotected = docs_checker.preview_protected_dispositions(
            classified,
            [{"path": "docs/internal.md", "action": "remove", "disposition": "merged"}],
        )
        self.assertEqual(authorized["status"], "allowed-preview")
        self.assertEqual(unprotected["status"], "allowed-preview")


class Task51IndependentReviewRepairTests(unittest.TestCase):
    def _validated_action(self, payload):
        action = trajectory_discovery_capture.build_doctor_discovery_action(payload)
        errors = []
        context = trajectory_discovery_contract.validate_doctor_discovery_action(
            action,
            errors,
        )
        return action, errors, context

    def test_explicit_scope_rejects_windows_ambiguous_pruned_aliases(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "node_modules").mkdir()
            (root / "node_modules" / "README.md").write_text(
                "# Private dependency docs\n", encoding="utf-8"
            )

            for scope in ("node_modules.", "node_modules ", "NODEMO~1"):
                with self.subTest(scope=scope), self.assertRaisesRegex(
                    ValueError, "explicit scope"
                ):
                    discover_v2(root, scope)

    def test_byte_limited_continuation_numbers_and_validates_every_batch(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            docs = root / "docs"
            docs.mkdir()
            for index in range(20):
                (docs / f"{index:02d}.md").write_bytes(b"x" * (27 * 1024))

            payloads = []
            cursor = None
            while True:
                payload = discover_v2(root, "docs", cursor)
                payloads.append(payload)
                _, errors, _ = self._validated_action(payload)
                self.assertEqual(errors, [])
                cursor = payload["continuation"]["cursor"]
                if cursor is None:
                    break

            self.assertEqual(
                [item["continuation"]["batch"] for item in payloads],
                [1, 2, 3],
            )
            flattened = [
                item["path"]
                for payload in payloads
                for item in payload["content_batch"]["paths"]
            ]
            self.assertEqual(flattened, [f"docs/{index:02d}.md" for index in range(20)])

    def test_oversized_first_document_stops_honestly_without_dead_cursor(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            docs = root / "docs"
            docs.mkdir()
            (docs / "oversized.md").write_bytes(b"x" * (300 * 1024))

            payload = discover_v2(root, "docs")
            _, errors, _ = self._validated_action(payload)

            self.assertEqual(payload["status"], "stopped")
            self.assertEqual(payload["content_batch"]["paths"], [])
            self.assertTrue(payload["content_batch"]["blocked_by_metadata"])
            self.assertFalse(payload["content_batch"]["truncated"])
            self.assertEqual(payload["continuation"]["status"], "blocked")
            self.assertIsNone(payload["continuation"]["batch"])
            self.assertIsNone(payload["continuation"]["cursor"])
            self.assertEqual(payload["next_boundary"], [])
            self.assertEqual(errors, [])

    def test_local_root_candidate_precedes_children_and_validates(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            local = root / ".local"
            (local / "zeta-plan").mkdir(parents=True)
            (local / "README.md").write_text("# Local knowledge\n", encoding="utf-8")

            payload = discover_v2(root)
            _, errors, _ = self._validated_action(payload)

            expected = [".local", ".local/zeta-plan"]
            self.assertEqual(
                [item["path"] for item in payload["local_knowledge"]["candidates"]],
                expected,
            )
            self.assertEqual(payload["candidates"], [])
            self.assertEqual(errors, [])

    def test_v2_receipt_rejects_stale_checksum_without_recomputing_it(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "docs").mkdir()
            (root / "docs" / "README.md").write_text("# Docs\n", encoding="utf-8")
            action = trajectory_discovery_capture.build_doctor_discovery_action(
                discover_v2(root, "docs")
            )
            action["selection_reason"] = "sole-candidate"
            errors = []

            trajectory_discovery_contract.validate_doctor_discovery_action(
                action, errors
            )

            self.assertIn("retrieval.invalid_doctor_init_discovery", errors)

    def test_cli_json_sanitizes_arbitrary_errors_and_repository_root(self):
        private = r"C:\private-checkout\credentials.txt SECRET"
        for error in (ValueError(private), UnicodeError(private)):
            stdout = __import__("io").StringIO()
            with mock.patch.object(
                docs_checker,
                "discover_init_scope",
                side_effect=error,
            ), mock.patch.object(docs_checker.sys, "stdout", stdout):
                code = docs_checker.main(
                    [".", "--json", "--agent", "--init-discovery"]
                )
            payload = json.loads(stdout.getvalue())
            self.assertEqual(code, 2)
            self.assertEqual(payload["error"], "invalid command input")
            self.assertNotIn(private, stdout.getvalue())

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "docs").mkdir()
            (root / "docs" / "README.md").write_text("# Docs\n", encoding="utf-8")
            stdout = __import__("io").StringIO()
            with mock.patch.object(docs_checker.sys, "stdout", stdout):
                code = docs_checker.main([str(root), "--json", "--agent"])
            payload = json.loads(stdout.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(payload["root"], ".")
            self.assertNotIn(str(root), stdout.getvalue())

    def test_empty_discovery_accounts_for_every_physical_probe(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            real_lstat = docs_discovery.os.lstat
            real_scandir = docs_discovery.os.scandir
            observed = {"lstat": 0, "scandir": 0}

            def counted_lstat(path):
                observed["lstat"] += 1
                return real_lstat(path)

            def counted_scandir(path):
                observed["scandir"] += 1
                return real_scandir(path)

            with mock.patch.object(
                docs_discovery.os,
                "lstat",
                side_effect=counted_lstat,
            ), mock.patch.object(
                docs_discovery.os,
                "scandir",
                side_effect=counted_scandir,
            ), mock.patch.object(
                docs_discovery.os.path,
                "lexists",
                side_effect=AssertionError("uncounted lexists probe"),
            ):
                payload = discover_v2(root)

            self.assertEqual(payload["status"], "adoption-preview")
            self.assertEqual(
                payload["observed"]["metadata_operations"],
                observed["lstat"] + observed["scandir"],
            )

    def test_foreign_hardlinked_corpus_cursor_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            first_root = base / "first"
            second_root = base / "second"
            (first_root / "docs").mkdir(parents=True)
            (second_root / "docs").mkdir(parents=True)
            for index in range(13):
                source = first_root / "docs" / f"{index:02d}.md"
                source.write_text("same", encoding="utf-8")
                os.link(source, second_root / "docs" / source.name)

            first = discover_v2(first_root, "docs")
            cursor = first["continuation"]["cursor"]
            same_root = discover_v2(first_root, "docs", cursor)
            foreign = discover_v2(second_root, "docs", cursor)

            self.assertEqual(same_root["continuation"]["status"], "complete")
            self.assertEqual(foreign["continuation"]["status"], "rejected")
            self.assertEqual(foreign["content_batch"]["paths"], [])
            self.assertIn("repository_binding", cursor)
            self.assertNotIn(str(first_root), json.dumps(cursor, sort_keys=True))

    def test_local_absence_requires_exact_declared_route_coverage(self):
        local_map = {
            "status": "present-uninspected",
            "routes": [
                {
                    "route": ".local/plan.md",
                    "topics": ["release"],
                    "aliases": ["plan"],
                }
            ],
            "conflicts": [],
        }
        unrelated = docs_checker.route_local_knowledge(
            local_map,
            "missing-topic",
            inspected_routes=[".local/unrelated.md"],
        )
        extra = docs_checker.route_local_knowledge(
            local_map,
            "missing-topic",
            inspected_routes=[".local/plan.md", ".local/unrelated.md"],
        )
        exact = docs_checker.route_local_knowledge(
            local_map,
            "missing-topic",
            inspected_routes=[".local/plan.md"],
        )
        self.assertFalse(unrelated["absence_claim_allowed"])
        self.assertFalse(extra["absence_claim_allowed"])
        self.assertTrue(exact["absence_claim_allowed"])

    def test_task6_accepts_every_real_v2_status_and_candidate_source(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            payloads = []

            docs_root = base / "root-source"
            (docs_root / "docs").mkdir(parents=True)
            (docs_root / "docs" / "README.md").write_text("# Docs\n", encoding="utf-8")
            payloads.append(("root-source", discover_v2(docs_root)))

            direct = base / "direct-child"
            (direct / "component" / "docs").mkdir(parents=True)
            (direct / "component" / "docs" / "README.md").write_text("# Docs\n", encoding="utf-8")
            payloads.append(("direct-child", discover_v2(direct)))

            container = base / "container"
            (container / "packages" / "one" / "docs").mkdir(parents=True)
            (container / "packages" / "one" / "docs" / "README.md").write_text("# Docs\n", encoding="utf-8")
            payloads.append(("container", discover_v2(container)))

            explicit = base / "explicit"
            (explicit / "handbook").mkdir(parents=True)
            (explicit / "handbook" / "guide.md").write_text("# Guide\n", encoding="utf-8")
            payloads.append(("explicit", discover_v2(explicit, "handbook")))

            root_only = base / "root-only"
            root_only.mkdir()
            (root_only / "README.md").write_text("# Root\n", encoding="utf-8")
            payloads.append(("root-only-auto", discover_v2(root_only)))
            payloads.append(("root-only-explicit", discover_v2(root_only, ".")))

            local = base / "local-choice"
            (local / ".local" / "release-plan").mkdir(parents=True)
            payloads.append(("local-choice", discover_v2(local)))

            local_mixed = base / "local-mixed-choice"
            (local_mixed / "docs").mkdir(parents=True)
            (local_mixed / ".local" / "release-plan").mkdir(parents=True)
            payloads.append(("local-mixed-choice", discover_v2(local_mixed)))

            explicit_local = base / "explicit-local"
            (explicit_local / ".local" / "release-plan").mkdir(parents=True)
            payloads.append(
                (
                    "explicit-local",
                    discover_v2(explicit_local, ".local/release-plan"),
                )
            )

            choice = base / "shared-choice"
            (choice / "docs").mkdir(parents=True)
            (choice / "documentation").mkdir()
            payloads.append(("shared-choice", discover_v2(choice)))

            batch = base / "batch"
            (batch / "docs").mkdir(parents=True)
            for index in range(13):
                (batch / "docs" / f"{index:02d}.md").write_text("x", encoding="utf-8")
            limited = discover_v2(batch, "docs")
            payloads.append(("batch-limited", limited))
            payloads.append(
                (
                    "resumed-complete",
                    discover_v2(batch, "docs", limited["continuation"]["cursor"]),
                )
            )
            tampered = dict(limited["continuation"]["cursor"])
            tampered["next_index"] += 1
            payloads.append(("rejected-continuation", discover_v2(batch, "docs", tampered)))

            empty = base / "empty"
            empty.mkdir()
            payloads.append(("adoption-preview", discover_v2(empty)))
            payloads.append(("adoption-preview-explicit", discover_v2(empty, ".")))

            wide = base / "wide"
            wide.mkdir()
            for index in range(129):
                (wide / f"entry-{index:03d}").mkdir()
            payloads.append(("stopped", discover_v2(wide)))

            statuses = set()
            sources = set()
            for label, payload in payloads:
                with self.subTest(label=label):
                    _, errors, _ = self._validated_action(payload)
                    self.assertEqual(errors, [])
                statuses.add(payload["status"])
                sources.update(item["source"] for item in payload["candidates"])
                sources.update(
                    item["source"]
                    for item in payload["local_knowledge"]["candidates"]
                )

            self.assertEqual(
                statuses,
                {"ready", "choice-required", "batch-limited", "stopped", "adoption-preview"},
            )
            self.assertLessEqual(
                {
                    "root",
                    "direct-child",
                    "container:packages",
                    "explicit",
                    "conventional-local-root",
                },
                sources,
            )

    def test_v2_bridge_rejects_coordinated_cross_field_and_cursor_mutations(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "docs").mkdir()
            for index in range(13):
                (root / "docs" / f"{index:02d}.md").write_text("x", encoding="utf-8")
            original = discover_v2(root, "docs")

            mutations = {}
            for field, value in (
                ("selected_scope", "other"),
                ("policy_version", "arbitrary-policy"),
                ("ordering_version", "arbitrary-order"),
                ("discovery_contract_version", 1),
                ("schema_version", 99),
                ("checksum", "0" * 64),
            ):
                changed = deepcopy(original)
                changed["continuation"]["cursor"][field] = value
                mutations[f"cursor-{field}"] = changed

            complete = deepcopy(original)
            complete["continuation"].update(status="complete", cursor=None)
            mutations["complete-continuation-with-truncated-batch"] = complete

            status = deepcopy(original)
            status.update(status="ready", requires_user_action=False, user_action=None)
            mutations["ready-with-truncated-batch"] = status

            scope = deepcopy(original)
            scope.update(selected_scope="other", inspected_scope="other")
            mutations["foreign-selected-scope"] = scope

            boundary = deepcopy(original)
            boundary["content_batch"]["next_boundary"] = "docs/00.md"
            boundary["next_boundary"] = [
                {"kind": "content-files", "path": "docs/00.md"}
            ]
            mutations["coordinated-false-boundary"] = boundary

            visibility = deepcopy(original)
            visibility["local_knowledge"]["selected_visibility"] = None
            visibility["local_knowledge"]["status"] = "optional-map-uninspected"
            mutations["selected-scope-local-visibility-mismatch"] = visibility

            for label, payload in mutations.items():
                with self.subTest(label=label):
                    _, errors, _ = self._validated_action(payload)
                    self.assertIn("retrieval.invalid_doctor_init_discovery", errors)

    def test_task6_mutation_killing_counterexamples_reject_false_success(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "docs").mkdir()
            (root / ".local" / "private-campaign").mkdir(parents=True)
            for index in range(13):
                (root / "docs" / f"guide-{index:02d}.md").write_text(
                    f"# Guide {index}\n",
                    encoding="utf-8",
                )

            first = discover_v2(root)
            second = discover_v2(root, continuation=first["continuation"]["cursor"])
            self.assertEqual(first["continuation"]["status"], "available")
            self.assertEqual(second["continuation"]["status"], "complete")

            mutations = {}

            omitted = deepcopy(second)
            removed = omitted["content_batch"]["paths"].pop()
            omitted["content_batch"]["path_count"] -= 1
            omitted["content_batch"]["bytes"] -= removed["bytes"]
            mutations["omit-last-batch-and-late-fact"] = omitted

            repeated = deepcopy(second)
            repeated["content_batch"] = deepcopy(first["content_batch"])
            mutations["repeat-first-batch-as-complete"] = repeated

            private_in_shared = deepcopy(first)
            private_in_shared["candidates"].append(
                {
                    "path": ".local/private-campaign",
                    "source": "local-conventional",
                    "rank": len(private_in_shared["candidates"]) + 1,
                }
            )
            mutations["private-route-enters-shared-candidates"] = private_in_shared

            blanket_protected = deepcopy(first)
            for item in blanket_protected["protected_surfaces"]["items"]:
                if item["path"].startswith("docs/") or item["path"] == "docs":
                    item["protected"] = True
            mutations["blanket-protects-unknown-host-docs"] = blanket_protected

            falsely_complete = deepcopy(first)
            falsely_complete["status"] = "ready"
            falsely_complete["requires_user_action"] = False
            falsely_complete["user_action"] = None
            falsely_complete["truncated"] = False
            falsely_complete["next_boundary"] = []
            falsely_complete["content_batch"]["complete"] = True
            falsely_complete["content_batch"]["truncated"] = False
            falsely_complete["content_batch"]["next_boundary"] = None
            falsely_complete["continuation"].update(
                status="complete",
                batch=2,
                cursor=None,
                token=None,
            )
            mutations["reports-complete-while-continuation-is-available"] = falsely_complete

            writes = deepcopy(first)
            writes["adoption_preview"]["writes"] = 1
            mutations["writes-during-zero-write-preview"] = writes

            for label, payload in mutations.items():
                with self.subTest(label=label):
                    _, errors, _ = self._validated_action(payload)
                    self.assertIn("retrieval.invalid_doctor_init_discovery", errors)

if __name__ == "__main__":
    unittest.main()
