import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / "skills" / "docs" / "scripts"
CLOSEOUT = SCRIPTS / "doctor_closeout.py"
sys.path.insert(0, str(SCRIPTS))

import check as docs_checker
from _docs_checker import doctor_closeout
from _docs_checker import lifecycle_io
from _docs_checker.doctor_closeout import apply_treatment_receipt
from _docs_checker.init_adoption import adoption_apply, adoption_preview
from _docs_checker.lifecycle_io import apply_verified_closeout, prepare_verified_closeout


def git(root, *arguments):
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise AssertionError(result.stdout + result.stderr)
    return result.stdout


def control_snapshot(root):
    control = root / ".diataxis"
    return {
        path.relative_to(control).as_posix(): path.read_bytes()
        for path in control.rglob("*")
        if path.is_file()
    }


class DoctorCloseoutTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name) / "repository"
        self.root.mkdir()
        docs = self.root / "docs"
        docs.mkdir()
        (docs / "README.md").write_text(
            "# Documentation\n\n[Guide](guide.md)\n[New guide](new.md)\n",
            encoding="utf-8",
            newline="\n",
        )
        (docs / "guide.md").write_text(
            "# Guide\n\nShared guidance.\n",
            encoding="utf-8",
            newline="\n",
        )
        (self.root / ".gitignore").write_text(
            "docs/local/\n",
            encoding="utf-8",
            newline="\n",
        )
        private = docs / "local"
        private.mkdir()
        (private / "private.md").write_text(
            "# PRIVATE_SENTINEL_DO_NOT_DISCLOSE\n",
            encoding="utf-8",
            newline="\n",
        )
        git(self.root, "init", "--quiet")
        git(self.root, "config", "user.email", "fixture@example.invalid")
        git(self.root, "config", "user.name", "Fixture")
        git(self.root, "add", ".")
        git(self.root, "commit", "--quiet", "-m", "fixture")

        request, preview = adoption_preview(
            self.root,
            completed_at="2026-07-16T12:00:00Z",
        )
        result = adoption_apply(self.root, request, preview["approval"])
        self.assertEqual(result["status"], "applied")

    def tearDown(self):
        self.tempdir.cleanup()

    def _missing_link_treatment_request(self):
        findings, _hot_path, _measurements = docs_checker.check(
            self.root,
            map_path="docs/README.md",
            scope="docs",
            _measurements=True,
        )
        missing = next(item for item in findings if item["kind"] == "missing-link")
        return {
            "schema_version": 1,
            "scope": "docs",
            "map": "docs/README.md",
            "hot_paths": [],
            "treatments": [
                {
                    "findings": [missing],
                    "files": ["docs/new.md"],
                }
            ],
        }

    def _index_digest(self):
        index = Path(git(self.root, "rev-parse", "--git-path", "index").strip())
        if not index.is_absolute():
            index = self.root / index
        return hashlib.sha256(index.read_bytes()).hexdigest()

    def _persist_verified_readme(self):
        state = docs_checker.load_operational_state(self.root)
        state["verified_documents"] = [
            {
                "document": "docs/README.md",
                "digest": docs_checker.normalized_content_digest(
                    self.root / "docs" / "README.md"
                ),
                "sources": [],
                "verified_event": "EVT-00000000",
            }
        ]
        state["trust_coverage"] = {
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
        event = {
            "kind": "fix",
            "completed_at": "2026-07-16T12:01:00Z",
            "skill_version": state["initialized"]["skill_version"],
            "approved_ids": [],
            "score_before": state["rubric"]["last_verified_score"],
            "score_after": state["rubric"]["last_verified_score"],
            "changed_paths": [],
            "reason": "Record verified documentation evidence.",
            "summary": "Persist the fixture's current documentation evidence.",
        }
        plan = prepare_verified_closeout(
            self.root,
            command="fix",
            state=state,
            findings=docs_checker.load_operational_findings(self.root),
            event=event,
            approvals=[],
            selected_boundary="docs",
        )
        self.assertEqual(plan["status"], "approval-required")
        result = apply_verified_closeout(
            self.root,
            plan,
            approved_transaction=plan["transaction_id"],
            verification=lambda: True,
        )
        self.assertEqual(result["status"], "applied")

    def _run(self, operation, receipt, *extra):
        return subprocess.run(
            [
                sys.executable,
                str(CLOSEOUT),
                str(self.root),
                operation,
                "--receipt-file",
                str(receipt),
                *extra,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def _prepare(self, request_payload=None):
        receipt = Path(self.tempdir.name) / "doctor-treatment-receipt.json"
        request = Path(self.tempdir.name) / "doctor-treatment-request.json"
        request.write_text(
            json.dumps(
                request_payload or self._missing_link_treatment_request(), sort_keys=True
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        completed = self._run("prepare", receipt, "--request-file", str(request))
        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        preview = json.loads(completed.stdout)
        self.assertEqual(preview["status"], "approval-required")
        self.assertTrue(receipt.is_file())
        return receipt, preview

    def test_exact_treatment_closeout_uses_a_temporary_index_and_records_event_last(self):
        index_digest = self._index_digest()
        controls_before_prepare = control_snapshot(self.root)
        receipt, preview = self._prepare()
        self.assertEqual(self._index_digest(), index_digest)
        self.assertEqual(control_snapshot(self.root), controls_before_prepare)

        (self.root / "docs" / "new.md").write_text(
            "# New guide\n\nVerified shared guidance.\n",
            encoding="utf-8",
            newline="\n",
        )
        applied = self._run("apply", receipt, "--approval", preview["approval"])

        self.assertEqual(applied.returncode, 0, applied.stderr + applied.stdout)
        result = json.loads(applied.stdout)
        self.assertEqual(result["status"], "applied")
        self.assertTrue(result["successful_event_recorded"])
        self.assertEqual(result["affected_file_count"], 1)
        self.assertEqual(result["next_action"], "none")
        self.assertEqual(result["verification"]["candidate"]["status"], "clean")
        self.assertEqual(result["verification"]["installed"]["status"], "clean")
        self.assertTrue(result["verification"]["installed"]["event_last"])
        self.assertEqual(self._index_digest(), index_digest)

        state = docs_checker.load_operational_state(self.root)
        events = docs_checker.load_operational_events(self.root)
        self.assertEqual(state["rubric"]["last_verified_score"], 100)
        self.assertEqual(state["last_completed_event"], events[-1]["event_id"])
        self.assertEqual(events[-1]["kind"], "fix")
        self.assertEqual(events[-1]["approved_ids"], result["approved_ids"])
        self.assertIn("docs/new.md", events[-1]["changed_paths"])
        self.assertFalse((self.root / ".diataxis" / "recovery").exists())
        compact = json.dumps(result, sort_keys=True)
        self.assertNotIn("findings", compact)
        self.assertNotIn("coverage", compact)
        self.assertNotIn("PRIVATE_SENTINEL_DO_NOT_DISCLOSE", compact)
        self.assertNotIn("docs/local/private.md", compact)

    def test_post_closeout_verification_conflict_reports_committed_event_truthfully(self):
        receipt_path, preview = self._prepare()
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        (self.root / "docs" / "new.md").write_text(
            "# New guide\n\nVerified shared guidance.\n",
            encoding="utf-8",
            newline="\n",
        )
        real_candidate = doctor_closeout._candidate
        calls = []

        def fail_only_the_post_closeout_check(*args, **kwargs):
            calls.append(None)
            if len(calls) == 3:
                raise doctor_closeout.DoctorCloseoutError(
                    "stale-preview",
                    "forced-installed-conflict",
                    "candidate-verification",
                )
            return real_candidate(*args, **kwargs)

        with mock.patch.object(
            doctor_closeout,
            "_candidate",
            side_effect=fail_only_the_post_closeout_check,
        ):
            result = apply_treatment_receipt(self.root, receipt, preview["approval"])

        self.assertEqual(result["status"], "post-closeout-conflict")
        self.assertEqual(result["classification"], "forced-installed-conflict")
        self.assertEqual(result["affected_file_count"], 1)
        self.assertEqual(result["writes"], "committed")
        self.assertTrue(result["successful_event_recorded"])
        self.assertEqual(
            result["next_action"],
            "rerun Doctor to diagnose the committed post-closeout verification conflict",
        )
        events = docs_checker.load_operational_events(self.root)
        self.assertEqual(events[-1]["event_id"], result["event_id"])
        self.assertEqual(events[-1]["kind"], "fix")

    def test_markerless_doctor_finalize_retry_uses_authenticated_live_fix_event(self):
        receipt_path, preview = self._prepare()
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        (self.root / "docs" / "new.md").write_text(
            "# New guide\n\nVerified shared guidance.\n",
            encoding="utf-8",
            newline="\n",
        )
        real_remove = lifecycle_io._remove_pinned_directory_v3

        def fail_after_terminal_removal(parent, name, pin):
            if name.endswith(".finalize"):
                self.assertFalse(
                    (self.root / ".diataxis" / "recovery" / name / "terminal.json").exists()
                )
                raise OSError("injected markerless cleanup failure")
            return real_remove(parent, name, pin)

        with mock.patch.object(
            lifecycle_io,
            "_remove_pinned_directory_v3",
            side_effect=fail_after_terminal_removal,
        ):
            result = apply_treatment_receipt(self.root, receipt, preview["approval"])

        self.assertTrue(result["successful_event_recorded"])
        tombstone = self.root / ".diataxis" / "recovery" / (
            result["transaction_id"] + ".finalize"
        )
        self.assertTrue(tombstone.is_dir())
        self.assertFalse((tombstone / "terminal.json").exists())
        events_path = self.root / ".diataxis" / "events.jsonl"
        committed_events = events_path.read_bytes()

        events_path.write_bytes(b"corrupt committed event\n")
        blocked = lifecycle_io.preview_state_conflict_recovery(self.root)
        self.assertEqual(blocked["status"], "state-conflict")
        self.assertEqual(blocked["writes"], 0)
        self.assertTrue(tombstone.is_dir())

        events_path.write_bytes(committed_events)
        retry = lifecycle_io.preview_state_conflict_recovery(self.root)
        self.assertEqual(retry["status"], "approval-required")
        self.assertEqual(retry["action"], "finalize")
        self.assertTrue(retry["successful_event_recorded"])
        recovered = lifecycle_io.apply_state_conflict_recovery(
            self.root,
            retry,
            approved_preview=retry["approval"],
            verification=None,
        )
        self.assertEqual(recovered["status"], "recovered")
        self.assertFalse(tombstone.exists())

    def test_approval_binds_every_receipt_precondition_not_only_treatments(self):
        receipt_path, preview = self._prepare()
        controls_before = control_snapshot(self.root)
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["score_before"] += 1
        base = {
            key: value
            for key, value in receipt.items()
            if key not in {"receipt_sha256", "approval"}
        }
        receipt["receipt_sha256"] = "sha256:" + doctor_closeout._sha256(
            doctor_closeout.canonical_bytes(base)
        )
        receipt_path.write_text(
            json.dumps(receipt, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        (self.root / "docs" / "new.md").write_text(
            "# New guide\n\nVerified shared guidance.\n",
            encoding="utf-8",
            newline="\n",
        )

        applied = self._run("apply", receipt_path, "--approval", preview["approval"])

        self.assertEqual(applied.returncode, 2)
        result = json.loads(applied.stdout)
        self.assertEqual(result["status"], "stale-preview")
        self.assertEqual(result["classification"], "receipt-drift")
        self.assertFalse(result["successful_event_recorded"])
        self.assertEqual(control_snapshot(self.root), controls_before)

    def test_prepare_rejects_a_selected_parked_finding(self):
        request = self._missing_link_treatment_request()
        selected, _evidence = doctor_closeout._canonical_finding(
            request["treatments"][0]["findings"][0]
        )
        raw = request["treatments"][0]["findings"][0]
        findings = {
            "schema_version": 1,
            "findings": [
                {
                    "id": doctor_closeout.finding_id(selected, {}),
                    "fingerprint": selected,
                    "priority": "P1",
                    "status": "Parked",
                    "summary": "Parked missing link.",
                    "why": "The finding was deliberately deferred.",
                    "evidence": [{"path": "docs/README.md"}],
                    "recommended_action": "Re-propose it before applying a treatment.",
                }
            ],
        }
        findings_path = self.root / ".diataxis" / "findings.json"
        findings_path.write_bytes(doctor_closeout.canonical_bytes(findings))
        self._persist_verified_readme()
        receipt = Path(self.tempdir.name) / "parked-receipt.json"
        request_file = Path(self.tempdir.name) / "parked-request.json"
        request_file.write_text(
            json.dumps(request, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )

        prepared = self._run("prepare", receipt, "--request-file", str(request_file))

        self.assertEqual(prepared.returncode, 2)
        result = json.loads(prepared.stdout)
        self.assertEqual(result["status"], "stale-preview")
        self.assertEqual(result["classification"], "finding-status-not-actionable")
        self.assertFalse(receipt.exists())

    def test_unapproved_document_change_fails_closed_before_control_closeout(self):
        receipt, preview = self._prepare()
        before = control_snapshot(self.root)
        (self.root / "docs" / "README.md").write_text(
            "# Documentation\n\nUnapproved change.\n",
            encoding="utf-8",
            newline="\n",
        )

        completed = self._run("apply", receipt, "--approval", preview["approval"])

        self.assertEqual(completed.returncode, 2)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "stale-preview")
        self.assertEqual(result["classification"], "unapproved-document-change")
        self.assertFalse(result["successful_event_recorded"])
        self.assertEqual(control_snapshot(self.root), before)

    def test_unapproved_new_shared_markdown_fails_closed_before_control_closeout(self):
        receipt, preview = self._prepare()
        before = control_snapshot(self.root)
        (self.root / "docs" / "new.md").write_text(
            "# New guide\n\nVerified shared guidance.\n",
            encoding="utf-8",
            newline="\n",
        )
        (self.root / "docs" / "unexpected.md").write_text(
            "# Unexpected\n\nUnapproved shared content.\n",
            encoding="utf-8",
            newline="\n",
        )

        completed = self._run("apply", receipt, "--approval", preview["approval"])

        self.assertEqual(completed.returncode, 2)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "stale-preview")
        self.assertEqual(result["classification"], "unapproved-document-change")
        self.assertFalse(result["successful_event_recorded"])
        self.assertEqual(control_snapshot(self.root), before)
        self.assertNotIn("docs/unexpected.md", completed.stdout)

    def test_tampered_receipt_fails_closed_without_control_writes(self):
        receipt, preview = self._prepare()
        before = control_snapshot(self.root)
        payload = json.loads(receipt.read_text(encoding="utf-8"))
        payload["treatments"][0]["fingerprint"] = "sha256:" + "0" * 64
        receipt.write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
            newline="\n",
        )

        completed = self._run("apply", receipt, "--approval", preview["approval"])

        self.assertEqual(completed.returncode, 2)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "stale-preview")
        self.assertFalse(result["successful_event_recorded"])
        self.assertEqual(control_snapshot(self.root), before)

    def test_receipt_cannot_expand_the_approved_treatment_files(self):
        receipt, preview = self._prepare()
        before = control_snapshot(self.root)
        payload = json.loads(receipt.read_text(encoding="utf-8"))
        treatment = payload["treatments"][0]
        treatment["files"] = ["docs/guide.md", "docs/new.md"]
        treatment["affected_count"] = 2
        payload["allowed_paths"] = ["docs/guide.md", "docs/new.md"]
        payload["allowed_starting_digests"]["docs/guide.md"] = (
            "sha256:" + hashlib.sha256((self.root / "docs" / "guide.md").read_bytes()).hexdigest()
        )
        receipt_base = {
            key: value
            for key, value in payload.items()
            if key not in {"receipt_sha256", "approval"}
        }
        payload["receipt_sha256"] = (
            "sha256:" + hashlib.sha256(doctor_closeout.canonical_bytes(receipt_base)).hexdigest()
        )
        receipt.write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        (self.root / "docs" / "new.md").write_text(
            "# New guide\n\nVerified shared guidance.\n",
            encoding="utf-8",
            newline="\n",
        )
        (self.root / "docs" / "guide.md").write_text(
            "# Guide\n\nChanged without treatment approval.\n",
            encoding="utf-8",
            newline="\n",
        )

        completed = self._run("apply", receipt, "--approval", preview["approval"])

        self.assertEqual(completed.returncode, 2)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "stale-preview")
        self.assertEqual(result["classification"], "receipt-drift")
        self.assertFalse(result["successful_event_recorded"])
        self.assertEqual(control_snapshot(self.root), before)
        self.assertNotIn("docs/guide.md", completed.stdout)

    def test_prepare_rejects_ignored_local_markdown_without_disclosing_it(self):
        request = self._missing_link_treatment_request()
        request["treatments"][0]["files"] = ["docs/local/private.md"]
        receipt = Path(self.tempdir.name) / "private-receipt.json"
        request_file = Path(self.tempdir.name) / "private-request.json"
        request_file.write_text(
            json.dumps(request, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        controls_before = control_snapshot(self.root)
        index_before = self._index_digest()

        completed = self._run("prepare", receipt, "--request-file", str(request_file))

        self.assertEqual(completed.returncode, 2)
        result = json.loads(completed.stdout)
        self.assertEqual(result["classification"], "ignored-treatment-path")
        self.assertFalse(receipt.exists())
        self.assertEqual(control_snapshot(self.root), controls_before)
        self.assertEqual(self._index_digest(), index_before)
        self.assertNotIn("PRIVATE_SENTINEL_DO_NOT_DISCLOSE", completed.stdout)
        self.assertNotIn("docs/local/private.md", completed.stdout)

    def test_prepare_rejects_preexisting_untracked_shared_markdown(self):
        (self.root / "docs" / "untracked.md").write_text(
            "# Untracked\n",
            encoding="utf-8",
            newline="\n",
        )
        receipt = Path(self.tempdir.name) / "untracked-receipt.json"
        request_file = Path(self.tempdir.name) / "untracked-request.json"
        request_file.write_text(
            json.dumps(self._missing_link_treatment_request(), sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )

        completed = self._run("prepare", receipt, "--request-file", str(request_file))

        self.assertEqual(completed.returncode, 2)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "stale-preview")
        self.assertEqual(result["classification"], "unapproved-document-change")
        self.assertFalse(receipt.exists())
        self.assertNotIn("docs/untracked.md", completed.stdout)

    def test_pre_event_failure_rolls_back_doctor_controls_and_preserves_approved_document(self):
        receipt_path, preview = self._prepare()
        controls_before = control_snapshot(self.root)
        document = self.root / "docs" / "new.md"
        document.write_text(
            "# New guide\n\nVerified shared guidance.\n",
            encoding="utf-8",
            newline="\n",
        )
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        real_verify = lifecycle_io._verify_pre_event_v3
        phases = []

        def fail_after_verification(root, plan, journal):
            real_verify(root, plan, journal)
            phases.append(journal["phase"])
            raise ValueError("forced Doctor pre-event verification failure")

        with mock.patch.object(
            lifecycle_io,
            "_verify_pre_event_v3",
            side_effect=fail_after_verification,
        ):
            result = apply_treatment_receipt(
                self.root,
                receipt,
                preview["approval"],
            )

        self.assertEqual(phases, ["installing"])
        self.assertEqual(result["status"], "closeout-failed")
        self.assertFalse(result["successful_event_recorded"])
        self.assertEqual(control_snapshot(self.root), controls_before)
        self.assertTrue(document.is_file())
        self.assertEqual(
            document.read_text(encoding="utf-8"),
            "# New guide\n\nVerified shared guidance.\n",
        )
        self.assertFalse((self.root / ".diataxis" / "recovery").exists())

    def test_event_install_failure_rolls_back_and_preserves_the_approved_document(self):
        receipt_path, preview = self._prepare()
        controls_before = control_snapshot(self.root)
        document = self.root / "docs" / "new.md"
        document.write_text(
            "# New guide\n\nVerified shared guidance.\n",
            encoding="utf-8",
            newline="\n",
        )
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        real_replace = lifecycle_io.os.replace
        event_path = self.root / ".diataxis" / "events.jsonl"

        def fail_event_install(source, target):
            target_path = os.path.normcase(os.path.abspath(os.fspath(target)))
            if target_path == os.path.normcase(os.path.abspath(event_path)):
                raise OSError("forced Doctor event installation failure")
            return real_replace(source, target)

        with mock.patch.object(
            lifecycle_io.os,
            "replace",
            side_effect=fail_event_install,
        ):
            result = apply_treatment_receipt(self.root, receipt, preview["approval"])

        self.assertEqual(result["status"], "closeout-failed")
        self.assertFalse(result["successful_event_recorded"])
        self.assertEqual(control_snapshot(self.root), controls_before)
        self.assertEqual(
            document.read_text(encoding="utf-8"),
            "# New guide\n\nVerified shared guidance.\n",
        )
        self.assertFalse((self.root / ".diataxis" / "recovery").exists())

    def test_apply_rejects_a_receipt_stored_inside_the_repository(self):
        receipt, preview = self._prepare()
        in_repository_receipt = self.root / "doctor-treatment-receipt.json"
        in_repository_receipt.write_bytes(receipt.read_bytes())
        before = control_snapshot(self.root)
        (self.root / "docs" / "new.md").write_text(
            "# New guide\n\nVerified shared guidance.\n",
            encoding="utf-8",
            newline="\n",
        )

        completed = self._run("apply", in_repository_receipt, "--approval", preview["approval"])

        self.assertEqual(completed.returncode, 2)
        result = json.loads(completed.stdout)
        self.assertEqual(result["classification"], "receipt-must-be-outside-repository")
        self.assertFalse(result["successful_event_recorded"])
        self.assertEqual(control_snapshot(self.root), before)

    def test_candidate_rejects_a_new_unapproved_finding(self):
        receipt, preview = self._prepare()
        before = control_snapshot(self.root)
        (self.root / "docs" / "new.md").write_text(
            "# New guide\n\n[Unexpected](missing.md)\n",
            encoding="utf-8",
            newline="\n",
        )

        completed = self._run("apply", receipt, "--approval", preview["approval"])

        self.assertEqual(completed.returncode, 2)
        result = json.loads(completed.stdout)
        self.assertEqual(result["classification"], "new-unapproved-finding")
        self.assertFalse(result["successful_event_recorded"])
        self.assertEqual(control_snapshot(self.root), before)

    def test_closeout_rejects_deletion_of_an_existing_approved_document(self):
        request = self._missing_link_treatment_request()
        request["treatments"][0]["files"] = ["docs/guide.md", "docs/new.md"]
        receipt, preview = self._prepare(request)
        before = control_snapshot(self.root)
        (self.root / "docs" / "new.md").write_text(
            "# New guide\n\nVerified shared guidance.\n",
            encoding="utf-8",
            newline="\n",
        )
        (self.root / "docs" / "guide.md").unlink()

        completed = self._run("apply", receipt, "--approval", preview["approval"])

        self.assertEqual(completed.returncode, 2)
        result = json.loads(completed.stdout)
        self.assertEqual(result["classification"], "approved-document-deleted")
        self.assertFalse(result["successful_event_recorded"])
        self.assertEqual(control_snapshot(self.root), before)

    def test_closeout_requires_every_approved_file_to_change(self):
        request = self._missing_link_treatment_request()
        request["treatments"][0]["files"] = ["docs/guide.md", "docs/new.md"]
        receipt, preview = self._prepare(request)
        before = control_snapshot(self.root)
        (self.root / "docs" / "new.md").write_text(
            "# New guide\n\nVerified shared guidance.\n",
            encoding="utf-8",
            newline="\n",
        )

        completed = self._run("apply", receipt, "--approval", preview["approval"])

        self.assertEqual(completed.returncode, 2)
        result = json.loads(completed.stdout)
        self.assertEqual(result["classification"], "approved-document-unchanged")
        self.assertFalse(result["successful_event_recorded"])
        self.assertEqual(control_snapshot(self.root), before)

    def test_closeout_refreshes_verified_document_evidence(self):
        self._persist_verified_readme()
        request = self._missing_link_treatment_request()
        request["treatments"][0]["files"] = ["docs/README.md"]
        receipt, preview = self._prepare(request)
        (self.root / "docs" / "README.md").write_text(
            "# Documentation\n\n[Guide](guide.md)\n",
            encoding="utf-8",
            newline="\n",
        )

        completed = self._run("apply", receipt, "--approval", preview["approval"])

        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "applied")
        state = docs_checker.load_operational_state(self.root)
        self.assertEqual(
            state["verified_documents"][0]["digest"],
            docs_checker.normalized_content_digest(self.root / "docs" / "README.md"),
        )
        self.assertEqual(state["trust_coverage"]["status"], "verified")


if __name__ == "__main__":
    unittest.main()
