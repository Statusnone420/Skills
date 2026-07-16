import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock


ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / "skills" / "docs" / "scripts"
CLOSEOUT = SCRIPTS / "init_closeout.py"
sys.path.insert(0, str(SCRIPTS))

import check as docs_checker
from _docs_checker import lifecycle_io
from _docs_checker.init_closeout import MAX_REQUEST_BYTES
from tests.init_v3_fixture import (
    document_change,
    empty_adoption_evidence_v3,
    evidence_v3,
    request_v3,
    whole_file_disposition,
)


def canonical_bytes(value):
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode("utf-8")


def file_digest(path):
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tree_snapshot(root):
    result = {}
    for path in Path(root).rglob("*"):
        if ".git" in path.relative_to(root).parts:
            continue
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            result[relative] = ("symlink", os.readlink(path))
        elif path.is_dir():
            result[relative] = ("directory", None)
        elif path.is_file():
            stat_result = path.stat()
            result[relative] = (
                "file",
                path.read_bytes(),
                stat_result.st_mtime_ns,
                stat_result.st_mode,
            )
    return result


def init_git(root):
    commands = (
        ("init", "-q"),
        ("config", "user.email", "fixture@example.invalid"),
        ("config", "user.name", "Fixture"),
        ("add", "."),
        ("commit", "-qm", "fixture"),
    )
    for arguments in commands:
        completed = subprocess.run(
            ["git", "-C", str(root), *arguments],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise AssertionError(completed.stdout + completed.stderr)


def build_repository(root, *, preexisting_control=False):
    docs = root / "docs"
    docs.mkdir(parents=True)
    for index in range(103):
        relative = docs / ("README.md" if index == 0 else f"page-{index:03d}.md")
        relative.write_text(
            f"# Verified page {index:03d}\n\nUnicode fact: café — ✓.\n",
            encoding="utf-8",
            newline="\n",
        )
    (root / "AGENTS.md").write_text(
        "# Agent instructions\n\nPreserve café paths.\n",
        encoding="utf-8",
        newline="\n",
    )
    (root / ".gitignore").write_text(".local/\n", encoding="utf-8", newline="\n")
    if preexisting_control:
        (root / ".diataxis").mkdir()
    init_git(root)


def finding(index):
    path = f"docs/page-{index + 1:03d}.md"
    evidence = [{"path": path}]
    fingerprint = docs_checker.finding_fingerprint("unreachable-page", evidence)
    return {
        "id": docs_checker.finding_id(fingerprint, {}),
        "fingerprint": fingerprint,
        "priority": "P2",
        "status": "Proposed",
        "summary": "Unreachable maintained page " + ("x" * 420),
        "why": "The maintained route is not linked from the map.",
        "evidence": evidence,
        "recommended_action": "Link the route from the documentation map.",
    }


def build_request(root, operation="preview", approval=None):
    from _docs_checker.init_closeout import _worktree_evidence

    worktree = _worktree_evidence(root)
    paths = sorted(
        (
            path.relative_to(root).as_posix()
            for path in (root / "docs").glob("*.md")
        ),
        key=lambda item: (item.casefold(), item),
    )
    dispositions = [
        whole_file_disposition(
            relative,
            (root / relative).read_bytes(),
            reason="The verified document remains at its current route.",
        )
        for relative in paths
    ]
    map_size = (root / "docs" / "README.md").stat().st_size
    evidence = evidence_v3(dispositions=dispositions)
    evidence["hot_path_bytes"] = {
        point: {
            "value": map_size,
            "unit": "bytes",
            "provenance": [
                {
                    "route": "docs/README.md",
                    "bytes": map_size,
                    "source": "filesystem-stat",
                }
            ],
        }
        for point in ("before", "after")
    }
    evidence["findings"] = {
        "schema_version": 1,
        "findings": [finding(i) for i in range(70)],
    }
    evidence["local_map"] = {
        "schema_version": 2,
        "repository_identity": worktree["repository_identity"],
        "worktree_identity": worktree["worktree_identity"],
        "routes": [],
    }
    evidence["event"].update(
        completed_at="2026-07-14T22:28:00Z",
        reason="Adopted the complete verified documentation corpus.",
        summary="Initialization completed without changing documentation bodies.",
    )
    evidence["source_changes"] = {
        "agents_orientation": True,
        "local_map_ignore": True,
    }
    return request_v3(
        operation,
        evidence=evidence,
        approval=approval,
    )


def run_closeout(root, request, *, raw=None):
    payload = canonical_bytes(request) if raw is None else raw
    return subprocess.run(
        [sys.executable, str(CLOSEOUT), str(root), request.get("operation", "preview")],
        cwd=ROOT,
        input=payload,
        capture_output=True,
        check=False,
    )


def run_init_discovery(root):
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "check.py"),
            str(root),
            "--json",
            "--agent",
            "--init-discovery",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def build_doctor_request(root, operation="preview", approval=None):
    request = build_request(root, operation, approval)
    map_path = root / "docs" / "README.md"
    request["evidence"]["current_truth_routes"] = []
    request["evidence"]["verified_documents"] = [
        {
            "document": "docs/README.md",
            "digest": docs_checker.normalized_content_digest(map_path),
            "sources": [],
            "verified_event": "EVT-00000000",
        }
    ]
    request["evidence"]["trust_coverage"] = {
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
    }
    return request


def assert_failure_envelope(case, payload):
    case.assertIn("classification", payload)
    case.assertIn("boundary", payload)
    case.assertIn("writes", payload)
    case.assertIn("rollback", payload)
    case.assertIs(payload["successful_event_recorded"], False)


class InitCloseoutProcessTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Windows PowerShell transport test")
    def test_documented_init_powershell_transport_executes_preview_and_apply(self):
        init_reference = (SCRIPTS.parent / "references" / "init.md").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "& '<python>' '<installed-skill>/scripts/init_closeout.py' "
            "'<repository-root>' adopt-preview --receipt-file "
            "'<outside-repository-receipt.json>'",
            init_reference,
        )
        self.assertIn(
            "& '<python>' '<installed-skill>/scripts/init_closeout.py' "
            "'<repository-root>' adopt-apply --receipt-file "
            "'<same-outside-repository-receipt.json>' --approval "
            "'<exact-engine-emitted-approval>'",
            init_reference,
        )

        def powershell_literal(value):
            return "'" + str(value).replace("'", "''") + "'"

        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "repository"
            build_repository(root)
            receipt_path = Path(td) / "init-receipt.json"
            before_preview = tree_snapshot(root)

            def invoke(operation, approval=None):
                arguments = (
                    "&",
                    powershell_literal(sys.executable),
                    powershell_literal(CLOSEOUT),
                    powershell_literal(root),
                    operation,
                    "--receipt-file",
                    powershell_literal(receipt_path),
                )
                if approval is not None:
                    arguments += ("--approval", powershell_literal(approval))
                command = " ".join(arguments)
                return subprocess.run(
                    [
                        "powershell",
                        "-NoProfile",
                        "-NonInteractive",
                        "-Command",
                        command,
                    ],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )

            preview_process = invoke("adopt-preview")
            self.assertEqual(
                preview_process.returncode,
                0,
                preview_process.stderr or preview_process.stdout,
            )
            preview = json.loads(preview_process.stdout)
            self.assertEqual(preview["status"], "approval-required")
            self.assertEqual(preview["writes"], 0)
            self.assertEqual(tree_snapshot(root), before_preview)
            self.assertTrue(receipt_path.is_file())
            self.assertEqual(
                preview["approval"],
                "Approve $docs init preview "
                f"{preview['preview_id']} with manifest {preview['manifest_sha256']}",
            )

            apply_process = invoke("adopt-apply", preview["approval"])
            self.assertEqual(
                apply_process.returncode,
                0,
                apply_process.stderr or apply_process.stdout,
            )
            applied = json.loads(apply_process.stdout)
            self.assertEqual(applied["status"], "applied")
            self.assertTrue(applied["successful_event_recorded"])

    def test_empty_adoption_preview_and_apply_create_the_derived_map(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            map_bytes = b"# Adopted documentation\n"
            request = request_v3(
                evidence=empty_adoption_evidence_v3(
                    map_bytes=len(map_bytes),
                ),
                document_changes=[
                    document_change(
                        "CREATE",
                        "README.md",
                        map_bytes,
                        source_item_ids=[],
                    )
                ],
            )

            preview_process = run_closeout(root, request)
            self.assertEqual(preview_process.returncode, 0, preview_process.stdout)
            preview = json.loads(preview_process.stdout)
            self.assertEqual(preview["status"], "approval-required")
            self.assertEqual(preview["selected_scope"], ".")
            self.assertEqual(preview["source_files_revalidated"], 0)
            self.assertEqual(preview["document_change_count"], 1)
            self.assertEqual(preview["corpus_transition"]["starting"]["path_count"], 0)
            self.assertEqual(preview["corpus_transition"]["result"]["path_count"], 1)

            apply_request = copy.deepcopy(request)
            apply_request.update(operation="apply", approval=preview["approval"])
            apply_process = run_closeout(root, apply_request)
            self.assertEqual(apply_process.returncode, 0, apply_process.stdout)
            applied = json.loads(apply_process.stdout)
            self.assertEqual(applied["status"], "applied")
            self.assertEqual(applied["corpus_transition"], preview["corpus_transition"])
            self.assertEqual((root / "README.md").read_bytes(), map_bytes)

            events = docs_checker.load_operational_events(root)
            manifest = json.loads((root / events[-1]["manifest"]["path"]).read_bytes())
            self.assertEqual(manifest["dispositions"], [])
            self.assertEqual(
                manifest["document_results"],
                [
                    {
                        "path": "README.md",
                        "operation": "CREATE",
                        "role": "document-result",
                        "starting_digest": "sha256:ABSENT",
                        "result_digest": file_digest(root / "README.md"),
                        "bytes": len(map_bytes),
                        "source_item_ids": [],
                    }
                ],
            )
            state = docs_checker.load_operational_state(root)
            self.assertEqual(
                state["hot_path_bytes"]["after"],
                {
                    "value": len(map_bytes),
                    "unit": "bytes",
                    "provenance": [
                        {
                            "route": "README.md",
                            "bytes": len(map_bytes),
                            "source": "filesystem-stat",
                        }
                    ],
                },
            )

    def test_result_state_rejects_missing_map_and_current_truth_routes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            build_repository(root)
            map_size = (root / "docs" / "README.md").stat().st_size

            missing_map = build_request(root)
            missing_map["evidence"]["map_path"] = "docs/MISSING.md"
            missing_map["evidence"]["hot_path_bytes"]["after"] = {
                "value": 0,
                "unit": "bytes",
                "provenance": [
                    {
                        "route": "docs/MISSING.md",
                        "bytes": 0,
                        "source": "filesystem-stat",
                    }
                ],
            }
            before = tree_snapshot(root)
            process = run_closeout(root, missing_map)
            self.assertNotEqual(process.returncode, 0)
            payload = json.loads(process.stdout)
            self.assertEqual(payload["classification"], "map-not-in-result-corpus")
            self.assertEqual(tree_snapshot(root), before)

            missing_current = build_request(root)
            missing_current["evidence"]["current_truth_routes"] = [
                "docs/MISSING.md"
            ]
            missing_current["evidence"]["hot_path_bytes"]["after"] = {
                "value": map_size,
                "unit": "bytes",
                "provenance": [
                    {
                        "route": "docs/MISSING.md",
                        "bytes": 0,
                        "source": "filesystem-stat",
                    },
                    {
                        "route": "docs/README.md",
                        "bytes": map_size,
                        "source": "filesystem-stat",
                    },
                ],
            }
            missing_current["evidence"]["trust_coverage"] = {
                "status": "verified",
                "numerator": 1,
                "denominator": 1,
                "routes": [
                    {
                        "route": "docs/MISSING.md",
                        "verified": True,
                        "freshness": "fresh",
                        "sources": ["state:initialized-hot-path"],
                    }
                ],
            }
            process = run_closeout(root, missing_current)
            self.assertNotEqual(process.returncode, 0)
            payload = json.loads(process.stdout)
            self.assertEqual(
                payload["classification"], "current-truth-not-in-result-corpus"
            )
            self.assertEqual(tree_snapshot(root), before)

    def test_empty_adoption_requires_a_result_map_and_exact_after_bytes(self):
        map_bytes = b"# Adopted documentation\n"
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            missing_create = request_v3(
                evidence=empty_adoption_evidence_v3(map_bytes=0),
                document_changes=[],
            )
            before = tree_snapshot(root)
            process = run_closeout(root, missing_create)
            self.assertNotEqual(process.returncode, 0)
            payload = json.loads(process.stdout)
            self.assertEqual(payload["classification"], "map-not-in-result-corpus")
            self.assertEqual(tree_snapshot(root), before)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            wrong_after = request_v3(
                evidence=empty_adoption_evidence_v3(map_bytes=999),
                document_changes=[
                    document_change(
                        "CREATE",
                        "README.md",
                        map_bytes,
                        source_item_ids=[],
                    )
                ],
            )
            before = tree_snapshot(root)
            process = run_closeout(root, wrong_after)
            self.assertNotEqual(process.returncode, 0)
            payload = json.loads(process.stdout)
            self.assertEqual(payload["classification"], "hot-path-after-mismatch")
            self.assertEqual(tree_snapshot(root), before)

    def test_hot_path_bytes_are_bound_to_retained_source_bytes_in_both_planes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            build_repository(root)
            for endpoint in ("before", "after"):
                with self.subTest(endpoint=endpoint):
                    request = build_request(root)
                    observation = request["evidence"]["hot_path_bytes"][endpoint]
                    observation["value"] += 1
                    observation["provenance"][0]["bytes"] += 1
                    before = tree_snapshot(root)
                    process = run_closeout(root, request)
                    self.assertNotEqual(process.returncode, 0)
                    payload = json.loads(process.stdout)
                    self.assertEqual(
                        payload["classification"], f"hot-path-{endpoint}-mismatch"
                    )
                    self.assertEqual(tree_snapshot(root), before)

    def test_prepare_reuses_the_single_bounded_source_read_for_its_receipt(self):
        from _docs_checker import init_closeout as closeout

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            build_repository(root)
            expected_bytes = sum(
                path.stat().st_size for path in (root / "docs").glob("*.md")
            )
            for operation in ("preview", "apply"):
                with self.subTest(operation=operation):
                    request = build_request(root, operation)
                    with mock.patch.object(
                        closeout,
                        "_read_document_bytes_v3",
                        wraps=closeout._read_document_bytes_v3,
                    ) as read_document, mock.patch.object(
                        closeout,
                        "_verify_disposition_sources",
                        side_effect=AssertionError(
                            "prepare must reuse its source cache"
                        ),
                    ):
                        prepared = closeout.prepare_initialization_closeout(
                            root, request
                        )

                    self.assertEqual(read_document.call_count, 103)
                    self.assertEqual(prepared["source_receipt"]["files"], 103)
                    self.assertEqual(
                        prepared["source_receipt"]["bytes"], expected_bytes
                    )

    def test_apply_prepared_plan_rejects_retained_drift_before_transaction_entry(self):
        from _docs_checker import init_closeout as closeout

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            build_repository(root)
            preview = closeout.prepare_initialization_closeout(
                root, build_request(root)
            )
            prepared = closeout.prepare_initialization_closeout(
                root,
                build_request(root, "apply", preview["approval"]),
            )
            tampered = copy.deepcopy(prepared)
            tampered["plan"]["retained_source_probes"][0]["digest"] = (
                "sha256:" + "0" * 64
            )
            tampered_response = closeout.apply_response(
                root,
                tampered,
                tampered["approval"],
            )
            self.assertEqual(tampered_response["status"], "closeout-failed")
            self.assertEqual(
                tampered_response["classification"],
                "transaction-authorization-mismatch",
            )
            self.assertFalse((root / ".diataxis").exists())

            retained = root / "docs" / "page-001.md"
            retained.write_bytes(
                retained.read_bytes().replace(b"page 001", b"page 999")
            )
            before = tree_snapshot(root)

            with mock.patch.object(
                lifecycle_io,
                "_prepare_recovery_area_v3",
                wraps=lifecycle_io._prepare_recovery_area_v3,
            ) as prepare_recovery:
                response = closeout.apply_response(
                    root,
                    prepared,
                    prepared["approval"],
                )

            self.assertEqual(response["status"], "verification-failed")
            self.assertEqual(response["boundary"], "pre-apply-verification")
            self.assertFalse(response["successful_event_recorded"])
            self.assertTrue(response["rollback"]["complete"])
            prepare_recovery.assert_not_called()
            self.assertEqual(
                len(prepared["plan"]["retained_source_probes"]),
                103,
            )
            self.assertEqual(tree_snapshot(root), before)
            self.assertFalse((root / ".diataxis").exists())

    def test_pre_event_verification_rejects_retained_drift_and_rolls_back(self):
        from _docs_checker import init_closeout as closeout

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            build_repository(root)
            preview = closeout.prepare_initialization_closeout(
                root, build_request(root)
            )
            prepared = closeout.prepare_initialization_closeout(
                root,
                build_request(root, "apply", preview["approval"]),
            )
            retained = root / "docs" / "page-001.md"
            original = retained.read_bytes()
            drifted = original.replace(b"page 001", b"page 999")
            real_verify = lifecycle_io._verify_pre_event_v3
            calls = []

            def mutate_before_event(repo, plan, journal):
                retained.write_bytes(drifted)
                calls.append(journal["phase"])
                return real_verify(repo, plan, journal)

            with mock.patch.object(
                lifecycle_io,
                "_verify_pre_event_v3",
                side_effect=mutate_before_event,
            ):
                response = closeout.apply_response(
                    root,
                    prepared,
                    prepared["approval"],
                )

            self.assertEqual(calls, ["installing"])
            self.assertEqual(response["status"], "closeout-failed")
            self.assertFalse(response["successful_event_recorded"])
            self.assertTrue(response["rollback"]["complete"])
            self.assertEqual(retained.read_bytes(), drifted)
            self.assertFalse((root / ".diataxis").exists())

    def test_event_boundary_rechecks_retained_bodies_after_terminal_preparation(self):
        from _docs_checker import init_closeout as closeout

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            build_repository(root)
            preview = closeout.prepare_initialization_closeout(
                root, build_request(root)
            )
            prepared = closeout.prepare_initialization_closeout(
                root,
                build_request(root, "apply", preview["approval"]),
            )
            retained = root / "docs" / "page-001.md"
            drifted = retained.read_bytes().replace(b"page 001", b"page 999")
            real_terminal = lifecycle_io._write_terminal_marker_v3

            def write_terminal_then_drift(*args, **kwargs):
                result = real_terminal(*args, **kwargs)
                retained.write_bytes(drifted)
                return result

            with mock.patch.object(
                lifecycle_io,
                "_write_terminal_marker_v3",
                side_effect=write_terminal_then_drift,
            ):
                response = closeout.apply_response(
                    root,
                    prepared,
                    prepared["approval"],
                )

            self.assertEqual(response["status"], "closeout-failed")
            self.assertFalse(response["successful_event_recorded"])
            self.assertTrue(response["rollback"]["complete"])
            self.assertEqual(retained.read_bytes(), drifted)
            self.assertFalse((root / ".diataxis").exists())

    def test_transformative_plan_reuses_start_digest_across_identity_convergence(self):
        from _docs_checker import init_closeout as closeout

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = b"# Documentation\n"
            archived = b"# Historical page\n"
            (root / "docs").mkdir(parents=True)
            (root / "docs" / "README.md").write_bytes(source)
            (root / "docs" / "historical.md").write_bytes(archived)
            init_git(root)

            retained = whole_file_disposition("docs/README.md", source)
            archived_item = whole_file_disposition(
                "docs/historical.md",
                archived,
                disposition="ARCHIVED",
                target="docs/archive/historical.md",
                recovery={
                    "kind": "archive",
                    "mode": "planned",
                    "path": "docs/archive/historical.md",
                    "digest": "sha256:" + hashlib.sha256(archived).hexdigest(),
                },
            )
            request = request_v3(
                evidence=evidence_v3(
                    dispositions=sorted(
                        [retained, archived_item],
                        key=lambda item: (
                            item["path"].casefold(),
                            item["path"],
                            item["item_id"],
                        ),
                    )
                ),
                document_changes=[
                    document_change(
                        "CREATE",
                        "docs/archive/historical.md",
                        archived,
                        source_item_ids=[archived_item["item_id"]],
                    ),
                    document_change(
                        "DELETE",
                        "docs/historical.md",
                        source_item_ids=[archived_item["item_id"]],
                    ),
                ],
            )
            historical = root / "docs" / "historical.md"
            path_digest_calls = []
            real_path_digest = lifecycle_io._path_digest

            def count_path_digest(path, capacity):
                if os.path.normcase(os.path.abspath(path)) == os.path.normcase(
                    os.path.abspath(historical)
                ):
                    path_digest_calls.append(os.fspath(path))
                return real_path_digest(path, capacity)

            with mock.patch.object(
                lifecycle_io,
                "_path_digest",
                side_effect=count_path_digest,
            ):
                prepared = closeout.prepare_initialization_closeout(root, request)

            self.assertEqual(path_digest_calls, [os.fspath(historical)])
            self.assertEqual(
                prepared["plan"]["retained_source_probes"],
                [
                    {
                        "path": "docs/README.md",
                        "digest": "sha256:" + hashlib.sha256(source).hexdigest(),
                    }
                ],
            )

    def test_result_routes_require_canonical_case_and_cannot_name_a_deleted_map(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            build_repository(root)
            wrong_case = build_request(root)
            map_size = (root / "docs" / "README.md").stat().st_size
            wrong_case["evidence"]["map_path"] = "docs/readme.md"
            for endpoint in ("before", "after"):
                wrong_case["evidence"]["hot_path_bytes"][endpoint] = {
                    "value": map_size,
                    "unit": "bytes",
                    "provenance": [
                        {
                            "route": "docs/readme.md",
                            "bytes": map_size,
                            "source": "filesystem-stat",
                        }
                    ],
                }
            process = run_closeout(root, wrong_case)
            self.assertNotEqual(process.returncode, 0)
            self.assertEqual(
                json.loads(process.stdout)["classification"],
                "map-not-in-result-corpus",
            )

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = b"# Documentation\n"
            (root / "docs").mkdir(parents=True)
            (root / "docs" / "README.md").write_bytes(source)
            init_git(root)
            commit = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            blob = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD:docs/README.md"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            item = whole_file_disposition(
                "docs/README.md",
                source,
                disposition="DISCARDED",
                recovery={
                    "kind": "git",
                    "commit": commit,
                    "blob": blob,
                    "digest": "sha256:" + hashlib.sha256(source).hexdigest(),
                },
            )
            request = request_v3(
                evidence=evidence_v3(dispositions=[item]),
                document_changes=[
                    document_change(
                        "DELETE",
                        "docs/README.md",
                        source_item_ids=[item["item_id"]],
                    )
                ],
            )
            before = tree_snapshot(root)
            process = run_closeout(root, request)
            self.assertNotEqual(process.returncode, 0)
            payload = json.loads(process.stdout)
            self.assertEqual(payload["classification"], "map-not-in-result-corpus")
            self.assertEqual(tree_snapshot(root), before)

    def test_apply_revalidates_hot_path_result_binding_before_writes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            map_bytes = b"# Adopted documentation\n"
            preview_request = request_v3(
                evidence=empty_adoption_evidence_v3(map_bytes=len(map_bytes)),
                document_changes=[
                    document_change(
                        "CREATE",
                        "README.md",
                        map_bytes,
                        source_item_ids=[],
                    )
                ],
            )
            preview = json.loads(run_closeout(root, preview_request).stdout)
            apply_request = copy.deepcopy(preview_request)
            apply_request.update(operation="apply", approval=preview["approval"])
            apply_request["evidence"]["hot_path_bytes"]["after"]["value"] = 999
            apply_request["evidence"]["hot_path_bytes"]["after"]["provenance"][0][
                "bytes"
            ] = 999
            before = tree_snapshot(root)
            process = run_closeout(root, apply_request)
            self.assertNotEqual(process.returncode, 0)
            payload = json.loads(process.stdout)
            self.assertEqual(payload["status"], "stale-preview")
            self.assertEqual(payload["classification"], "hot-path-after-mismatch")
            self.assertEqual(tree_snapshot(root), before)

    def test_large_findings_preview_and_apply_are_exact_event_last_and_artifact_free(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            build_repository(root)
            request = build_request(root)
            self.assertGreater(len(canonical_bytes(request["evidence"]["findings"])), 40 * 1024)
            before = tree_snapshot(root)

            preview_process = run_closeout(root, request)
            self.assertEqual(preview_process.returncode, 0, preview_process.stderr)
            preview = json.loads(preview_process.stdout)
            self.assertEqual(preview["status"], "approval-required")
            self.assertEqual(preview["writes"], 0)
            self.assertEqual(tree_snapshot(root), before)

            apply_request = build_request(root, "apply", preview["approval"])
            apply_process = run_closeout(root, apply_request)
            self.assertEqual(apply_process.returncode, 0, apply_process.stderr)
            applied = json.loads(apply_process.stdout)
            self.assertEqual(
                set(applied),
                {
                    "schema_version",
                    "status",
                    "preview_id",
                    "manifest_sha256",
                    "transaction_id",
                    "event_id",
                    "corpus_transition",
                    "verification",
                    "rollback",
                    "successful_event_recorded",
                },
            )
            self.assertEqual(applied["status"], "applied")
            self.assertEqual(applied["preview_id"], preview["preview_id"])
            self.assertEqual(applied["manifest_sha256"], preview["manifest_sha256"])
            self.assertEqual(applied["corpus_transition"], preview["corpus_transition"])
            self.assertTrue(applied["verification"]["exact_installed_bytes"])
            self.assertTrue(applied["verification"]["event_last"])
            self.assertTrue(applied["verification"]["local_map_ignored"])
            self.assertEqual(
                applied["rollback"],
                {
                    "required": False,
                    "complete": True,
                    "documents": "not-required",
                    "controls": "not-required",
                    "cleanup": "not-required",
                },
            )

            control = root / ".diataxis"
            findings_bytes = (control / "findings.json").read_bytes()
            self.assertGreater(len(findings_bytes), 40 * 1024)
            self.assertEqual(json.loads(findings_bytes), apply_request["evidence"]["findings"])
            state = docs_checker.load_operational_state(root)
            findings = docs_checker.load_operational_findings(root)
            events = docs_checker.load_operational_events(root)
            self.assertEqual(len(findings["findings"]), 70)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["event_id"], applied["event_id"])
            self.assertEqual(
                set(events[0]["changed_paths"]), set(events[0]["transaction_targets"])
            )
            self.assertEqual(state["last_completed_event"], applied["event_id"])
            manifest_path = root / events[0]["manifest"]["path"]
            manifest_bytes = manifest_path.read_bytes()
            self.assertEqual(
                hashlib.sha256(manifest_bytes).hexdigest(), preview["manifest_sha256"]
            )
            self.assertEqual(len(json.loads(manifest_bytes)["dispositions"]), 103)
            self.assertEqual(docs_checker.inspect_operational_memory(root), [])
            from _docs_checker.init_closeout import git_identity_evidence

            post_apply_identity = git_identity_evidence(root)
            local_inspection = docs_checker.inspect_local_map(
                root,
                repository_identity=post_apply_identity["repository_identity"],
                worktree_identity=post_apply_identity["worktree_identity"],
            )
            self.assertEqual(local_inspection["binding"], "matched")
            self.assertFalse(any(path.name.startswith(".docs-txn-") for path in root.rglob("*")))
            self.assertIn("Repository knowledge starts", (root / "AGENTS.md").read_text("utf-8"))
            self.assertIn(".diataxis/local-map.json", (root / ".gitignore").read_text("utf-8"))

    def test_apply_rejects_approval_manifest_source_and_target_drift_without_writes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            build_repository(root)
            request = build_request(root)
            preview = json.loads(run_closeout(root, request).stdout)

            alternate_preview_id = (
                "INIT-"
                + ("0" if preview["preview_id"][5] != "0" else "1")
                + preview["preview_id"][6:]
            )
            cases = []
            cases.append(
                (
                    "approval",
                    preview["approval"].replace(
                        preview["preview_id"], alternate_preview_id
                    ),
                )
            )
            cases.append(
                (
                    "manifest",
                    preview["approval"].replace(
                        preview["manifest_sha256"], "0" * 64
                    ),
                )
            )
            for label, approval in cases:
                with self.subTest(label=label):
                    before = tree_snapshot(root)
                    process = run_closeout(root, build_request(root, "apply", approval))
                    self.assertNotEqual(process.returncode, 0)
                    payload = json.loads(process.stdout)
                    self.assertEqual(payload["status"], "stale-preview")
                    self.assertFalse(payload["successful_event_recorded"])
                    self.assertEqual(tree_snapshot(root), before)

            (root / "docs" / "page-001.md").write_text("# drift\n", encoding="utf-8")
            source_before = tree_snapshot(root)
            process = run_closeout(root, build_request(root, "apply", preview["approval"]))
            self.assertNotEqual(process.returncode, 0)
            payload = json.loads(process.stdout)
            self.assertIn(payload["status"], {"stale-preview", "invalid-request"})
            self.assertEqual(tree_snapshot(root), source_before)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            build_repository(root)
            preview = json.loads(run_closeout(root, build_request(root)).stdout)
            (root / ".diataxis").mkdir()
            (root / ".diataxis" / "state.json").write_text("{}\n", encoding="utf-8")
            target_before = tree_snapshot(root)
            process = run_closeout(root, build_request(root, "apply", preview["approval"]))
            self.assertNotEqual(process.returncode, 0)
            self.assertEqual(json.loads(process.stdout)["status"], "stale-preview")
            self.assertEqual(tree_snapshot(root), target_before)


class InitRepeatDoctorTests(unittest.TestCase):
    ALREADY_INITIALIZED = (
        "This repository is already initialized. "
        "Run $docs doctor to diagnose or improve it."
    )

    def _apply_doctor_fixture(self, root):
        build_repository(root)
        links = "\n".join(
            f"- [Verified page {index:03d}](page-{index:03d}.md)"
            for index in range(71, 103)
        )
        (root / "docs" / "README.md").write_text(
            "# Verified page 000\n\nUnicode fact: café — ✓.\n\n"
            "## Current routes\n\n"
            + links
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        preview_request = build_doctor_request(root)
        preview_process = run_closeout(root, preview_request)
        self.assertEqual(preview_process.returncode, 0, preview_process.stderr)
        preview = json.loads(preview_process.stdout)
        apply_request = build_doctor_request(root, "apply", preview["approval"])
        apply_process = run_closeout(root, apply_request)
        self.assertEqual(apply_process.returncode, 0, apply_process.stderr)
        return preview_request, json.loads(apply_process.stdout)

    def test_successful_init_discovery_preflight_is_zero_traversal_and_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._apply_doctor_fixture(root)
            before = tree_snapshot(root)

            process = run_init_discovery(root)

            self.assertEqual(process.returncode, 0, process.stderr)
            payload = json.loads(process.stdout)
            self.assertEqual(payload["mode"], "init-preflight")
            self.assertEqual(payload["status"], "already-initialized")
            self.assertEqual(payload["message"], self.ALREADY_INITIALIZED)
            self.assertEqual(payload["map"], "docs/README.md")
            self.assertEqual(payload["baseline"]["last_verified_score"], 83)
            self.assertEqual(payload["structural_score"], 83)
            self.assertEqual(payload["candidate_traversal"], 0)
            self.assertEqual(payload["content_reads"], 0)
            self.assertEqual(payload["writes"], 0)
            self.assertEqual(tree_snapshot(root), before)

            output = io.StringIO()
            with mock.patch.object(
                docs_checker,
                "discover_init_scope",
                side_effect=AssertionError("adoption discovery must not run"),
            ), mock.patch(
                "_docs_checker.memory._inspect_protected_intent_sources",
                side_effect=AssertionError("preflight must not read documentation bodies"),
            ), redirect_stdout(output):
                exit_code = docs_checker.main(
                    [str(root), "--json", "--agent", "--init-discovery"]
                )
            self.assertEqual(exit_code, 0)
            self.assertEqual(json.loads(output.getvalue())["status"], "already-initialized")

    def test_initialized_preflight_uses_only_stable_git_identity_without_status(self):
        from _docs_checker import init_closeout as closeout

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._apply_doctor_fixture(root)
            calls = []
            real_run_git = closeout._run_git

            def stable_only(repository, *arguments):
                calls.append(arguments)
                if arguments and arguments[0] == "status":
                    self.fail("Init preflight must not enumerate the worktree with git status")
                return real_run_git(repository, *arguments)

            with mock.patch.object(closeout, "_run_git", stable_only):
                payload = closeout.inspect_initialization_preflight(root)

            self.assertEqual(payload["status"], "already-initialized")
            self.assertEqual(
                calls,
                [
                    ("rev-parse", "--show-toplevel"),
                    ("rev-parse", "--path-format=absolute", "--git-common-dir"),
                    ("rev-parse", "--path-format=absolute", "--git-dir"),
                ],
            )

    def test_torn_or_incomplete_operational_state_routes_to_doctor_without_adoption(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._apply_doctor_fixture(root)
            control = root / ".diataxis"
            originals = {
                path.relative_to(control).as_posix(): path.read_bytes()
                for path in control.rglob("*")
                if path.is_file()
            }
            event = docs_checker.load_operational_events(root)[0]
            manifest_relative = event["manifest"]["path"].removeprefix(".diataxis/")

            def write_legacy_state():
                state = json.loads(originals["state.json"])
                state = {
                    key: value
                    for key, value in state.items()
                    if key
                    in {
                        "schema_version",
                        "initialized",
                        "rubric",
                        "cold_paths",
                        "verified_documents",
                        "protected_intent",
                        "last_completed_event",
                    }
                }
                state["schema_version"] = 1
                (control / "state.json").write_bytes(canonical_bytes(state))

            def write_open_p0_finding():
                findings = json.loads(originals["findings.json"])
                findings["findings"][0]["priority"] = "P0"
                (control / "findings.json").write_bytes(canonical_bytes(findings))

            def restore():
                for path in sorted(control.rglob("*"), reverse=True):
                    if path.is_file():
                        path.unlink()
                for relative, data in originals.items():
                    target = control / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)

            cases = {
                "truncated-state": lambda: (control / "state.json").write_bytes(b"{\n"),
                "legacy-incomplete-state": write_legacy_state,
                "missing-event": lambda: (control / "events.jsonl").unlink(),
                "missing-manifest": lambda: (control / manifest_relative).unlink(),
                "stale-local-map": lambda: (control / "local-map.json").write_bytes(b"{}\n"),
                "orphan-transaction": lambda: (control / ".docs-txn-orphan").write_bytes(b"x"),
                "open-p0-finding": write_open_p0_finding,
            }
            for label, corrupt in cases.items():
                with self.subTest(label=label):
                    restore()
                    corrupt()
                    before = tree_snapshot(root)
                    process = run_init_discovery(root)
                    self.assertEqual(process.returncode, 2, process.stderr)
                    payload = json.loads(process.stdout)
                    self.assertEqual(payload["mode"], "init-preflight")
                    self.assertEqual(payload["status"], "state-conflict")
                    self.assertEqual(payload["user_action"], "run-doctor")
                    self.assertEqual(payload["candidate_traversal"], 0)
                    self.assertEqual(payload["content_reads"], 0)
                    self.assertEqual(payload["writes"], 0)
                    self.assertEqual(tree_snapshot(root), before)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            build_repository(root, preexisting_control=True)
            before = tree_snapshot(root)
            process = run_init_discovery(root)
            self.assertEqual(process.returncode, 2, process.stderr)
            payload = json.loads(process.stdout)
            self.assertEqual(payload["status"], "state-conflict")
            self.assertEqual(payload["candidate_traversal"], 0)
            self.assertEqual(payload["content_reads"], 0)
            self.assertEqual(tree_snapshot(root), before)


    def test_second_public_init_does_not_create_preview_manifest_or_event(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            preview_request, applied = self._apply_doctor_fixture(root)
            before = tree_snapshot(root)
            events_before = docs_checker.load_operational_events(root)
            manifests_before = sorted((root / ".diataxis" / "manifests").iterdir())

            process = run_closeout(root, preview_request)

            self.assertEqual(process.returncode, 0, process.stderr)
            payload = json.loads(process.stdout)
            self.assertEqual(payload["status"], "already-initialized")
            self.assertEqual(payload["message"], self.ALREADY_INITIALIZED)
            self.assertEqual(payload["writes"], 0)
            self.assertNotIn("preview_id", payload)
            self.assertEqual(docs_checker.load_operational_events(root), events_before)
            self.assertEqual(
                sorted((root / ".diataxis" / "manifests").iterdir()),
                manifests_before,
            )
            self.assertEqual(tree_snapshot(root), before)
            self.assertEqual(events_before[-1]["event_id"], applied["event_id"])

    def test_doctor_checker_consumes_real_closeout_state_and_findings_without_writes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._apply_doctor_fixture(root)
            before = tree_snapshot(root)

            process = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "check.py"),
                    str(root),
                    "--json",
                    "--agent",
                    "--map",
                    "docs/README.md",
                    "--scope",
                    "docs",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(process.returncode, 0, process.stderr)
            payload = json.loads(process.stdout)
            state = docs_checker.load_operational_state(root)
            findings = docs_checker.load_operational_findings(root)["findings"]
            events = docs_checker.load_operational_events(root)
            self.assertEqual(state["schema_version"], 3)
            self.assertEqual(len(events), 1)
            self.assertEqual(len(findings), 70)
            self.assertEqual(len(json.loads((root / events[0]["manifest"]["path"]).read_bytes())["dispositions"]), 103)
            self.assertEqual(payload["health"]["percentage"], 83)
            self.assertEqual(payload["health"]["trust_status"], "verified")
            self.assertEqual(payload["health"]["coverage"]["numerator"], 1)
            self.assertEqual(payload["health"]["coverage"]["denominator"], 1)
            self.assertEqual(payload["health"]["open_priorities"], {"P0": 0, "P1": 0, "P2": 70})
            self.assertEqual(len(payload["findings"]), 70)
            self.assertTrue(
                all(item["kind"] == "unreachable" for item in payload["findings"])
            )
            self.assertEqual(docs_checker.inspect_operational_memory(root), [])
            self.assertEqual(tree_snapshot(root), before)


class InitCloseoutBoundaryTests(unittest.TestCase):

    def test_request_file_transport_is_bounded_and_missing_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "repository"
            build_repository(root)

            request_path = Path(td) / "init-request.json"
            request_path.write_bytes(canonical_bytes(build_request(root)))
            before = tree_snapshot(root)
            selected_file_process = subprocess.run(
                [
                    sys.executable,
                    str(CLOSEOUT),
                    str(root),
                    "preview",
                    "--request-file",
                    str(request_path),
                ],
                cwd=ROOT,
                input=b"{malformed-stdin",
                capture_output=True,
                check=False,
            )
            self.assertEqual(selected_file_process.returncode, 0)
            self.assertEqual(
                json.loads(selected_file_process.stdout)["status"],
                "approval-required",
            )
            self.assertEqual(tree_snapshot(root), before)

            missing = Path(td) / "missing-request.json"
            before = tree_snapshot(root)
            missing_process = subprocess.run(
                [
                    sys.executable,
                    str(CLOSEOUT),
                    str(root),
                    "preview",
                    "--request-file",
                    str(missing),
                ],
                cwd=ROOT,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(missing_process.returncode, 0)
            missing_payload = json.loads(missing_process.stdout)
            self.assertEqual(missing_payload["classification"], "request-unavailable")
            self.assertEqual(missing_payload["writes"], 0)
            self.assertEqual(tree_snapshot(root), before)

            oversized = Path(td) / "oversized-request.json"
            oversized.write_bytes(b"{" + b"x" * MAX_REQUEST_BYTES)
            before = tree_snapshot(root)
            oversized_process = subprocess.run(
                [
                    sys.executable,
                    str(CLOSEOUT),
                    str(root),
                    "preview",
                    "--request-file",
                    str(oversized),
                ],
                cwd=ROOT,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(oversized_process.returncode, 0)
            oversized_payload = json.loads(oversized_process.stdout)
            self.assertEqual(oversized_payload["classification"], "request-capacity")
            self.assertEqual(oversized_payload["writes"], 0)
            self.assertEqual(tree_snapshot(root), before)

    def test_boundary_rejects_duplicate_oversized_extra_traversal_and_unicode_is_safe(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            build_repository(root)
            request = build_request(root)
            unicode_process = run_closeout(root, request)
            self.assertEqual(unicode_process.returncode, 0, unicode_process.stderr)
            json.loads(unicode_process.stdout.decode("utf-8"))

            duplicate = canonical_bytes(request).replace(
                b',"schema_version":3}',
                b',"schema_version":3,"schema_version":3}',
                1,
            )
            duplicate_process = run_closeout(root, request, raw=duplicate)
            self.assertNotEqual(duplicate_process.returncode, 0)
            self.assertEqual(json.loads(duplicate_process.stdout)["status"], "invalid-request")

            oversized_process = run_closeout(
                root,
                request,
                raw=b"{" + b"x" * MAX_REQUEST_BYTES,
            )
            self.assertNotEqual(oversized_process.returncode, 0)
            self.assertEqual(json.loads(oversized_process.stdout)["classification"], "request-capacity")

            extra = copy.deepcopy(request)
            extra["target"] = "outside.json"
            extra_process = run_closeout(root, extra)
            self.assertNotEqual(extra_process.returncode, 0)
            self.assertEqual(json.loads(extra_process.stdout)["status"], "invalid-request")

            traversal = copy.deepcopy(request)
            traversal["evidence"]["dispositions"][0]["path"] = "../outside.md"
            traversal["evidence"]["dispositions"][0]["item_id"] = "../outside.md#<whole-file>"
            traversal_process = run_closeout(root, traversal)
            self.assertNotEqual(traversal_process.returncode, 0)
            self.assertEqual(json.loads(traversal_process.stdout)["status"], "invalid-request")

    def test_event_privacy_is_strict_and_changed_paths_are_derived(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            build_repository(root)
            for label, mutate in (
                (
                    "private-changed-path",
                    lambda request: request["evidence"]["event"].update(
                        changed_paths=[".local/private-plan.md"]
                    ),
                ),
                (
                    "non-string-reason",
                    lambda request: request["evidence"]["event"].update(
                        reason={"body": "private"}
                    ),
                ),
                (
                    "private-summary",
                    lambda request: request["evidence"]["event"].update(
                        summary="Read .local/private-plan.md"
                    ),
                ),
            ):
                with self.subTest(label=label):
                    request = build_request(root)
                    mutate(request)
                    before = tree_snapshot(root)
                    process = run_closeout(root, request)
                    self.assertNotEqual(process.returncode, 0)
                    payload = json.loads(process.stdout)
                    self.assertEqual(payload["status"], "invalid-request")
                    assert_failure_envelope(self, payload)
                    self.assertEqual(tree_snapshot(root), before)

    def test_persisted_event_and_disposition_text_rejects_embedded_routes_without_writes(self):
        unsafe_texts = (
            "See [.local/secret.md]",
            "See route=.local/secret.md",
            "See route:.local/secret.md",
            r"See route=C:\private\secret.md",
            "See route=/home/private/secret.md",
            r"See route=\\server\share\secret.md",
            r"See route=\\server/share/secret.md",
            "See route=//server/share/secret.md",
            "See file:///home/private/secret.md",
            "See file://server/share/secret.md",
            "See file://localhost/home/private/secret.md",
            r"See https://example.com/?file=C:\private\secret.md",
            "See https://example.com/.local/secret.md",
            "See https://example.com/?file=../secret.md",
            "See https://example.com/?file=file:///home/private/secret.md",
            "See https://example.com/?file=/home/private/secret.md",
            "See https://example.com/#source=/Users/Anthony/private.md",
            "See https://example.com/?file=%2Fhome%2Fprivate%2Fsecret.md",
            "See https://example.com/?file=//server/share/private.md",
            "See https://example.com/?file=/workspace/private/secret.md",
            "See https://example.com/?file=C%3A%5CUsers%5CAnthony%5Csecret.md",
            "See https://example.com/?file=%2Elocal%2Fsecret.md",
            "See https://example.com/?file=%2E%2E%2Fsecret.md",
            "See https://example.com/?file=file%3A%2F%2F%2Fhome%2Fsecret.md",
            "See https://example.com/?file=%5CUsers%5CAnthony%5Csecret.md",
            "See route=///home/private/secret.md",
            r"See route=\\\server\share\secret.md",
            r"See route=\\server",
            r"See route=\Users",
            r"See route=\Users\private\secret.md",
            r"See route=\secret.md",
            r"See route=\.ssh\id_rsa",
            r"See route=\_private\secret.md",
            r"See route=\folder\.ssh",
            r"See route=\folder\_private",
            r"See route=\folder\-private",
            "See route=//home/private/secret.md",
            "See route=../secret.md",
        )
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            build_repository(root)
            for field, mutate, classification in (
                (
                    "event-reason",
                    lambda request, text: request["evidence"]["event"].update(
                        reason=text
                    ),
                    "invalid-event-reason",
                ),
                (
                    "disposition-reason",
                    lambda request, text: request["evidence"]["dispositions"][0].update(
                        reason=text
                    ),
                    "invalid-disposition-reason",
                ),
            ):
                for text in unsafe_texts:
                    with self.subTest(field=field, text=text):
                        request = build_request(root)
                        mutate(request, text)
                        before = tree_snapshot(root)
                        process = run_closeout(root, request)
                        self.assertNotEqual(process.returncode, 0)
                        payload = json.loads(process.stdout)
                        self.assertEqual(payload["status"], "invalid-request")
                        self.assertEqual(payload["classification"], classification)
                        assert_failure_envelope(self, payload)
                        self.assertEqual(tree_snapshot(root), before)

    def test_persisted_findings_reject_private_or_unsafe_routes_recursively_without_writes(self):
        unsafe_texts = (
            "See [.local/secret.md]",
            r"See route=C:\private\secret.md",
            r"See route=\\server\share\secret.md",
            "See route=/home/private/secret.md",
            "See route=../secret.md",
        )
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            build_repository(root)
            mutations = (
                ("summary", lambda record, text: record.update(summary=text)),
                ("why", lambda record, text: record.update(why=text)),
                (
                    "recommended-action",
                    lambda record, text: record.update(recommended_action=text),
                ),
                (
                    "nested-evidence",
                    lambda record, text: record["evidence"][0].update(
                        context={"detail": text}
                    ),
                ),
                (
                    "optional-detail",
                    lambda record, text: record.update(detail={"text": text}),
                ),
            )
            for field, mutate in mutations:
                for text in unsafe_texts:
                    with self.subTest(field=field, text=text):
                        request = build_request(root)
                        mutate(request["evidence"]["findings"]["findings"][0], text)
                        before = tree_snapshot(root)
                        process = run_closeout(root, request)
                        self.assertNotEqual(process.returncode, 0)
                        payload = json.loads(process.stdout)
                        self.assertEqual(payload["status"], "invalid-request")
                        assert_failure_envelope(self, payload)
                        self.assertEqual(tree_snapshot(root), before)

    def test_persisted_state_text_rejects_private_or_unsafe_routes_without_writes(self):
        base_intent = {
            "id": "INTENT-1",
            "intent_key": "preserve-agent-contract",
            "source": "docs/README.md#verified-page-000",
            "preserve": True,
            "status": "active",
        }
        mutations = (
            (
                "intent-key",
                lambda record: record.update(intent_key="See .local/secret.md"),
            ),
            (
                "source-anchor",
                lambda record: record.update(
                    source="docs/README.md#see:/home/private/secret.md"
                ),
            ),
            (
                "status",
                lambda record: record.update(status=r"See C:\private\secret.md"),
            ),
        )
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            build_repository(root)
            for field, mutate in mutations:
                with self.subTest(field=field):
                    request = build_request(root)
                    intent = copy.deepcopy(base_intent)
                    mutate(intent)
                    request["evidence"]["protected_intent"] = [intent]
                    before = tree_snapshot(root)
                    process = run_closeout(root, request)
                    self.assertNotEqual(process.returncode, 0)
                    payload = json.loads(process.stdout)
                    self.assertEqual(payload["status"], "invalid-request")
                    assert_failure_envelope(self, payload)
                    self.assertEqual(tree_snapshot(root), before)

    def test_persisted_finding_object_keys_reject_private_routes_without_writes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            build_repository(root)
            for field, mutate in (
                (
                    "evidence-key",
                    lambda record: record["evidence"][0].update(
                        {".local/secret.md": "private route key"}
                    ),
                ),
                (
                    "optional-key",
                    lambda record: record.update(
                        {".local/secret.md": "private route key"}
                    ),
                ),
            ):
                with self.subTest(field=field):
                    request = build_request(root)
                    mutate(request["evidence"]["findings"]["findings"][0])
                    before = tree_snapshot(root)
                    process = run_closeout(root, request)
                    self.assertNotEqual(process.returncode, 0)
                    payload = json.loads(process.stdout)
                    self.assertEqual(payload["status"], "invalid-request")
                    assert_failure_envelope(self, payload)
                    self.assertEqual(tree_snapshot(root), before)

    def test_persisted_text_allows_public_https_links(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            build_repository(root)
            safe_texts = (
                "See https://example.com/docs/page.md",
                "See https://example.com/home/private/page.md",
                "See https://example.com/login?next=/docs/home",
                "See https://example.com/app#/docs/home",
                r"Use \*literal\* as escaped Markdown emphasis syntax.",
                r"Use \_literal\_ as escaped Markdown emphasis syntax.",
                r"Use \-literal\- as escaped Markdown punctuation.",
                r"Use regex \d+ to match one or more digits.",
                r"Use LaTeX \alpha for the Greek letter.",
            )
            for text in safe_texts:
                with self.subTest(text=text):
                    request = build_request(root)
                    request["evidence"]["event"]["summary"] = text
                    request["evidence"]["dispositions"][0]["reason"] = text
                    request["evidence"]["findings"]["findings"][0]["why"] = text
                    before = tree_snapshot(root)
                    process = run_closeout(root, request)
                    self.assertEqual(process.returncode, 0, process.stdout)
                    self.assertEqual(
                        json.loads(process.stdout)["status"], "approval-required"
                    )
                    self.assertEqual(tree_snapshot(root), before)

    def test_public_process_rejects_unpaired_migration_without_writes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            build_repository(root)
            request = build_request(root)
            item = request["evidence"]["dispositions"][0]
            target = "docs/migrated/page-001.md"
            recovery_path = "docs/archive/page-001.md"
            item.update(
                {
                    "disposition": "MIGRATED",
                    "target": target,
                    "recovery": {
                        "kind": "archive",
                        "mode": "planned",
                        "path": recovery_path,
                        "digest": item["source_digest"],
                    },
                }
            )
            before = tree_snapshot(root)
            process = run_closeout(root, request)
            self.assertNotEqual(process.returncode, 0)
            payload = json.loads(process.stdout)
            self.assertEqual(payload["classification"], "orphan-document-operation")
            assert_failure_envelope(self, payload)
            self.assertEqual(tree_snapshot(root), before)

    def test_same_porcelain_status_source_drift_invalidates_noop_fixed_roles(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            build_repository(root)
            agents = root / "AGENTS.md"
            agents.write_text(
                "# Agent instructions\n\n"
                + lifecycle_io.INIT_ORIENTATION_TEXT
                + "\n\nMarker: alpha\n",
                encoding="utf-8",
            )
            (root / ".gitignore").write_text(
                ".local/\n.diataxis/local-map.json\n", encoding="utf-8"
            )
            status_before = subprocess.run(
                ["git", "-C", str(root), "status", "--porcelain=v1", "-z"],
                capture_output=True,
                check=True,
            ).stdout
            preview = json.loads(run_closeout(root, build_request(root)).stdout)
            agents.write_text(agents.read_text("utf-8").replace("alpha", "bravo"), encoding="utf-8")
            status_after = subprocess.run(
                ["git", "-C", str(root), "status", "--porcelain=v1", "-z"],
                capture_output=True,
                check=True,
            ).stdout
            self.assertEqual(status_after, status_before)
            before = tree_snapshot(root)
            process = run_closeout(root, build_request(root, "apply", preview["approval"]))
            self.assertNotEqual(process.returncode, 0)
            payload = json.loads(process.stdout)
            self.assertEqual(payload["status"], "stale-preview")
            assert_failure_envelope(self, payload)
            self.assertEqual(tree_snapshot(root), before)

    def test_local_map_identity_mismatch_fails_and_absence_is_not_applicable(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            build_repository(root)
            mismatch = build_request(root)
            mismatch["evidence"]["local_map"]["repository_identity"] = "0" * 64
            before = tree_snapshot(root)
            process = run_closeout(root, mismatch)
            self.assertNotEqual(process.returncode, 0)
            payload = json.loads(process.stdout)
            self.assertEqual(payload["classification"], "local-map-identity-mismatch")
            assert_failure_envelope(self, payload)
            self.assertEqual(tree_snapshot(root), before)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            build_repository(root)
            preview_request = build_request(root)
            preview_request["evidence"]["local_map"] = None
            preview_request["evidence"]["source_changes"]["local_map_ignore"] = False
            preview = json.loads(run_closeout(root, preview_request).stdout)
            apply_request = copy.deepcopy(preview_request)
            apply_request.update(operation="apply", approval=preview["approval"])
            applied_process = run_closeout(root, apply_request)
            self.assertEqual(applied_process.returncode, 0, applied_process.stdout)
            applied = json.loads(applied_process.stdout)
            self.assertEqual(
                applied["verification"]["local_map_ignored"], "not-applicable"
            )

    def test_recovery_staging_keeps_private_local_map_bytes_git_ignored_before_apply(self):
        from _docs_checker import init_closeout as closeout

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            build_repository(root)
            request = build_request(root)
            private_route = ".local/private-campaign/PLAN.md"
            request["evidence"]["local_map"]["routes"] = [
                {
                    "route": private_route,
                    "visibility": "local-only",
                    "kind": "campaign-plan",
                    "topics": ["private-campaign"],
                    "aliases": ["private-plan"],
                    "authority": "authoritative",
                    "status": "current",
                    "preservation": "preserve-local-only",
                    "last_verified_system": "0.3.0",
                    "last_verified_rubric": "3",
                    "content_digest": "sha256-text:" + "a" * 64,
                }
            ]
            prepared = closeout.prepare_initialization_closeout(root, request)
            recovery = lifecycle_io._prepare_recovery_area_v3(root, prepared["plan"])[
                "path"
            ]

            private_bytes = private_route.encode("utf-8")
            containing = [
                path
                for path in recovery.rglob("*")
                if path.is_file() and private_bytes in path.read_bytes()
            ]
            self.assertTrue(containing)
            for path in containing:
                ignored = subprocess.run(
                    ["git", "-C", str(root), "check-ignore", "-q", "--", str(path)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(ignored.returncode, 0, path.relative_to(root).as_posix())
            status = subprocess.run(
                ["git", "-C", str(root), "status", "--short", "--untracked-files=all"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(status.returncode, 0, status.stderr)
            self.assertNotIn(".diataxis/recovery/", status.stdout.replace("\\", "/"))
            add_preview = subprocess.run(
                ["git", "-C", str(root), "add", "-n", "-A"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(add_preview.returncode, 0, add_preview.stderr)
            self.assertNotIn(
                ".diataxis/recovery/",
                add_preview.stdout.replace("\\", "/"),
            )

    def test_post_prepare_ignore_guard_tamper_stops_before_any_target_install(self):
        from _docs_checker import init_closeout as closeout

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            build_repository(root)
            request = build_request(root)
            request["evidence"]["local_map"]["routes"] = [
                {
                    "route": ".local/private-campaign/PLAN.md",
                    "visibility": "local-only",
                    "kind": "campaign-plan",
                    "topics": ["private-campaign"],
                    "aliases": ["private-plan"],
                    "authority": "authoritative",
                    "status": "current",
                    "preservation": "preserve-local-only",
                    "last_verified_system": "0.3.0",
                    "last_verified_rubric": "3",
                    "content_digest": "sha256-text:" + "a" * 64,
                }
            ]
            prepared = closeout.prepare_initialization_closeout(root, request)
            plan = prepared["plan"]

            def authorized_targets():
                return {
                    relative: (
                        (root / relative).read_bytes()
                        if (root / relative).is_file()
                        else None
                    )
                    for relative in plan["replacement_order"]
                }

            before = authorized_targets()
            event_path = next(
                root / relative
                for relative, role in plan["target_roles"].items()
                if role == "event"
            )
            real_prepare = lifecycle_io._prepare_recovery_area_v3
            real_install = lifecycle_io._install_entry_v3
            tampered = {}

            def prepare_then_remove_guard(*args, **kwargs):
                recovery = real_prepare(*args, **kwargs)
                guard = recovery["path"] / ".gitignore"
                guard.unlink()
                tampered["recovery"] = recovery["path"]
                return recovery

            with mock.patch.object(
                lifecycle_io,
                "_prepare_recovery_area_v3",
                side_effect=prepare_then_remove_guard,
            ), mock.patch.object(
                lifecycle_io,
                "_install_entry_v3",
                wraps=real_install,
            ) as install_entry:
                response = closeout.apply_response(
                    root,
                    prepared,
                    prepared["approval"],
                )

            self.assertIn("recovery", tampered)
            self.assertEqual(install_entry.call_count, 0)
            self.assertFalse(response["successful_event_recorded"])
            self.assertNotEqual(response["status"], "applied")
            self.assertEqual(authorized_targets(), before)
            self.assertFalse(event_path.exists())

    def test_ignore_guard_tamper_stops_before_a_later_target_install(self):
        from _docs_checker import init_closeout as closeout

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            build_repository(root)
            prepared = closeout.prepare_initialization_closeout(
                root,
                build_request(root),
            )
            plan = prepared["plan"]
            non_event = [
                entry
                for entry in plan["journal_models"]["prepared"]["entries"]
                if entry["role"] != "event"
            ]
            self.assertGreaterEqual(len(non_event), 2)
            later_target = root / non_event[1]["path"]
            later_start = (
                later_target.read_bytes() if later_target.is_file() else None
            )
            event_path = root / next(
                relative
                for relative, role in plan["target_roles"].items()
                if role == "event"
            )
            event_start = event_path.read_bytes() if event_path.is_file() else None
            real_write_journal = lifecycle_io._write_active_journal_v3
            real_install_once = lifecycle_io._install_entry_once_v3
            tampered = {}

            def write_journal_then_tamper(recovery_root, journal):
                result = real_write_journal(recovery_root, journal)
                installed = sum(
                    entry["status"] == "installed" for entry in journal["entries"]
                )
                if (
                    journal["phase"] == "installing"
                    and installed == 1
                    and not tampered
                ):
                    guard = Path(recovery_root) / ".gitignore"
                    guard.write_bytes(b"!\n")
                    tampered["guard"] = guard
                return result

            with mock.patch.object(
                lifecycle_io,
                "_write_active_journal_v3",
                side_effect=write_journal_then_tamper,
            ), mock.patch.object(
                lifecycle_io,
                "_install_entry_once_v3",
                wraps=real_install_once,
            ) as install_once:
                response = closeout.apply_response(
                    root,
                    prepared,
                    prepared["approval"],
                )

            self.assertIn("guard", tampered)
            self.assertEqual(install_once.call_count, 1)
            self.assertFalse(response["successful_event_recorded"])
            self.assertNotEqual(response["status"], "applied")
            self.assertEqual(
                later_target.read_bytes() if later_target.is_file() else None,
                later_start,
            )
            self.assertEqual(
                event_path.read_bytes() if event_path.is_file() else None,
                event_start,
            )

    def test_post_rename_ignore_guard_tamper_stops_before_recovery_unlink(self):
        from _docs_checker import init_closeout as closeout

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            build_repository(root)
            request = build_request(root)
            private_route = ".local/private-campaign/PLAN.md"
            request["evidence"]["local_map"]["routes"] = [
                {
                    "route": private_route,
                    "visibility": "local-only",
                    "kind": "campaign-plan",
                    "topics": ["private-campaign"],
                    "aliases": ["private-plan"],
                    "authority": "authoritative",
                    "status": "current",
                    "preservation": "preserve-local-only",
                    "last_verified_system": "0.3.0",
                    "last_verified_rubric": "3",
                    "content_digest": "sha256-text:" + "a" * 64,
                }
            ]
            prepared = closeout.prepare_initialization_closeout(root, request)
            plan = prepared["plan"]
            before_targets = {
                relative: (
                    (root / relative).read_bytes()
                    if (root / relative).is_file()
                    else None
                )
                for relative in plan["replacement_order"]
            }
            recovery = lifecycle_io._prepare_recovery_area_v3(root, plan)["path"]
            private_bytes = private_route.encode("utf-8")
            self.assertTrue(
                any(
                    path.is_file() and private_bytes in path.read_bytes()
                    for path in recovery.rglob("*")
                )
            )
            real_open_tree = lifecycle_io._open_cleanup_tree_v3
            real_unlink = lifecycle_io._unlink_cleanup_child_v3
            tampered = {}

            def open_tree_then_remove_guard(*args, **kwargs):
                tree = real_open_tree(*args, **kwargs)
                guard = tree["path"] / ".gitignore"
                guard.unlink()
                tampered["tombstone"] = tree["path"]
                return tree

            with mock.patch.object(
                lifecycle_io,
                "_open_cleanup_tree_v3",
                side_effect=open_tree_then_remove_guard,
            ), mock.patch.object(
                lifecycle_io,
                "_unlink_cleanup_child_v3",
                wraps=real_unlink,
            ) as unlink:
                with self.assertRaises(lifecycle_io._V3CleanupFailure):
                    lifecycle_io._cleanup_recovery_area_v3(
                        root,
                        recovery,
                        action="cleanup",
                    )

            self.assertIn("tombstone", tampered)
            self.assertEqual(unlink.call_count, 0)
            tombstone = tampered["tombstone"]
            self.assertTrue(
                any(
                    path.is_file() and private_bytes in path.read_bytes()
                    for path in tombstone.rglob("*")
                )
            )
            self.assertEqual(
                {
                    relative: (
                        (root / relative).read_bytes()
                        if (root / relative).is_file()
                        else None
                    )
                    for relative in plan["replacement_order"]
                },
                before_targets,
            )

    @unittest.skipIf(os.name == "nt", "POSIX cleanup identity-swap coverage")
    def test_posix_cleanup_guard_check_is_bound_to_pinned_tombstone(self):
        from _docs_checker import init_closeout as closeout

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            build_repository(root)
            request = build_request(root)
            private_route = ".local/private-campaign/PLAN.md"
            request["evidence"]["local_map"]["routes"] = [
                {
                    "route": private_route,
                    "visibility": "local-only",
                    "kind": "campaign-plan",
                    "topics": ["private-campaign"],
                    "aliases": ["private-plan"],
                    "authority": "authoritative",
                    "status": "current",
                    "preservation": "preserve-local-only",
                    "last_verified_system": "0.3.0",
                    "last_verified_rubric": "3",
                    "content_digest": "sha256-text:" + "a" * 64,
                }
            ]
            prepared = closeout.prepare_initialization_closeout(root, request)
            recovery = lifecycle_io._prepare_recovery_area_v3(
                root,
                prepared["plan"],
            )["path"]
            private_bytes = private_route.encode("utf-8")
            self.assertTrue(
                any(
                    path.is_file() and private_bytes in path.read_bytes()
                    for path in recovery.rglob("*")
                )
            )
            real_open_tree = lifecycle_io._open_cleanup_tree_v3
            real_unlink = lifecycle_io._unlink_cleanup_child_v3
            swapped = {}
            pinned_body_unlinks = []

            def open_tree_then_swap_path(*args, **kwargs):
                tree = real_open_tree(*args, **kwargs)
                tombstone = tree["path"]
                displaced = tombstone.with_name(tombstone.name + ".displaced")
                os.replace(tombstone, displaced)
                tombstone.mkdir()
                (tombstone / ".gitignore").write_bytes(b"*\n")
                os.unlink(".gitignore", dir_fd=tree["root"]["fd"])
                swapped.update(
                    tombstone=tombstone,
                    displaced=displaced,
                    child_pins=tuple(tree["children"].values()),
                )
                return tree

            def record_pinned_body_unlink(pin, name):
                if any(pin is child for child in swapped.get("child_pins", ())):
                    pinned_body_unlinks.append(name)
                return real_unlink(pin, name)

            with mock.patch.object(
                lifecycle_io,
                "_open_cleanup_tree_v3",
                side_effect=open_tree_then_swap_path,
            ), mock.patch.object(
                lifecycle_io,
                "_unlink_cleanup_child_v3",
                side_effect=record_pinned_body_unlink,
            ):
                with self.assertRaises(lifecycle_io._V3CleanupFailure):
                    lifecycle_io._cleanup_recovery_area_v3(
                        root,
                        recovery,
                        action="cleanup",
                    )

            self.assertIn("displaced", swapped)
            self.assertEqual(pinned_body_unlinks, [])
            self.assertEqual((swapped["tombstone"] / ".gitignore").read_bytes(), b"*\n")
            self.assertTrue(
                any(
                    path.is_file() and private_bytes in path.read_bytes()
                    for path in swapped["displaced"].rglob("*")
                )
            )

    def test_real_preapply_verifier_rejects_drift_and_failures_are_normalized(self):
        from _docs_checker import init_closeout as closeout

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            build_repository(root)
            request = build_request(root)
            prepared = closeout.prepare_initialization_closeout(root, request)
            (root / "docs" / "page-001.md").write_text("# drift\n", encoding="utf-8")
            before = tree_snapshot(root)
            response = closeout.apply_response(root, prepared, prepared["approval"])
            self.assertEqual(response["status"], "verification-failed")
            assert_failure_envelope(self, response)
            self.assertEqual(tree_snapshot(root), before)

        for lifecycle_result in (
            {
                "status": "stale-target",
                "path": ".diataxis/state.json",
                "successful_event_recorded": False,
            },
            {
                "status": "closeout-failed",
                "classification": "transaction-io-failure",
                "boundary": "replace:AGENTS.md",
                "control_plane_rolled_back": True,
                "successful_event_recorded": False,
            },
        ):
            with self.subTest(status=lifecycle_result["status"]), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                build_repository(root)
                prepared = closeout.prepare_initialization_closeout(
                    root, build_request(root)
                )
                with mock.patch.object(
                    closeout, "apply_verified_closeout", return_value=lifecycle_result
                ):
                    response = closeout.apply_response(
                        root, prepared, prepared["approval"]
                    )
                assert_failure_envelope(self, response)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            build_repository(root)
            prepared = closeout.prepare_initialization_closeout(
                root, build_request(root)
            )
            incomplete = {
                "status": "closeout-failed",
                "classification": "transaction-io-failure",
                "boundary": "restore:AGENTS.md",
                "control_plane_rolled_back": False,
                "rollback": {
                    "complete": False,
                    "writes": 2,
                    "outcomes": {
                        "documents": "complete",
                        "controls": "not-run",
                        "cleanup": "incomplete",
                    },
                },
                "successful_event_recorded": False,
            }
            with mock.patch.object(
                closeout, "apply_verified_closeout", return_value=incomplete
            ):
                response = closeout.apply_response(
                    root, prepared, prepared["approval"]
                )
            assert_failure_envelope(self, response)
            self.assertEqual(response["writes"], "unknown")
            self.assertEqual(response["partial_state"], "possible")
            self.assertEqual(
                response["rollback"],
                {
                    "required": True,
                    "complete": False,
                    "documents": "complete",
                    "controls": "not-run",
                    "cleanup": "incomplete",
                },
            )


class InitCloseoutRollbackTests(unittest.TestCase):
    def _prepared_plan(self, root):
        from _docs_checker.init_closeout import prepare_initialization_closeout

        return prepare_initialization_closeout(root, build_request(root))["plan"]

    def test_torn_recovery_ignore_guard_is_aborted_before_any_payload_write(self):
        from _docs_checker import init_closeout as closeout

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            build_repository(root)
            prepared = closeout.prepare_initialization_closeout(
                root, build_request(root)
            )
            before = tree_snapshot(root)
            real_write = lifecycle_io.os.write
            injected = False

            def partial_guard_then_fail(descriptor, data):
                nonlocal injected
                if not injected and bytes(data) == b"*\n":
                    injected = True
                    real_write(descriptor, data[:1])
                    raise OSError("forced partial recovery ignore guard")
                return real_write(descriptor, data)

            with mock.patch.object(
                lifecycle_io.os,
                "write",
                side_effect=partial_guard_then_fail,
            ):
                response = closeout.apply_response(
                    root,
                    prepared,
                    prepared["approval"],
                )

            self.assertTrue(injected)
            self.assertEqual(response["status"], "closeout-failed")
            self.assertEqual(response["writes"], 0)
            self.assertEqual(response["partial_state"], "none")
            self.assertTrue(response["rollback"]["complete"])
            self.assertEqual(response["rollback"]["cleanup"], "complete")
            self.assertEqual(tree_snapshot(root), before)
            self.assertFalse((root / ".diataxis").exists())

    def test_partial_preparation_journal_is_aborted_without_a_false_clean_envelope(self):
        from _docs_checker import init_closeout as closeout

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            build_repository(root)
            prepared = closeout.prepare_initialization_closeout(
                root, build_request(root)
            )
            before = tree_snapshot(root)
            real_write = lifecycle_io.os.write
            injected = False

            def partial_then_fail(descriptor, data):
                nonlocal injected
                if not injected and b'"journal_version":"init-recovery-v1"' in bytes(data):
                    injected = True
                    real_write(descriptor, data[:17])
                    raise OSError("forced partial preparation journal")
                return real_write(descriptor, data)

            with mock.patch.object(
                lifecycle_io.os,
                "write",
                side_effect=partial_then_fail,
            ):
                response = closeout.apply_response(
                    root,
                    prepared,
                    prepared["approval"],
                )

            self.assertTrue(injected)
            self.assertEqual(response["status"], "closeout-failed")
            self.assertEqual(response["writes"], 0)
            self.assertEqual(response["partial_state"], "none")
            self.assertTrue(response["rollback"]["complete"])
            self.assertEqual(response["rollback"]["cleanup"], "complete")
            self.assertEqual(tree_snapshot(root), before)
            self.assertFalse((root / ".diataxis").exists())

    def test_partial_preparation_preserves_a_preexisting_empty_recovery_container(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            build_repository(root, preexisting_control=True)
            (root / ".diataxis" / "recovery").mkdir()
            plan = self._prepared_plan(root)
            before = tree_snapshot(root)
            real_write = lifecycle_io.os.write
            injected = False

            def partial_then_fail(descriptor, data):
                nonlocal injected
                if not injected and b'"journal_version":"init-recovery-v1"' in bytes(data):
                    injected = True
                    real_write(descriptor, data[:17])
                    raise OSError("forced partial preparation journal")
                return real_write(descriptor, data)

            with mock.patch.object(
                lifecycle_io.os,
                "write",
                side_effect=partial_then_fail,
            ):
                result = docs_checker.apply_verified_closeout(
                    root,
                    plan,
                    approved_transaction=plan["transaction_id"],
                    verification=lambda: True,
                )

            self.assertTrue(injected)
            self.assertEqual(result["status"], "closeout-failed")
            self.assertTrue(result["control_plane_rolled_back"])
            self.assertEqual(tree_snapshot(root), before)
            self.assertEqual(list((root / ".diataxis" / "recovery").iterdir()), [])

    def test_interrupted_preparation_aborts_partial_journal_before_reraising(self):
        from _docs_checker import init_closeout as closeout

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            build_repository(root)
            prepared = closeout.prepare_initialization_closeout(
                root, build_request(root)
            )
            before = tree_snapshot(root)
            real_write = lifecycle_io.os.write
            injected = False

            def partial_then_interrupt(descriptor, data):
                nonlocal injected
                if not injected and b'"journal_version":"init-recovery-v1"' in bytes(data):
                    injected = True
                    real_write(descriptor, data[:17])
                    raise KeyboardInterrupt
                return real_write(descriptor, data)

            with mock.patch.object(
                lifecycle_io.os,
                "write",
                side_effect=partial_then_interrupt,
            ):
                with self.assertRaises(KeyboardInterrupt):
                    closeout.apply_response(
                        root,
                        prepared,
                        prepared["approval"],
                    )

            self.assertTrue(injected)
            self.assertEqual(tree_snapshot(root), before)
            self.assertFalse((root / ".diataxis").exists())

    def test_failed_recursive_parent_creation_does_not_leave_false_clean_directories(self):
        from _docs_checker import init_closeout as closeout

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            build_repository(root)
            prepared = closeout.prepare_initialization_closeout(
                root, build_request(root)
            )
            before = tree_snapshot(root)
            backups = (
                root
                / ".diataxis"
                / "recovery"
                / prepared["plan"]["transaction_id"]
                / "backups"
            )
            real_mkdir = Path.mkdir
            injected = False

            def fail_after_parent_creation(path, *args, **kwargs):
                nonlocal injected
                if Path(path) == backups and not injected:
                    injected = True
                    real_mkdir(
                        backups.parent.parent,
                        parents=True,
                        exist_ok=True,
                    )
                    raise OSError("forced preparation directory failure")
                return real_mkdir(path, *args, **kwargs)

            with mock.patch.object(
                Path,
                "mkdir",
                fail_after_parent_creation,
            ):
                response = closeout.apply_response(
                    root,
                    prepared,
                    prepared["approval"],
                )

            self.assertTrue(injected)
            self.assertEqual(response["status"], "closeout-failed")
            self.assertEqual(response["writes"], 0)
            self.assertEqual(response["partial_state"], "none")
            self.assertTrue(response["rollback"]["complete"])
            self.assertEqual(response["rollback"]["cleanup"], "complete")
            self.assertEqual(tree_snapshot(root), before)
            self.assertFalse((root / ".diataxis").exists())

    def test_parent_create_then_raise_is_reconciled_before_claiming_clean(self):
        from _docs_checker import init_closeout as closeout

        for relative in (".diataxis", ".diataxis/recovery"):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                build_repository(root)
                prepared = closeout.prepare_initialization_closeout(
                    root, build_request(root)
                )
                before = tree_snapshot(root)
                target = root / relative
                real_mkdir = Path.mkdir
                injected = False

                def create_then_raise(path, *args, **kwargs):
                    nonlocal injected
                    if Path(path) == target and not injected:
                        injected = True
                        real_mkdir(path, *args, **kwargs)
                        raise OSError("forced parent create-then-raise")
                    return real_mkdir(path, *args, **kwargs)

                with mock.patch.object(Path, "mkdir", create_then_raise):
                    response = closeout.apply_response(
                        root,
                        prepared,
                        prepared["approval"],
                    )

                self.assertTrue(injected)
                self.assertEqual(response["status"], "closeout-failed")
                self.assertEqual(response["writes"], 0)
                self.assertEqual(response["partial_state"], "none")
                self.assertTrue(response["rollback"]["complete"])
                self.assertEqual(response["rollback"]["cleanup"], "complete")
                self.assertEqual(tree_snapshot(root), before)
                self.assertFalse((root / ".diataxis").exists())

    def test_partial_writes_and_late_preparation_failure_restore_absent_control_and_retry_once(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            build_repository(root)
            plan = self._prepared_plan(root)
            before = tree_snapshot(root)
            real_write = lifecycle_io.os.write
            real_write_journal = lifecycle_io._write_active_journal_v3
            calls = {"writes": 0}
            journal_phases = []

            def partial_write(descriptor, data):
                calls["writes"] += 1
                return real_write(descriptor, data[: max(1, min(len(data), 97))])

            def fail_late(recovery_root, journal):
                journal_phases.append(journal["phase"])
                result = real_write_journal(recovery_root, journal)
                if journal["phase"] == "installing":
                    raise ValueError("forced late preparation failure")
                return result

            with mock.patch.object(lifecycle_io.os, "write", partial_write), mock.patch.object(
                lifecycle_io, "_write_active_journal_v3", fail_late
            ):
                result = docs_checker.apply_verified_closeout(
                    root,
                    plan,
                    approved_transaction=plan["transaction_id"],
                    verification=lambda: True,
                )
            self.assertGreater(calls["writes"], len(plan["targets"]))
            self.assertEqual(
                journal_phases,
                ["preparing", "prepared", "installing"],
            )
            self.assertEqual(result["status"], "closeout-failed")
            self.assertEqual(result["classification"], "transaction-semantic-verification-failure")
            self.assertEqual(result["boundary"], "transaction-preparation")
            self.assertTrue(result["control_plane_rolled_back"])
            self.assertFalse(result["successful_event_recorded"])
            self.assertEqual(tree_snapshot(root), before)
            self.assertFalse((root / ".diataxis").exists())
            self.assertFalse((root / ".diataxis" / "recovery").exists())
            self.assertFalse(any(path.name.startswith(".docs-txn-") for path in root.rglob("*")))

            retry = docs_checker.apply_verified_closeout(
                root,
                plan,
                approved_transaction=plan["transaction_id"],
                verification=lambda: True,
            )
            self.assertEqual(retry["status"], "applied")
            events = docs_checker.load_operational_events(root)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["event_id"], retry["event_id"])
            self.assertEqual(len(list((root / ".diataxis" / "manifests").glob("*.json"))), 1)
            self.assertFalse((root / ".diataxis" / "recovery").exists())
            self.assertFalse(any(path.name.startswith(".docs-txn-") for path in root.rglob("*")))

    def test_late_preparation_failure_preserves_preexisting_empty_control_directory(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            build_repository(root, preexisting_control=True)
            plan = self._prepared_plan(root)
            before = tree_snapshot(root)
            real_write_journal = lifecycle_io._write_active_journal_v3
            journal_phases = []

            def fail_late(recovery_root, journal):
                journal_phases.append(journal["phase"])
                result = real_write_journal(recovery_root, journal)
                if journal["phase"] == "installing":
                    raise ValueError("forced late preparation failure")
                return result

            with mock.patch.object(
                lifecycle_io, "_write_active_journal_v3", fail_late
            ):
                result = docs_checker.apply_verified_closeout(
                    root,
                    plan,
                    approved_transaction=plan["transaction_id"],
                    verification=lambda: True,
                )
            self.assertEqual(
                journal_phases,
                ["preparing", "prepared", "installing"],
            )
            self.assertEqual(result["status"], "closeout-failed")
            self.assertEqual(result["classification"], "transaction-semantic-verification-failure")
            self.assertEqual(result["boundary"], "transaction-preparation")
            self.assertTrue(result["control_plane_rolled_back"])
            self.assertFalse(result["successful_event_recorded"])
            self.assertEqual(tree_snapshot(root), before)
            self.assertTrue((root / ".diataxis").is_dir())
            self.assertEqual(list((root / ".diataxis").iterdir()), [])
            self.assertFalse((root / ".diataxis" / "recovery").exists())

    def test_post_replacement_verification_failure_restores_every_target_and_directory_history(self):
        for preexisting in (False, True):
            with self.subTest(preexisting=preexisting), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                build_repository(root, preexisting_control=preexisting)
                plan = self._prepared_plan(root)
                before = tree_snapshot(root)
                real_verify = lifecycle_io._verify_pre_event_v3
                verification_calls = []

                def fail_after_verification(repo, prepared_plan, journal):
                    real_verify(repo, prepared_plan, journal)
                    verification_calls.append(journal["phase"])
                    raise ValueError("forced post-install verification failure")

                with mock.patch.object(
                    lifecycle_io,
                    "_verify_pre_event_v3",
                    side_effect=fail_after_verification,
                ):
                    result = docs_checker.apply_verified_closeout(
                        root,
                        plan,
                        approved_transaction=plan["transaction_id"],
                        verification=lambda: True,
                    )
                self.assertEqual(verification_calls, ["installing"])
                self.assertEqual(result["status"], "closeout-failed")
                self.assertEqual(result["classification"], "transaction-semantic-verification-failure")
                self.assertEqual(result["boundary"], "transaction-preparation")
                self.assertTrue(result["control_plane_rolled_back"])
                self.assertFalse(result["successful_event_recorded"])
                self.assertEqual(tree_snapshot(root), before)
                self.assertEqual((root / ".diataxis").exists(), preexisting)
                if preexisting:
                    self.assertEqual(list((root / ".diataxis").iterdir()), [])
                self.assertFalse((root / ".diataxis" / "recovery").exists())
                self.assertFalse(
                    any(path.name.startswith(".docs-txn-") for path in root.rglob("*"))
                )


if __name__ == "__main__":
    unittest.main()
