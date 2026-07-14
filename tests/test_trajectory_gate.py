import ast
import hashlib
import importlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import trajectory_gate
import trajectory_discovery_contract
import trajectory_discovery_capture
import trajectory_routes
from skills.docs.scripts._docs_checker import discovery as discovery_module
from skills.docs.scripts._docs_checker.discovery import (
    INIT_DISCOVERY_LIMITS,
    discover_init_scope,
)
from skills.docs.scripts._docs_checker.paths import (
    ANYWHERE_PRUNE_DIRS,
    REPOSITORY_ROOT_ONLY_PRUNE_DIRS,
)


def finding_identities(count):
    identities = []
    for index in range(count):
        digest = hashlib.sha256(f"doctor-finding-{index}".encode("utf-8")).hexdigest()
        identities.append(
            {
                "id": f"DOC-{digest[:8].upper()}",
                "fingerprint": digest,
            }
        )
    return identities


def refresh_discovery_checksum(action):
    """Recompute receipt coherence to isolate structural validation in tests."""
    capture = importlib.import_module("trajectory_discovery_capture")
    payload = {
        field: action[field]
        for field in capture.DOCTOR_DISCOVERY_RECEIPT_FIELDS
    }
    checksum = capture._canonical_receipt_checksum(payload)
    if checksum is None:
        raise AssertionError("test mutation is not an exact serializable receipt")
    action["receipt_checksum"] = checksum


def _write_markdown(root, relative, text="# Evidence\n"):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@lru_cache(maxsize=None)
def actual_doctor_discovery_payload(status, scope="docs", map_name="README.md", physical=True):
    """Return one real Task 5 receipt with only its absolute root removed."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        explicit_scope = None
        if status == "ready":
            explicit_scope = scope
            for relative in (
                map_name,
                "guide.md",
                "current/fix.md",
                "build/guide.md",
                "node_modules/private.md",
            ):
                _write_markdown(root / scope, relative)
        elif status == "choice-required":
            _write_markdown(root, "docs/README.md")
            _write_markdown(root, "wiki/index.md")
        elif status == "no-candidates":
            pass
        elif status == "stopped" and physical:
            for index in range(INIT_DISCOVERY_LIMITS["child_entries_per_container"] + 1):
                (root / f"entry-{index:03d}").mkdir()
        elif status == "stopped":
            explicit_scope = scope
            for directory_index in range(3):
                for file_index in range(90):
                    _write_markdown(
                        root / scope,
                        f"part-{directory_index}/page-{file_index:03d}.md",
                    )
        elif status == "batch-limited":
            explicit_scope = scope
            for index in range(INIT_DISCOVERY_LIMITS["content_files"] + 1):
                _write_markdown(root / scope, f"page-{index:03d}.md")
        else:
            raise AssertionError(f"unsupported Task 5 fixture status: {status}")

        payload = discover_init_scope(root, explicit_scope)
        if payload["status"] != status:
            raise AssertionError(
                f"Task 5 fixture produced {payload['status']!r}, expected {status!r}"
            )
        return trajectory_discovery_contract.build_doctor_discovery_action(payload)


@lru_cache(maxsize=None)
def actual_doctor_prune_payload():
    """Return a real ready receipt containing two independently observed prunes."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for relative in (
            "README.md",
            "guide.md",
            ".cache/private.md",
            "node_modules/private.md",
        ):
            _write_markdown(root / "docs", relative)

        payload = discover_init_scope(root, "docs")
        if payload["status"] != "ready" or len(payload["prunes"]["applied_paths"]) != 2:
            raise AssertionError("Task 5 prune fixture did not observe exactly two prunes")
        return trajectory_discovery_contract.build_doctor_discovery_action(payload)


@lru_cache(maxsize=None)
def actual_doctor_candidate_limit_payload():
    """Return the real Task 5 receipt whose 65th candidate is the boundary."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for child_index in range(22):
            for doc_name in ("docs", "documentation", "wiki"):
                (root / f"p{child_index:02d}" / doc_name).mkdir(parents=True)

        payload = discover_init_scope(root)
        expected_boundary = [{"kind": "candidate-roots", "path": "p21/documentation"}]
        if (
            payload["status"] != "stopped"
            or payload["observed"]["candidate_roots"] != 65
            or len(payload["candidates"]) != 64
            or payload["next_boundary"] != expected_boundary
        ):
            raise AssertionError("Task 5 candidate-limit fixture shape changed")
        return trajectory_discovery_contract.build_doctor_discovery_action(payload)


@lru_cache(maxsize=None)
def actual_doctor_sibling_docs_limit_payload():
    """Return a real receipt whose boundary is the next sibling's docs root."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for child_index in range(65):
            (root / f"p{child_index:02d}" / "docs").mkdir(parents=True)

        payload = discover_init_scope(root)
        if (
            payload["status"] != "stopped"
            or len(payload["candidates"]) != 64
            or payload["candidates"][-1]["path"] != "p63/docs"
            or payload["next_boundary"]
            != [{"kind": "candidate-roots", "path": "p64/docs"}]
        ):
            raise AssertionError("Task 5 sibling-docs fixture shape changed")
        return trajectory_discovery_contract.build_doctor_discovery_action(payload)


@lru_cache(maxsize=None)
def actual_doctor_wiki_tail_limit_payload():
    """Return a real receipt whose sparse same-parent boundary is wiki."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for child_index in range(21):
            for doc_name in ("docs", "documentation", "wiki"):
                (root / f"p{child_index:02d}" / doc_name).mkdir(parents=True)
        for doc_name in ("docs", "wiki"):
            (root / "p21" / doc_name).mkdir(parents=True)

        payload = discover_init_scope(root)
        if (
            payload["status"] != "stopped"
            or len(payload["candidates"]) != 64
            or payload["candidates"][-1]["path"] != "p21/docs"
            or payload["next_boundary"]
            != [{"kind": "candidate-roots", "path": "p21/wiki"}]
        ):
            raise AssertionError("Task 5 wiki-tail fixture shape changed")
        return trajectory_discovery_contract.build_doctor_discovery_action(payload)


@lru_cache(maxsize=None)
def actual_doctor_cross_parent_limit_payload():
    """Return a real receipt with a boundary in the following parent."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for child_index in range(21):
            for doc_name in ("docs", "documentation", "wiki"):
                (root / f"p{child_index:02d}" / doc_name).mkdir(parents=True)
        (root / "p21" / "wiki").mkdir(parents=True)
        (root / "p22" / "docs").mkdir(parents=True)
        (root / "p23" / "docs").mkdir(parents=True)

        payload = discover_init_scope(root)
        if (
            payload["status"] != "stopped"
            or len(payload["candidates"]) != 64
            or payload["candidates"][-1]["path"] != "p21/wiki"
            or payload["next_boundary"]
            != [{"kind": "candidate-roots", "path": "p22/docs"}]
        ):
            raise AssertionError("Task 5 cross-parent fixture shape changed")
        return trajectory_discovery_contract.build_doctor_discovery_action(payload)


@lru_cache(maxsize=None)
def actual_doctor_logical_boundary_payload():
    """Return a real Task 5 result stopped by a disappearing selected scope."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        docs = root / "docs"
        docs.mkdir()
        real_lstat = os.lstat
        selected_scope_calls = 0

        def disappearing_scope(path):
            nonlocal selected_scope_calls
            if Path(path) == docs:
                selected_scope_calls += 1
                if selected_scope_calls >= 2:
                    raise FileNotFoundError(path)
            return real_lstat(path)

        with patch.object(
            discovery_module.os,
            "lstat",
            side_effect=disappearing_scope,
        ):
            payload = discover_init_scope(root, "docs")

        expected_boundary = [{"kind": "missing-container", "path": "docs"}]
        if (
            payload["status"] != "stopped"
            or payload["physical_limit"] is not None
            or payload["next_boundary"] != expected_boundary
        ):
            raise AssertionError("Task 5 logical-boundary fixture shape changed")
        return trajectory_discovery_contract.build_doctor_discovery_action(payload)


def route_mutations(actions):
    """Return deterministic, independently named one-change route counterexamples."""
    base = deepcopy(actions)
    mutations = []

    def add(name, expected, mutate):
        candidate = deepcopy(base)
        mutate(candidate)
        mutations.append((name, candidate, expected))

    first_kind = base[0].get("status") if base else None
    add(
        "remove-first-map-read",
        "retrieval.invalid_map_read" if first_kind == "complete" and len(base) > 2 else "retrieval.missing_map_read",
        lambda candidate: candidate.pop(0),
    )
    add(
        "duplicate-first-map-read",
        "retrieval.duplicate_map_read" if first_kind == "complete" else "retrieval.invalid_map_route",
        lambda candidate: candidate.insert(1, deepcopy(candidate[0])),
    )
    add(
        "swap-map-and-checker",
        "retrieval.map_read_not_first",
        lambda candidate: candidate.__setitem__(
            slice(0, len(candidate)), [candidate[-1], *candidate[1:-1], candidate[0]]
        ),
    )
    add(
        "late-repository-read-after-checker",
        "retrieval.checker_not_final",
        lambda candidate: candidate.append(dict(candidate[0], paths=["STATE.md"], status="complete")),
    )
    unknown_index = 1 if len(base) > 1 else 0
    add(
        "unknown-action-kind",
        "retrieval.unknown_action_kind:unknown-action",
        lambda candidate: candidate[unknown_index].update(kind="unknown-action"),
    )
    checker_index = next(index for index, action in enumerate(base) if action.get("kind") == "checker")
    add(
        "failed-checker-status",
        "retrieval.checker_failed",
        lambda candidate: candidate[checker_index].update(status="error"),
    )
    add(
        "checker-path-smuggling",
        "retrieval.invalid_action_paths",
        lambda candidate: candidate[checker_index].update(paths=["README.md"]),
    )
    add(
        "checker-count-plus-one",
        "retrieval.repeated_checker",
        lambda candidate: candidate[checker_index].update(count=2),
    )
    add(
        "forbidden-source-path",
        "retrieval.forbidden_path",
        lambda candidate: candidate[0].update(paths=["src/main.py"]),
    )
    malformed_index = next(
        index for index, action in enumerate(base) if isinstance(action.get("paths"), list)
    )
    add(
        "malformed-path-array",
        "retrieval.invalid_action_paths",
        lambda candidate: candidate[malformed_index].update(paths=[7]),
    )
    if first_kind == "missing":
        probe_index = next(index for index, action in enumerate(base) if action.get("kind") == "bounded-probe")
        combined_index = next(index for index, action in enumerate(base) if action.get("kind") == "combined-read")
        add(
            "empty-fallback-paths",
            "retrieval.empty_fallback_paths",
            lambda candidate: candidate[probe_index].update(paths=[]),
        )
        add(
            "failed-fallback-status",
            "retrieval.fallback_action_failed",
            lambda candidate: candidate[probe_index].update(status="error"),
        )
        add(
            "fallback-order-swap",
            "retrieval.invalid_map_route",
            lambda candidate: candidate.__setitem__(
                slice(probe_index, combined_index + 1),
                [candidate[combined_index], candidate[probe_index]],
            ),
        )
        add(
            "combined-path-plus-one",
            "retrieval.action_path_budget",
            lambda candidate: candidate[combined_index]["paths"].append("docs/extra.md"),
        )
        add(
            "combined-read-not-from-probe",
            "retrieval.invalid_map_route",
            lambda candidate: candidate[combined_index].update(paths=["docs/hidden.md"]),
        )
        add(
            "revisit-confirmed-missing-map",
            "retrieval.invalid_map_route",
            lambda candidate: candidate[probe_index]["paths"].append("docs/README.md"),
        )
        add(
            "canonical-duplicate-combined-path",
            "retrieval.invalid_action_paths",
            lambda candidate: candidate[combined_index].update(paths=["README.md", r"README.md"]),
        )
        add(
            "remove-combined-read",
            "retrieval.missing_combined_read",
            lambda candidate: candidate.pop(combined_index),
        )
    else:
        hot_index = next(index for index, action in enumerate(base) if index and action.get("kind") == "read-map")
        add(
            "failed-hot-path-status",
            "retrieval.mapped_read_failed",
            lambda candidate: candidate[hot_index].update(status="missing"),
        )
        add(
            "duplicate-hot-path-target",
            "retrieval.duplicate_map_read",
            lambda candidate: candidate[hot_index].update(paths=candidate[0]["paths"]),
        )
        add(
            "empty-hot-paths",
            "retrieval.invalid_action_paths",
            lambda candidate: candidate[hot_index].update(paths=[]),
        )
        add(
            "canonical-duplicate-hot-path",
            "retrieval.invalid_action_paths",
            lambda candidate: candidate[hot_index].update(
                paths=["docs/current/STATE.md", r"docs\current\STATE.md"]
            ),
        )
    add(
        "remove-checker",
        "retrieval.missing_checker",
        lambda candidate: candidate.pop(checker_index),
    )
    add(
        "checker-before-orientation",
        "retrieval.checker_not_final",
        lambda candidate: candidate.insert(0, candidate.pop(checker_index)),
    )
    add(
        "action-budget-plus-one",
        "retrieval.docs_action_budget",
        lambda candidate: candidate.append(dict(candidate[0], paths=["DESIGN.md"], status="complete")),
    )
    return mutations


@lru_cache(maxsize=None)
def _slop_fixture():
    """Load the Task 8A synthetic nightmare fixture (evals/doctor-slop-fixture.json)."""
    return json.loads(
        (ROOT / "evals" / "doctor-slop-fixture.json").read_text(encoding="utf-8")
    )


def _fixture_required_unique_truth_ids(fixture):
    return {
        item["id"]
        for item in fixture["unique_truth_inventory"]
        if item.get("requires_disposition") is True
    }


def _fixture_disposition_coverage(manifest_entries):
    """Return {id: entry} for manifest entries that name an explicit surviving disposition.

    An entry counts as coverage only when it carries a non-empty ``disposition`` string and
    either a non-empty ``destination`` or the explicit ``exclude-out-of-scope`` disposition
    (the one disposition kind that legitimately has no in-repository destination).
    """
    covered = {}
    duplicates = []
    for entry in manifest_entries:
        if not isinstance(entry, dict):
            continue
        identity = entry.get("id")
        disposition = entry.get("disposition")
        destination = entry.get("destination")
        if not isinstance(identity, str) or not identity:
            continue
        if not isinstance(disposition, str) or not disposition:
            continue
        has_destination = destination not in (None, "", [])
        if not has_destination and disposition != "exclude-out-of-scope":
            continue
        if identity in covered:
            duplicates.append(identity)
        covered[identity] = entry
    return covered, sorted(set(duplicates))


def _fixture_paths(value):
    if isinstance(value, list):
        return {item for item in value if isinstance(item, str)}
    return {value} if isinstance(value, str) else set()


def zero_unique_truth_loss_oracle(fixture, manifest_entries):
    """Implementation-independent oracle: every load-bearing fact needs a surviving disposition.

    Deliberately does not import or call anything from tools/trajectory_gate.py or
    tools/trajectory_routes.py: it only inventories the fixture's declared unique truth and
    checks disposition coverage using logic local to this test file, so it can judge Task 8B's
    eventual production behavior without depending on it.
    """
    required = _fixture_required_unique_truth_ids(fixture)
    covered, duplicates = _fixture_disposition_coverage(manifest_entries)
    inventory = {
        item["id"]: item
        for item in fixture["unique_truth_inventory"]
        if item.get("requires_disposition") is True
    }
    invalid_ids = sorted(set(covered) - required)
    missing = sorted(required - set(covered))
    intent_errors = []
    for identity, item in inventory.items():
        entry = covered.get(identity)
        if entry is None:
            continue
        kind = item.get("kind")
        destinations = _fixture_paths(entry.get("destination"))
        expected = _fixture_paths(item.get("location"))
        disposition = entry.get("disposition")
        if kind == "protected-public-surface":
            if disposition != "retain-protected" or destinations != expected:
                intent_errors.append(identity)
        elif kind == "local-only-authoritative-truth":
            if disposition != "local-preserve" or not destinations or not all(
                path.startswith(".local/") for path in destinations
            ):
                intent_errors.append(identity)
        elif kind == "out-of-scope-vendor-symlink":
            if disposition != "exclude-out-of-scope" or entry.get("destination") is not None:
                intent_errors.append(identity)
        elif kind == "unrelated-dirty-work":
            if disposition != "preserve-unrelated" or destinations != expected:
                intent_errors.append(identity)
    invalid = bool(missing or invalid_ids or duplicates or intent_errors)
    return {
        "zero_unique_truth_loss": not invalid,
        "missing_ids": missing,
        "invalid_ids": invalid_ids,
        "duplicate_ids": duplicates,
        "intent_errors": sorted(set(intent_errors)),
    }


class SyntheticSlopFixtureOracleTests(unittest.TestCase):
    """Task 8A: oracle proof over the synthetic nightmare fixture.

    These tests exercise only the fixture and the implementation-independent oracle above;
    they do not touch tools/trajectory_gate.py or tools/trajectory_routes.py.
    """

    def setUp(self):
        self.fixture = _slop_fixture()

    def test_fixture_declares_every_required_nightmare_shape(self):
        fixture = self.fixture
        self.assertTrue(fixture.get("synthetic") is True)
        self.assertEqual(len(fixture["duplicated_mixed_purpose_docs"]["paths"]), 2)

        unique_kinds = {item["kind"] for item in fixture["unique_truth_inventory"]}
        self.assertEqual(
            unique_kinds,
            {
                "load-bearing-decision",
                "bloated-current-state",
                "orphaned-maintained-instructions",
                "deliberate-archive-material",
                "conflicting-protected-intent",
                "verified-source-later-changed",
                "out-of-scope-vendor-symlink",
                "unrelated-dirty-work",
                "local-only-authoritative-truth",
                "protected-public-surface",
            },
        )

        hidden = [
            item
            for item in fixture["unique_truth_inventory"]
            if item.get("hidden_in_duplicate")
        ]
        self.assertEqual(len(hidden), 1)
        self.assertEqual(hidden[0]["id"], "UNIQ-RETRY-BACKOFF-0001")
        self.assertEqual(hidden[0]["location"], "docs/current/guide-duplicate-b.md")
        self.assertIn(
            hidden[0]["location"], fixture["duplicated_mixed_purpose_docs"]["paths"]
        )

        self.assertIn(".diataxis/local-map.json", fixture["tree"])
        missing_clone = fixture["local_only_authoritative_truth"]["missing_clone_case"]
        self.assertEqual(
            missing_clone["expected_status"], "declared-local-knowledge-unavailable"
        )

        protected = fixture["protected_public_surfaces"]
        for key in ("readme", "community", "release", "docs_site_route", "wiki_declaration"):
            self.assertIn(key, protected)

        conflict_ids = {
            item["id"]
            for item in fixture["unique_truth_inventory"]
            if item["kind"] == "conflicting-protected-intent"
        }
        self.assertEqual(len(conflict_ids), 2)
        merge = fixture["branch_merge_conflict"]
        self.assertEqual(merge["status"], "state-conflict")
        self.assertEqual(merge["priority"], "P0")
        self.assertTrue(merge["read_only"])
        self.assertTrue(merge["reconstruction_preview"])
        cleanup = fixture["no_git_cleanup"]
        self.assertFalse(cleanup["git_available"])
        self.assertEqual(cleanup["disposition"], "archive")
        self.assertFalse(cleanup["hard_delete_approved"])

    def test_approved_disposition_manifest_achieves_zero_unique_truth_loss(self):
        result = zero_unique_truth_loss_oracle(
            self.fixture, self.fixture["disposition_manifest"]
        )

        self.assertEqual(result["missing_ids"], [])
        self.assertTrue(result["zero_unique_truth_loss"])

    def test_oracle_rejects_manifest_that_silently_drops_the_hidden_unique_decision(self):
        adversarial = self.fixture["adversarial_manifest_missing_unique_decision"]

        result = zero_unique_truth_loss_oracle(
            self.fixture, adversarial["disposition_manifest"]
        )

        self.assertFalse(result["zero_unique_truth_loss"])
        self.assertIn("UNIQ-RETRY-BACKOFF-0001", result["missing_ids"])

    def test_oracle_rejection_holds_even_though_the_bad_manifest_scores_better(self):
        approved = self.fixture["approved_transformation"]
        adversarial = self.fixture["adversarial_manifest_missing_unique_decision"]

        # The adversarial manifest produces a *smaller* hot-path byte count than the approved
        # one -- by a naive "fewer bytes is healthier" score it looks like an improvement.
        self.assertLess(adversarial["hot_path_bytes_after"], approved["hot_path_bytes_after"])
        self.assertEqual(adversarial["naive_structure_status"], "improved")

        # The oracle must still refuse it: it is independent of any byte/structure score and
        # only cares whether every unique load-bearing fact has a surviving disposition.
        approved_result = zero_unique_truth_loss_oracle(
            self.fixture, self.fixture["disposition_manifest"]
        )
        adversarial_result = zero_unique_truth_loss_oracle(
            self.fixture, adversarial["disposition_manifest"]
        )

        self.assertTrue(approved_result["zero_unique_truth_loss"])
        self.assertFalse(adversarial_result["zero_unique_truth_loss"])

    def test_oracle_rejects_duplicate_unknown_and_moved_intent_entries(self):
        fixture = self.fixture
        manifest = deepcopy(fixture["disposition_manifest"])
        manifest.append(deepcopy(manifest[0]))
        manifest.append({"id": "UNIQ-FABRICATED", "disposition": "retain", "destination": "docs/x.md"})
        moved = next(entry for entry in manifest if entry["id"] == "UNIQ-PROTECTED-README")
        moved["destination"] = "docs/new-readme.md"
        local = next(
            entry for entry in manifest if entry["id"] == "UNIQ-LOCAL-AUTHORITATIVE-DECISIONS"
        )
        local["destination"] = "docs/shared-decisions.md"

        result = zero_unique_truth_loss_oracle(fixture, manifest)

        self.assertFalse(result["zero_unique_truth_loss"])
        self.assertIn("UNIQ-RETRY-BACKOFF-0001", result["duplicate_ids"])
        self.assertIn("UNIQ-FABRICATED", result["invalid_ids"])
        self.assertIn("UNIQ-PROTECTED-README", result["intent_errors"])
        self.assertIn("UNIQ-LOCAL-AUTHORITATIVE-DECISIONS", result["intent_errors"])

    def test_disposition_manifest_accounts_for_every_unique_fact_and_preserves_intent(self):
        fixture = self.fixture
        inventory = {
            item["id"]
            for item in fixture["unique_truth_inventory"]
            if item.get("requires_disposition") is True
        }
        manifest = fixture["disposition_manifest"]
        manifest_ids = [entry["id"] for entry in manifest]
        self.assertEqual(len(manifest_ids), len(set(manifest_ids)))
        self.assertEqual(set(manifest_ids), inventory)

        by_id = {entry["id"]: entry for entry in manifest}
        local = by_id["UNIQ-LOCAL-AUTHORITATIVE-DECISIONS"]
        self.assertEqual(local["disposition"], "local-preserve")
        self.assertTrue(local["destination"].startswith(".local/"))
        for item_id in (
            "UNIQ-PROTECTED-README",
            "UNIQ-PROTECTED-COMMUNITY",
            "UNIQ-PROTECTED-RELEASE",
            "UNIQ-PROTECTED-DOCS-SITE",
            "UNIQ-PROTECTED-WIKI",
        ):
            self.assertEqual(by_id[item_id]["disposition"], "retain-protected")

    def test_fixture_oracle_is_deterministic_and_does_not_claim_success_for_open_priority(self):
        fixture = self.fixture
        approved = fixture["disposition_manifest"]
        first = zero_unique_truth_loss_oracle(fixture, approved)
        second = zero_unique_truth_loss_oracle(fixture, approved)
        self.assertEqual(first, second)
        self.assertTrue(first["zero_unique_truth_loss"])

        transformation = fixture["approved_transformation"]
        self.assertEqual(transformation["open_p0_required_by_scope"], 0)
        self.assertEqual(transformation["open_p1_required_by_scope"], 0)
        self.assertLess(
            transformation["hot_path_bytes_after"],
            transformation["hot_path_bytes_before"],
        )


class TrajectoryGateTests(unittest.TestCase):
    def load(self, name):
        return json.loads((ROOT / "evals" / "trajectory" / name).read_text(encoding="utf-8"))

    def mapped_actions(self, hot=True):
        base = self.load("bulwark-map-accepted.json")["retrieval"]["actions"]
        result = [dict(deepcopy(base[0]), status="complete")]
        if hot:
            result.append(dict(deepcopy(base[0]), paths=["STATE.md"], status="complete"))
        result.append(deepcopy(base[3]))
        return result

    def missing_map_actions(self):
        base = self.load("bulwark-map-accepted.json")["retrieval"]["actions"]
        return [deepcopy(base[index]) for index in (0, 1, 2, 3)]

    def doctor_actions(self, groups=(), *, missing=False):
        actions = self.missing_map_actions() if missing else self.mapped_actions()
        for name, paths in groups:
            actions.append(
                {
                    "owner": "docs",
                    "kind": "post-check-read",
                    "group": name,
                    "paths": list(paths),
                    "status": "complete",
                }
            )
        return actions

    def bind_doctor_findings(self, receipt, count=None):
        if count is None:
            count = receipt["outcome"]["findings"]
        identities = finding_identities(count)
        checker = next(
            action
            for action in receipt["retrieval"]["actions"]
            if action["kind"] == "checker"
        )
        scope = receipt["outcome"].setdefault("scope", ".")
        checker.setdefault("scope", scope)
        checker.update(
            status="clean" if count == 0 else "findings",
            compact_finding_count=count,
            compact_findings=deepcopy(identities),
        )
        receipt["outcome"].update(
            findings=count,
            reported_finding_count=count,
            reported_findings=deepcopy(identities),
        )
        return identities

    def doctor_discovery_action(
        self,
        status="ready",
        scope="docs",
        *,
        map_name="README.md",
        physical=True,
    ):
        return {
            "owner": "docs",
            "kind": "init-discovery",
            **deepcopy(
                actual_doctor_discovery_payload(
                    status,
                    scope,
                    map_name,
                    physical,
                )
            ),
        }

    def bind_terminal_doctor_outcome(self, receipt, discovery):
        receipt["outcome"].update(
            status="incomplete",
            findings=0,
            reported_finding_count=0,
            reported_findings=[],
            findings_exhaustive=False,
            scope=(
                discovery["selected_scope"]
                or discovery["jurisdiction_scope"]
            ),
        )

    def evaluate_doctor_discovery(self, discovery):
        """Evaluate one real or mutated discovery action in its valid route shell."""
        receipt = self.load("bulwark-map-accepted.json")
        receipt["command"] = "doctor"
        receipt["presentation"].pop("tree")
        receipt["presentation"].pop("tree_features")
        if discovery["status"] == "ready":
            scope = discovery["selected_scope"]
            checker = self.mapped_actions(False)[-1]
            checker["scope"] = scope
            receipt["outcome"].update(scope=scope, findings_exhaustive=True)
            receipt["retrieval"]["actions"] = [discovery, checker]
            self.bind_doctor_findings(receipt)
        else:
            receipt["retrieval"]["actions"] = [discovery]
            self.bind_terminal_doctor_outcome(receipt, discovery)
            receipt["presentation"].pop("health_meter")
        return trajectory_gate.evaluate(receipt)

    def scoped_doctor_receipt(self, scope="packages/pkg/docs"):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["command"] = "doctor"
        discovery = self.doctor_discovery_action(scope=scope)
        discovery.update(
            normalized_scope=scope,
            jurisdiction_scope=scope,
        )
        checker = self.mapped_actions(False)[-1]
        checker["scope"] = scope
        receipt["outcome"].update(
            scope=scope,
            findings_exhaustive=True,
        )
        receipt["retrieval"]["actions"] = [
            discovery,
            {
                "owner": "docs",
                "kind": "combined-read",
                "paths": [f"{scope}/guide.md"],
                "status": "complete",
            },
            checker,
            {
                "owner": "docs",
                "kind": "post-check-read",
                "group": "treatment-evidence",
                "paths": [f"{scope}/current/fix.md"],
                "status": "complete",
            },
        ]
        receipt["presentation"].pop("tree")
        receipt["presentation"].pop("tree_features")
        self.bind_doctor_findings(receipt)
        return receipt

    def test_accepted_map_receipt_passes_with_host_overhead_separated(self):
        result = trajectory_gate.evaluate(self.load("bulwark-map-accepted.json"))

        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["metrics"]["docs_actions"], 4)
        self.assertEqual(result["metrics"]["external_actions"], 2)
        self.assertEqual(result["metrics"]["checker_runs"], 1)
        self.assertIn("usage.unpaired_host_baseline", result["warnings"])

    def test_external_repository_actions_cannot_hide_as_host_overhead(self):
        cases = (
            (
                "path-bearing-read",
                {
                    "owner": "host",
                    "kind": "read-map",
                    "paths": ["src/main.py"],
                    "status": "complete",
                },
            ),
            (
                "broad-search",
                {
                    "owner": "host",
                    "kind": "repo-wide-search",
                    "status": "complete",
                },
            ),
        )
        for name, action in cases:
            with self.subTest(name=name):
                receipt = self.load("bulwark-map-accepted.json")
                receipt["retrieval"]["actions"].append(action)

                result = trajectory_gate.evaluate(receipt)

                self.assertEqual(result["status"], "FAIL")
                self.assertIn("retrieval.external_repository_action", result["errors"])

    def test_map_check_and_doctor_require_health_meter(self):
        for command in ("map", "check", "doctor"):
            with self.subTest(command=command):
                receipt = self.load("bulwark-map-accepted.json")
                receipt["command"] = command
                receipt["presentation"].pop("health_meter", None)
                if command != "map":
                    receipt["presentation"].pop("tree")
                    receipt["presentation"].pop("tree_features")

                result = trajectory_gate.evaluate(receipt)

                self.assertEqual(result["status"], "FAIL")
                self.assertIn("presentation.missing_health_meter", result["errors"])

    def test_health_meter_requires_exact_cells_and_percentage(self):
        valid = "Docs [" + "█" * 14 + "░" * 6 + "] 70%"
        invalid = (
            valid.replace("░" * 6, "░" * 5),
            f"```{valid}```",
            "Docs [" + "█" * 15 + "░" * 5 + "] 70%",
        )
        for meter in invalid:
            with self.subTest(meter=meter):
                receipt = self.load("bulwark-map-accepted.json")
                receipt["presentation"]["health_meter"] = meter

                result = trajectory_gate.evaluate(receipt)

                self.assertEqual(result["status"], "FAIL")
                self.assertIn("presentation.invalid_health_meter", result["errors"])

    def test_health_meter_requires_filled_cells_before_empty_cells(self):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["presentation"]["health_meter"] = "Docs [" + "░" * 6 + "█" * 14 + "] 70%"

        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "FAIL")
        self.assertIn("presentation.invalid_health_meter", result["errors"])

    def test_health_meter_is_bound_to_checker_evidence(self):
        cases = (
            (
                "missing-checker-health",
                lambda receipt: receipt["retrieval"]["actions"][3].pop("health"),
                "presentation.missing_checker_health",
            ),
            (
                "different-displayed-percentage",
                lambda receipt: receipt["presentation"].update(
                    health_meter="Docs [█████░░░░░░░░░░░░░░░] 25%"
                ),
                "presentation.health_meter_mismatch",
            ),
            (
                "checker-percentage-disagrees-with-meter",
                lambda receipt: receipt["retrieval"]["actions"][3]["health"].update(
                    percentage=25
                ),
                "presentation.invalid_checker_health",
            ),
            (
                "unsupported-checker-rubric",
                lambda receipt: receipt["retrieval"]["actions"][3]["health"].update(
                    rubric_version=1
                ),
                "presentation.invalid_checker_health",
            ),
        )
        for name, mutate, expected in cases:
            with self.subTest(name=name):
                receipt = self.load("bulwark-map-accepted.json")
                mutate(receipt)

                result = trajectory_gate.evaluate(receipt)

                self.assertEqual(result["status"], "FAIL")
                self.assertIn(expected, result["errors"])

    def test_doctor_and_check_exhaustive_claims_require_matching_scope_evidence(self):
        for command in ("check", "doctor"):
            with self.subTest(command=command):
                receipt = self.load("bulwark-map-accepted.json")
                receipt["command"] = command
                receipt["outcome"]["findings_exhaustive"] = True
                receipt["outcome"]["scope"] = "." if command == "doctor" else "docs"
                receipt["retrieval"]["actions"] = self.mapped_actions(True)
                receipt["retrieval"]["actions"][-1]["scope"] = (
                    "." if command == "doctor" else "docs"
                )
                if command == "doctor":
                    self.bind_doctor_findings(receipt)
                receipt["presentation"].pop("tree")
                receipt["presentation"].pop("tree_features")

                accepted = trajectory_gate.evaluate(receipt)
                self.assertEqual(accepted["status"], "PASS", accepted["errors"])

                receipt["retrieval"]["actions"][-1].pop("scope")
                missing = trajectory_gate.evaluate(receipt)
                self.assertEqual(missing["status"], "FAIL")
                self.assertIn("retrieval.missing_checker_scope", missing["errors"])

                receipt["retrieval"]["actions"][-1]["scope"] = (
                    "docs" if command == "doctor" else "."
                )
                mismatched = trajectory_gate.evaluate(receipt)
                self.assertEqual(mismatched["status"], "FAIL")
                self.assertIn("retrieval.checker_scope_mismatch", mismatched["errors"])

    def test_context_does_not_require_health_meter(self):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["command"] = "context"
        receipt["presentation"].pop("health_meter", None)
        receipt["presentation"].pop("tree")
        receipt["presentation"].pop("tree_features")
        receipt["retrieval"]["actions"] = [
            {
                "owner": "docs",
                "kind": "combined-read",
                "paths": ["README.md"],
                "status": "complete",
            }
        ]

        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "PASS", result["errors"])

    def test_valid_route_factories_cover_each_command_boundary(self):
        cases = (
            ("mapped-map", "map", self.mapped_actions(False), True),
            ("mapped-map-hot", "map", self.mapped_actions(True), True),
            ("missing-map", "map", self.missing_map_actions(), True),
            ("mapped-check", "check", self.mapped_actions(True), False),
            ("missing-check", "check", self.missing_map_actions(), False),
            (
                "bounded-context",
                "context",
                [
                    {
                        "owner": "docs",
                        "kind": "combined-read",
                        "paths": ["docs/README.md", "STATE.md"],
                        "status": "complete",
                    }
                ],
                False,
            ),
            ("doctor-zero-groups", "doctor", self.doctor_actions(), False),
            (
                "doctor-discovery-zero-groups",
                "doctor",
                [
                    self.doctor_discovery_action(),
                    dict(self.mapped_actions(False)[-1], scope="docs"),
                ],
                False,
            ),
            (
                "doctor-one-group",
                "doctor",
                self.doctor_actions((("finding-1", ("README.md", "STATE.md")),)),
                False,
            ),
            (
                "doctor-boundary-groups",
                "doctor",
                self.doctor_actions(
                    (
                        ("finding-1", ("README.md", "STATE.md")),
                        ("finding-2", ("PRODUCT.md", "DESIGN.md")),
                    )
                ),
                False,
            ),
        )
        for name, command, actions, needs_tree in cases:
            with self.subTest(name=name):
                receipt = self.load("bulwark-map-accepted.json")
                receipt["command"] = command
                receipt["retrieval"]["actions"] = actions
                if command == "doctor":
                    receipt["outcome"]["scope"] = (
                        "docs" if actions[0]["kind"] == "init-discovery" else "."
                    )
                    self.bind_doctor_findings(receipt)
                if not needs_tree:
                    receipt["presentation"].pop("tree")
                    receipt["presentation"].pop("tree_features")

                result = trajectory_gate.evaluate(receipt)

                self.assertEqual(result["status"], "PASS", result["errors"])

    def test_generated_single_mutations_are_deterministic_and_rejected(self):
        routes = (
            ("mapped", self.mapped_actions(True)),
            ("missing", self.missing_map_actions()),
        )
        for state, actions in routes:
            first_mutations = list(route_mutations(actions))
            second_mutations = list(route_mutations(actions))
            self.assertEqual(first_mutations, second_mutations)

            def observations(mutations, command):
                result = []
                for name, mutated, _ in mutations:
                    receipt = self.load("bulwark-map-accepted.json")
                    receipt["command"] = command
                    receipt["retrieval"]["actions"] = mutated
                    if command != "map":
                        receipt["presentation"].pop("tree")
                        receipt["presentation"].pop("tree_features")
                    evaluated = trajectory_gate.evaluate(receipt)
                    result.append((name, evaluated["status"], tuple(evaluated["errors"])))
                return result

            for command in ("map", "check", "doctor"):
                first_results = observations(first_mutations, command)
                second_results = observations(second_mutations, command)
                self.assertEqual(len(first_results), len(second_results))
                self.assertEqual(first_results, second_results)
                for name, mutated, expected in first_mutations:
                    with self.subTest(state=state, command=command, mutation=name):
                        receipt = self.load("bulwark-map-accepted.json")
                        receipt["command"] = command
                        receipt["retrieval"]["actions"] = mutated
                        if command != "map":
                            receipt["presentation"].pop("tree")
                            receipt["presentation"].pop("tree_features")

                        result = trajectory_gate.evaluate(receipt)

                        self.assertEqual(result["status"], "FAIL")
                        expected_error = (
                            "retrieval.doctor_init_discovery_required"
                            if command == "doctor"
                            and state == "missing"
                            and mutated
                            and mutated[0].get("kind") == "read-map"
                            and mutated[0].get("status") == "missing"
                            else "retrieval.doctor_precheck_budget"
                            if command == "doctor"
                            and expected == "retrieval.docs_action_budget"
                            else expected
                        )
                        self.assertIn(expected_error, result["errors"])

    def test_context_and_doctor_file_boundaries_are_explicit(self):
        context = self.load("bulwark-map-accepted.json")
        context["command"] = "context"
        context["retrieval"]["actions"] = [
            {
                "owner": "docs",
                "kind": "combined-read",
                "paths": ["README.md", "STATE.md", "PRODUCT.md", "DESIGN.md"],
                "status": "complete",
            }
        ]
        context["presentation"].pop("tree")
        context["presentation"].pop("tree_features")
        self.assertEqual(trajectory_gate.evaluate(context)["status"], "PASS")

        context["retrieval"]["actions"][0]["paths"].append("PLAN.md")
        result = trajectory_gate.evaluate(context)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("retrieval.context_file_budget", result["errors"])

        doctor = self.load("bulwark-map-accepted.json")
        doctor["command"] = "doctor"
        doctor["retrieval"]["actions"] = self.doctor_actions(
            (
                ("finding-1", ("README.md", "STATE.md")),
                ("finding-2", ("PRODUCT.md", "DESIGN.md")),
            )
        )
        self.bind_doctor_findings(doctor)
        doctor["presentation"].pop("tree")
        doctor["presentation"].pop("tree_features")
        self.assertEqual(trajectory_gate.evaluate(doctor)["status"], "PASS")

        doctor["retrieval"]["actions"].append(
            {
                "owner": "docs",
                "kind": "post-check-read",
                "group": "finding-3",
                "paths": ["PLAN.md"],
                "status": "complete",
            }
        )
        result = trajectory_gate.evaluate(doctor)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("retrieval.doctor_postcheck_file_budget", result["errors"])

    def test_doctor_can_report_fifty_checker_findings_with_four_content_reads(self):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["command"] = "doctor"
        receipt["outcome"].update(
            findings=50,
            findings_exhaustive=True,
            scope=".",
        )
        receipt["retrieval"]["actions"] = self.doctor_actions(
            (
                ("DOC-00000001", ("README.md",)),
                ("DOC-00000002", ("STATE.md",)),
                ("DOC-00000003", ("PRODUCT.md",)),
                ("DOC-00000004", ("DESIGN.md",)),
            )
        )
        checker = next(
            action
            for action in receipt["retrieval"]["actions"]
            if action["kind"] == "checker"
        )
        checker.update(
            scope=".",
        )
        identities = self.bind_doctor_findings(receipt, 50)
        receipt["presentation"].pop("tree")
        receipt["presentation"].pop("tree_features")

        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "PASS", result["errors"])
        self.assertEqual(result["metrics"]["docs_actions"], 7)
        self.assertEqual(checker["compact_findings"], identities)
        self.assertEqual(receipt["outcome"]["reported_findings"], identities)
        self.assertNotIn("retrieval.doctor_postcheck_group_budget", result["errors"])

        receipt["retrieval"]["actions"].append(
            {
                "owner": "docs",
                "kind": "post-check-read",
                "group": "DOC-00000005",
                "paths": ["PLAN.md"],
                "status": "complete",
            }
        )
        bounded = trajectory_gate.evaluate(receipt)
        self.assertEqual(bounded["status"], "FAIL")
        self.assertIn("retrieval.doctor_postcheck_file_budget", bounded["errors"])

    def test_healthy_doctor_requires_explicit_empty_finding_identity_sets(self):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["command"] = "doctor"
        receipt["retrieval"]["actions"] = self.doctor_actions()
        receipt["presentation"].pop("tree")
        receipt["presentation"].pop("tree_features")
        self.bind_doctor_findings(receipt, 0)

        accepted = trajectory_gate.evaluate(receipt)
        self.assertEqual(accepted["status"], "PASS", accepted["errors"])

        receipt["outcome"].pop("reported_findings")
        rejected = trajectory_gate.evaluate(receipt)
        self.assertEqual(rejected["status"], "FAIL")
        self.assertIn("outcome.invalid_reported_findings", rejected["errors"])

    def test_doctor_rejects_malformed_or_duplicate_finding_identities(self):
        cases = (
            (
                "malformed-id",
                lambda compact, reported: compact[0].update(id="DOC-not-hex"),
                "retrieval.invalid_compact_findings",
            ),
            (
                "short-fingerprint",
                lambda compact, reported: compact[0].update(fingerprint="1234"),
                "retrieval.invalid_compact_findings",
            ),
            (
                "approval-prefixed-fingerprint",
                lambda compact, reported: compact[0].update(
                    fingerprint=f"sha256:{compact[0]['fingerprint']}"
                ),
                "retrieval.invalid_compact_findings",
            ),
            (
                "id-fingerprint-mismatch",
                lambda compact, reported: compact[0].update(id="DOC-FFFFFFFF"),
                "retrieval.invalid_compact_findings",
            ),
            (
                "duplicate-compact-identity",
                lambda compact, reported: compact.__setitem__(1, deepcopy(compact[0])),
                "retrieval.invalid_compact_findings",
            ),
            (
                "duplicate-reported-identity",
                lambda compact, reported: reported.__setitem__(1, deepcopy(reported[0])),
                "outcome.invalid_reported_findings",
            ),
            (
                "missing-identity-field",
                lambda compact, reported: reported[0].pop("fingerprint"),
                "outcome.invalid_reported_findings",
            ),
        )
        for name, mutate, expected in cases:
            with self.subTest(name=name):
                receipt = self.load("bulwark-map-accepted.json")
                receipt["command"] = "doctor"
                receipt["retrieval"]["actions"] = self.doctor_actions()
                receipt["presentation"].pop("tree")
                receipt["presentation"].pop("tree_features")
                self.bind_doctor_findings(receipt, 2)
                checker = next(
                    action
                    for action in receipt["retrieval"]["actions"]
                    if action["kind"] == "checker"
                )
                mutate(checker["compact_findings"], receipt["outcome"]["reported_findings"])

                result = trajectory_gate.evaluate(receipt)

                self.assertEqual(result["status"], "FAIL")
                self.assertIn(expected, result["errors"])

    def test_doctor_requires_exact_finding_counts_and_reported_identity_set(self):
        def replace_reported_with_one(receipt, checker):
            receipt["outcome"]["reported_findings"] = deepcopy(
                receipt["outcome"]["reported_findings"][:1]
            )
            receipt["outcome"]["reported_finding_count"] = 1

        cases = (
            (
                "missing-compact-array",
                lambda receipt, checker: checker.pop("compact_findings"),
                "retrieval.invalid_compact_findings",
            ),
            (
                "missing-reported-array",
                lambda receipt, checker: receipt["outcome"].pop("reported_findings"),
                "outcome.invalid_reported_findings",
            ),
            (
                "compact-count-list-mismatch",
                lambda receipt, checker: checker.update(compact_finding_count=1),
                "retrieval.compact_finding_count_mismatch",
            ),
            (
                "reported-count-list-mismatch",
                lambda receipt, checker: receipt["outcome"].update(reported_finding_count=1),
                "outcome.reported_finding_count_mismatch",
            ),
            (
                "outcome-count-list-mismatch",
                lambda receipt, checker: receipt["outcome"].update(findings=1),
                "outcome.finding_count_mismatch",
            ),
            (
                "omitted-reported-identity",
                lambda receipt, checker: receipt["outcome"]["reported_findings"].pop(),
                "outcome.reported_finding_count_mismatch",
            ),
            (
                "extra-reported-identity",
                lambda receipt, checker: receipt["outcome"]["reported_findings"].append(
                    finding_identities(3)[2]
                ),
                "outcome.reported_finding_count_mismatch",
            ),
            (
                "same-count-different-identity",
                lambda receipt, checker: receipt["outcome"]["reported_findings"].__setitem__(
                    1, finding_identities(3)[2]
                ),
                "outcome.reported_findings_mismatch",
            ),
            (
                "fifty-findings-reported-as-one",
                replace_reported_with_one,
                "outcome.finding_count_mismatch",
            ),
        )
        for name, mutate, expected in cases:
            with self.subTest(name=name):
                count = 50 if name == "fifty-findings-reported-as-one" else 2
                receipt = self.load("bulwark-map-accepted.json")
                receipt["command"] = "doctor"
                receipt["retrieval"]["actions"] = self.doctor_actions()
                receipt["presentation"].pop("tree")
                receipt["presentation"].pop("tree_features")
                self.bind_doctor_findings(receipt, count)
                checker = next(
                    action
                    for action in receipt["retrieval"]["actions"]
                    if action["kind"] == "checker"
                )
                mutate(receipt, checker)

                result = trajectory_gate.evaluate(receipt)

                self.assertEqual(result["status"], "FAIL")
                self.assertIn(expected, result["errors"])

    def test_doctor_missing_map_requires_init_discovery_instead_of_legacy_fallback(self):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["command"] = "doctor"
        receipt["retrieval"]["actions"] = self.missing_map_actions()
        receipt["presentation"].pop("tree")
        receipt["presentation"].pop("tree_features")
        self.bind_doctor_findings(receipt)

        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "FAIL")
        self.assertIn("retrieval.doctor_init_discovery_required", result["errors"])

    def test_doctor_ready_discovery_selects_scope_before_content_and_checker(self):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["command"] = "doctor"
        checker = self.mapped_actions(False)[-1]
        checker["scope"] = "docs"
        receipt["outcome"].update(scope="docs", findings_exhaustive=True)
        receipt["retrieval"]["actions"] = [
            self.doctor_discovery_action(),
            {
                "owner": "docs",
                "kind": "combined-read",
                "paths": ["docs/README.md"],
                "status": "complete",
            },
            checker,
        ]
        receipt["presentation"].pop("tree")
        receipt["presentation"].pop("tree_features")
        self.bind_doctor_findings(receipt)

        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "PASS", result["errors"])
        self.assertEqual(result["metrics"]["checker_runs"], 1)

    def test_doctor_terminal_discovery_results_stop_honestly_before_content(self):
        cases = (
            ("choice-required", True),
            ("no-candidates", True),
            ("stopped", True),
            ("stopped", False),
            ("batch-limited", True),
        )
        for status, physical in cases:
            with self.subTest(status=status, physical=physical):
                receipt = self.load("bulwark-map-accepted.json")
                receipt["command"] = "doctor"
                discovery = self.doctor_discovery_action(status, physical=physical)
                receipt["retrieval"]["actions"] = [discovery]
                self.bind_terminal_doctor_outcome(receipt, discovery)
                receipt["presentation"].pop("tree")
                receipt["presentation"].pop("tree_features")
                receipt["presentation"].pop("health_meter")

                result = trajectory_gate.evaluate(receipt)

                self.assertEqual(result["status"], "PASS", result["errors"])
                self.assertEqual(result["metrics"]["checker_runs"], 0)

    def test_doctor_terminal_discovery_rejects_diagnosis_or_treatment_claims(self):
        cases = (
            (
                "fifty-findings",
                lambda receipt, discovery: receipt["outcome"].update(
                    findings=50,
                    reported_finding_count=50,
                    reported_findings=finding_identities(50),
                ),
            ),
            (
                "malformed-reported-array",
                lambda receipt, discovery: receipt["outcome"].update(
                    reported_findings="none"
                ),
            ),
            (
                "duplicate-reported-identity",
                lambda receipt, discovery: receipt["outcome"].update(
                    reported_finding_count=2,
                    reported_findings=[
                        finding_identities(1)[0],
                        deepcopy(finding_identities(1)[0]),
                    ],
                ),
            ),
            (
                "compact-diagnosis",
                lambda receipt, discovery: discovery.update(
                    compact_finding_count=1,
                    compact_findings=finding_identities(1),
                ),
            ),
            (
                "outcome-compact-diagnosis",
                lambda receipt, discovery: receipt["outcome"].update(
                    compact_finding_count=1,
                    compact_findings=finding_identities(1),
                ),
            ),
            (
                "treatment-claim",
                lambda receipt, discovery: receipt["outcome"].update(
                    treatments=[{"finding_ids": [finding_identities(1)[0]["id"]]}]
                ),
            ),
            (
                "recommended-treatment-claim",
                lambda receipt, discovery: receipt["outcome"].update(
                    recommended_treatments=[
                        {"finding_ids": [finding_identities(1)[0]["id"]]}
                    ]
                ),
            ),
        )
        for name, mutate in cases:
            with self.subTest(name=name):
                receipt = self.load("bulwark-map-accepted.json")
                receipt["command"] = "doctor"
                discovery = self.doctor_discovery_action("choice-required")
                receipt["retrieval"]["actions"] = [discovery]
                self.bind_terminal_doctor_outcome(receipt, discovery)
                receipt["presentation"].pop("tree")
                receipt["presentation"].pop("tree_features")
                receipt["presentation"].pop("health_meter")
                mutate(receipt, discovery)

                result = trajectory_gate.evaluate(receipt)

                self.assertEqual(result["status"], "FAIL")
                self.assertIn(
                    "outcome.invalid_terminal_doctor_diagnosis",
                    result["errors"],
                )

    def test_doctor_terminal_discovery_outcome_uses_an_exact_v1_allowlist(self):
        for field in (
            "diagnosis",
            "diagnosed_findings",
            "recommendations",
            "prescriptions",
            "repair_plan",
            "advice",
        ):
            with self.subTest(field=field):
                receipt = self.load("bulwark-map-accepted.json")
                receipt["command"] = "doctor"
                discovery = self.doctor_discovery_action("choice-required")
                receipt["retrieval"]["actions"] = [discovery]
                self.bind_terminal_doctor_outcome(receipt, discovery)
                receipt["outcome"][field] = []
                receipt["presentation"].pop("tree")
                receipt["presentation"].pop("tree_features")
                receipt["presentation"].pop("health_meter")

                result = trajectory_gate.evaluate(receipt)

                self.assertEqual(result["status"], "FAIL")
                self.assertIn(
                    "outcome.invalid_terminal_doctor_diagnosis",
                    result["errors"],
                )

    def test_doctor_terminal_discovery_cannot_continue_or_claim_completion(self):
        late_actions = (
            {
                "owner": "docs",
                "kind": "combined-read",
                "paths": ["docs/README.md"],
                "status": "complete",
            },
            self.mapped_actions(False)[-1],
            {
                "owner": "docs",
                "kind": "post-check-read",
                "paths": ["docs/README.md"],
                "status": "complete",
            },
        )
        for late_action in late_actions:
            with self.subTest(kind=late_action["kind"]):
                receipt = self.load("bulwark-map-accepted.json")
                receipt["command"] = "doctor"
                receipt["retrieval"]["actions"] = [
                    self.doctor_discovery_action("choice-required"),
                    deepcopy(late_action),
                ]
                self.bind_terminal_doctor_outcome(
                    receipt,
                    receipt["retrieval"]["actions"][0],
                )
                receipt["presentation"].pop("tree")
                receipt["presentation"].pop("tree_features")

                result = trajectory_gate.evaluate(receipt)

                self.assertEqual(result["status"], "FAIL")
                self.assertIn("retrieval.doctor_discovery_must_stop", result["errors"])

        receipt = self.load("bulwark-map-accepted.json")
        receipt["command"] = "doctor"
        receipt["retrieval"]["actions"] = [
            self.doctor_discovery_action("no-candidates")
        ]
        self.bind_terminal_doctor_outcome(
            receipt,
            receipt["retrieval"]["actions"][0],
        )
        receipt["outcome"]["status"] = "complete"
        receipt["presentation"].pop("tree")
        receipt["presentation"].pop("tree_features")
        receipt["presentation"].pop("health_meter")

        completed = trajectory_gate.evaluate(receipt)

        self.assertEqual(completed["status"], "FAIL")
        self.assertIn("outcome.discovery_not_incomplete", completed["errors"])

    def test_doctor_terminal_discovery_binds_jurisdiction_and_user_action(self):
        cases = (
            (
                "scope-mismatch",
                lambda receipt: receipt["outcome"].update(scope="docs"),
                "retrieval.doctor_discovery_scope_mismatch",
            ),
            (
                "wrong-user-action",
                lambda receipt: receipt["retrieval"]["actions"][0].update(
                    user_action="continue"
                ),
                "retrieval.invalid_doctor_init_discovery",
            ),
        )
        for name, mutate, expected in cases:
            with self.subTest(name=name):
                receipt = self.load("bulwark-map-accepted.json")
                receipt["command"] = "doctor"
                receipt["retrieval"]["actions"] = [
                    self.doctor_discovery_action("choice-required")
                ]
                self.bind_terminal_doctor_outcome(
                    receipt,
                    receipt["retrieval"]["actions"][0],
                )
                receipt["presentation"].pop("tree")
                receipt["presentation"].pop("tree_features")
                receipt["presentation"].pop("health_meter")
                mutate(receipt)

                result = trajectory_gate.evaluate(receipt)

                self.assertEqual(result["status"], "FAIL")
                self.assertIn(expected, result["errors"])

    def test_doctor_discovery_metadata_must_preserve_task_five_semantics(self):
        cases = (
            ("wrong-mode", lambda action: action.update(mode="doctor")),
            ("wrong-schema", lambda action: action.update(schema_version=True)),
            ("content-read", lambda action: action.update(content_reads=1)),
            ("not-scope-limited", lambda action: action.update(scope_limited=False)),
            ("exhaustive", lambda action: action.update(repository_exhaustive=True)),
            ("selection-mismatch", lambda action: action.update(inspected_scope="wiki")),
            ("ready-truncated", lambda action: action.update(truncated=True)),
            (
                "ready-physical-limit",
                lambda action: action.update(physical_limit={"kind": "metadata_operations"}),
            ),
            ("ready-user-action", lambda action: action.update(requires_user_action=True)),
        )
        for name, mutate in cases:
            with self.subTest(name=name):
                receipt = self.load("bulwark-map-accepted.json")
                receipt["command"] = "doctor"
                discovery = self.doctor_discovery_action()
                mutate(discovery)
                checker = self.mapped_actions(False)[-1]
                checker["scope"] = "docs"
                receipt["outcome"]["scope"] = "docs"
                receipt["retrieval"]["actions"] = [discovery, checker]
                receipt["presentation"].pop("tree")
                receipt["presentation"].pop("tree_features")
                self.bind_doctor_findings(receipt)

                result = trajectory_gate.evaluate(receipt)

                self.assertEqual(result["status"], "FAIL")
                self.assertIn("retrieval.invalid_doctor_init_discovery", result["errors"])

    def test_doctor_discovery_requires_an_exact_boolean_user_action_flag(self):
        discovery = self.doctor_discovery_action()
        discovery["requires_user_action"] = 0

        result = self.evaluate_doctor_discovery(discovery)

        self.assertEqual(result["status"], "FAIL")
        self.assertIn("retrieval.invalid_doctor_init_discovery", result["errors"])

    def test_doctor_discovery_contract_consumes_canonical_task_five_policy(self):
        self.assertIs(trajectory_routes.INIT_DISCOVERY_LIMITS, INIT_DISCOVERY_LIMITS)
        self.assertIs(trajectory_routes.ANYWHERE_PRUNE_DIRS, ANYWHERE_PRUNE_DIRS)
        self.assertIs(
            trajectory_routes.REPOSITORY_ROOT_ONLY_PRUNE_DIRS,
            REPOSITORY_ROOT_ONLY_PRUNE_DIRS,
        )

        for status, physical in (
            ("ready", True),
            ("choice-required", True),
            ("no-candidates", True),
            ("stopped", True),
            ("stopped", False),
            ("batch-limited", True),
        ):
            with self.subTest(status=status, physical=physical):
                action = self.doctor_discovery_action(status, physical=physical)
                self.assertNotIn("root", action)
                self.assertEqual(action["limits"], INIT_DISCOVERY_LIMITS)
                self.assertEqual(action["prunes"]["anywhere_names"], list(ANYWHERE_PRUNE_DIRS))
                self.assertEqual(
                    action["prunes"]["repository_root_only_names"],
                    list(REPOSITORY_ROOT_ONLY_PRUNE_DIRS),
                )

    def test_doctor_discovery_prunes_require_exact_bidirectional_parity(self):
        def add_applied_and_exclusion(action):
            action["prunes"]["applied_paths"].append("docs/.venv")
            action["prunes"]["applied_paths"].sort(
                key=lambda path: (path.casefold(), path)
            )
            action["applied_exclusions"].append(
                {"path": "docs/.venv", "reason": "anywhere-prune"}
            )
            action["applied_exclusions"].sort(
                key=lambda item: (
                    (item["path"].casefold(), item["path"]),
                    item["reason"],
                )
            )

        def add_exclusion_only(action):
            action["applied_exclusions"].append(
                {"path": "docs/.venv", "reason": "anywhere-prune"}
            )
            action["applied_exclusions"].sort(
                key=lambda item: (
                    (item["path"].casefold(), item["path"]),
                    item["reason"],
                )
            )

        def substitute_applied_path(action):
            action["prunes"]["applied_paths"][0] = "docs/.venv"
            action["prunes"]["applied_paths"].sort(
                key=lambda path: (path.casefold(), path)
            )
            add_exclusion_only(action)

        cases = (
            ("erase-all-applied", lambda action: action["prunes"].update(applied_paths=[])),
            ("add-applied-and-exclusion", add_applied_and_exclusion),
            ("reverse-canonical-order", lambda action: action["prunes"]["applied_paths"].reverse()),
            (
                "duplicate-applied",
                lambda action: action["prunes"]["applied_paths"].append(
                    action["prunes"]["applied_paths"][-1]
                ),
            ),
            ("extra-prune-exclusion", add_exclusion_only),
            ("substitute-prune-path", substitute_applied_path),
            (
                "remove-prune-exclusion",
                lambda action: action["applied_exclusions"].pop(0),
            ),
            (
                "mismatched-prune-reason",
                lambda action: action["applied_exclusions"][0].update(
                    reason="repository-root-only-prune"
                ),
            ),
        )
        for name, mutate in cases:
            with self.subTest(name=name):
                discovery = {
                    "owner": "docs",
                    "kind": "init-discovery",
                    **deepcopy(actual_doctor_prune_payload()),
                }
                mutate(discovery)

                result = self.evaluate_doctor_discovery(discovery)

                self.assertEqual(result["status"], "FAIL")
                self.assertIn(
                    "retrieval.invalid_doctor_init_discovery",
                    result["errors"],
                )

    def test_doctor_discovery_preserves_real_nonprune_exclusions(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "alpha").mkdir()
            (root / "alpha" / "docs").write_text("not a directory", encoding="utf-8")
            payload = discover_init_scope(root)

        self.assertEqual(payload["status"], "no-candidates")
        self.assertIn(
            {"path": "alpha/docs", "reason": "not-directory"},
            payload["applied_exclusions"],
        )
        discovery = trajectory_discovery_contract.build_doctor_discovery_action(
            payload
        )

        result = self.evaluate_doctor_discovery(discovery)

        self.assertEqual(result["status"], "PASS", result["errors"])

    def test_doctor_candidate_limit_boundary_is_bound_to_serialized_evidence(self):
        def swap_first_candidates(action):
            action["candidates"][0], action["candidates"][1] = (
                action["candidates"][1],
                action["candidates"][0],
            )
            for rank, candidate in enumerate(action["candidates"], 1):
                candidate["rank"] = rank
            action["recommended_scope"] = action["candidates"][0]["path"]

        cases = (
            (
                "fabricated-path",
                lambda action: action["next_boundary"][0].update(path="fabricated"),
            ),
            (
                "fabricated-parent",
                lambda action: action["next_boundary"][0].update(path="p99/docs"),
            ),
            (
                "noncandidate-path",
                lambda action: action["next_boundary"][0].update(path="p21/not-docs"),
            ),
            (
                "noncanonical-doc-name",
                lambda action: action["next_boundary"][0].update(
                    path="p21/Documentation"
                ),
            ),
            (
                "unsafe-path",
                lambda action: action["next_boundary"][0].update(path="../docs"),
            ),
            (
                "integer-path",
                lambda action: action["next_boundary"][0].update(path=7),
            ),
            (
                "mapping-path",
                lambda action: action["next_boundary"][0].update(
                    path={"path": "p21/documentation"}
                ),
            ),
            (
                "pruned-path",
                lambda action: action["next_boundary"][0].update(
                    path="node_modules/docs"
                ),
            ),
            (
                "reported-path",
                lambda action: action["next_boundary"][0].update(path="p21/docs"),
            ),
            (
                "out-of-order-path",
                lambda action: action["next_boundary"][0].update(path="p00/wiki"),
            ),
            (
                "wrong-kind",
                lambda action: action["next_boundary"][0].update(kind="content-files"),
            ),
            (
                "wrong-count",
                lambda action: action["observed"].update(candidate_roots=64),
            ),
            ("candidate-order", swap_first_candidates),
        )
        for name, mutate in cases:
            with self.subTest(name=name):
                discovery = {
                    "owner": "docs",
                    "kind": "init-discovery",
                    **deepcopy(actual_doctor_candidate_limit_payload()),
                }
                mutate(discovery)

                errors = trajectory_routes.validate_route(
                    "doctor",
                    [discovery],
                    scope=".",
                )

                self.assertIn("retrieval.invalid_doctor_init_discovery", errors)

    def test_doctor_discovery_accepts_real_task_five_boundary_shapes(self):
        def depth_limit(docs):
            current = docs
            for index in range(INIT_DISCOVERY_LIMITS["selected_scope_depth"] + 2):
                current /= f"depth-{index:02d}"
                current.mkdir()

        def scandir_limit(docs):
            for index in range(100):
                parent = docs / f"directory-{index:03d}"
                parent.mkdir()
                (parent / "left").mkdir()
                (parent / "right").mkdir()

        def raw_entry_limit(docs):
            for directory_index in range(40):
                parent = docs / f"directory-{directory_index:03d}"
                parent.mkdir()
                for file_index in range(110):
                    (parent / f"entry-{file_index:03d}.txt").write_text(
                        "metadata only",
                        encoding="utf-8",
                    )

        for expected_kind, build in (
            ("selected_scope_depth", depth_limit),
            ("scandir_calls", scandir_limit),
            ("raw_directory_entries", raw_entry_limit),
        ):
            with self.subTest(kind=expected_kind), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                docs = root / "docs"
                docs.mkdir()
                build(docs)
                payload = discover_init_scope(root, "docs")
                self.assertEqual(payload["status"], "stopped")
                self.assertEqual(payload["physical_limit"]["kind"], expected_kind)
                discovery = trajectory_discovery_contract.build_doctor_discovery_action(
                    payload
                )

                receipt = self.load("bulwark-map-accepted.json")
                receipt["command"] = "doctor"
                receipt["retrieval"]["actions"] = [discovery]
                self.bind_terminal_doctor_outcome(receipt, discovery)
                receipt["presentation"].pop("tree")
                receipt["presentation"].pop("tree_features")
                receipt["presentation"].pop("health_meter")

                result = trajectory_gate.evaluate(receipt)

                self.assertEqual(result["status"], "PASS", result["errors"])

    def test_doctor_ready_discovery_requires_every_task_five_evidence_family(self):
        families = (
            "requested_scope",
            "normalized_scope",
            "jurisdiction_scope",
            "candidates",
            "recommended_scope",
            "selected_scope",
            "inspected_scope",
            "selection_reason",
            "limits",
            "observed",
            "scope_metadata",
            "content_batch",
            "physical_limit",
            "prunes",
            "applied_exclusions",
            "explicit_root_only_overrides",
            "truncated",
            "next_boundary",
            "requires_user_action",
            "user_action",
            "scope_limited",
            "repository_exhaustive",
            "content_reads",
        )
        for family in families:
            with self.subTest(family=family):
                receipt = self.load("bulwark-map-accepted.json")
                receipt["command"] = "doctor"
                discovery = self.doctor_discovery_action()
                discovery.pop(family)
                checker = self.mapped_actions(False)[-1]
                checker["scope"] = "docs"
                receipt["outcome"]["scope"] = "docs"
                receipt["retrieval"]["actions"] = [discovery, checker]
                receipt["presentation"].pop("tree")
                receipt["presentation"].pop("tree_features")
                self.bind_doctor_findings(receipt)

                result = trajectory_gate.evaluate(receipt)

                self.assertEqual(result["status"], "FAIL")
                self.assertIn("retrieval.invalid_doctor_init_discovery", result["errors"])

    def test_doctor_discovery_rejects_invented_wrong_or_inconsistent_physical_limits(self):
        cases = (
            ("invented-kind", lambda limit, action: limit.update(kind="root_entries")),
            ("wrong-limit", lambda limit, action: limit.update(limit=127)),
            ("wrong-observed", lambda limit, action: limit.update(observed=128)),
            (
                "wrong-lower-bound",
                lambda limit, action: limit.update(observed_is_lower_bound=False),
            ),
            ("fabricated-container", lambda limit, action: limit.update(container="elsewhere")),
            ("impossible-depth", lambda limit, action: limit.update(depth=17)),
            (
                "missing-physical-boundary",
                lambda limit, action: action.update(next_boundary=[]),
            ),
        )
        for name, mutate in cases:
            with self.subTest(name=name):
                receipt = self.load("bulwark-map-accepted.json")
                receipt["command"] = "doctor"
                discovery = self.doctor_discovery_action("stopped", physical=True)
                mutate(discovery["physical_limit"], discovery)
                receipt["retrieval"]["actions"] = [discovery]
                self.bind_terminal_doctor_outcome(receipt, discovery)
                receipt["presentation"].pop("tree")
                receipt["presentation"].pop("tree_features")
                receipt["presentation"].pop("health_meter")

                result = trajectory_gate.evaluate(receipt)

                self.assertEqual(result["status"], "FAIL")
                self.assertIn("retrieval.invalid_doctor_init_discovery", result["errors"])

    def test_doctor_discovery_rejects_fabricated_boundary_or_ready_truncation(self):
        cases = (
            (
                "fabricated-boundary",
                lambda action: action.update(
                    truncated=True,
                    next_boundary=[{"kind": "content-files", "path": "docs/not-planned.md"}],
                ),
            ),
            (
                "fabricated-continuation",
                lambda action: action["content_batch"].update(
                    complete=False,
                    truncated=True,
                    next_boundary="docs/not-planned.md",
                ),
            ),
            (
                "ready-incomplete-metadata",
                lambda action: action["scope_metadata"].update(complete=False),
            ),
        )
        for name, mutate in cases:
            with self.subTest(name=name):
                receipt = self.load("bulwark-map-accepted.json")
                receipt["command"] = "doctor"
                discovery = self.doctor_discovery_action()
                mutate(discovery)
                checker = self.mapped_actions(False)[-1]
                checker["scope"] = "docs"
                receipt["outcome"]["scope"] = "docs"
                receipt["retrieval"]["actions"] = [discovery, checker]
                receipt["presentation"].pop("tree")
                receipt["presentation"].pop("tree_features")
                self.bind_doctor_findings(receipt)

                result = trajectory_gate.evaluate(receipt)

                self.assertEqual(result["status"], "FAIL")
                self.assertIn("retrieval.invalid_doctor_init_discovery", result["errors"])

    def test_doctor_discovery_nested_schema_types_fail_closed(self):
        cases = (
            ("empty-explicit-candidates", lambda action: action.update(candidates=[])),
            (
                "non-string-applied-prune",
                lambda action: action["prunes"].update(applied_paths=[7]),
            ),
            (
                "non-integer-observed-counter",
                lambda action: action["observed"].update(candidate_roots=[]),
            ),
            (
                "non-string-container-limit-kind",
                lambda action: action["observed"]["containers"][0].update(
                    limit_kind={}
                ),
            ),
            (
                "non-string-exclusion-reason",
                lambda action: action["applied_exclusions"][0].update(reason={}),
            ),
        )
        for name, mutate in cases:
            with self.subTest(name=name):
                receipt = self.load("bulwark-map-accepted.json")
                receipt["command"] = "doctor"
                discovery = self.doctor_discovery_action()
                mutate(discovery)
                checker = self.mapped_actions(False)[-1]
                checker["scope"] = "docs"
                receipt["outcome"]["scope"] = "docs"
                receipt["retrieval"]["actions"] = [discovery, checker]
                receipt["presentation"].pop("tree")
                receipt["presentation"].pop("tree_features")
                self.bind_doctor_findings(receipt)

                result = trajectory_gate.evaluate(receipt)

                self.assertEqual(result["status"], "FAIL")
                self.assertIn("retrieval.invalid_doctor_init_discovery", result["errors"])

        terminal = self.load("bulwark-map-accepted.json")
        terminal["command"] = "doctor"
        discovery = self.doctor_discovery_action("choice-required")
        discovery["content_batch"].update(path_count=False, bytes=False)
        terminal["retrieval"]["actions"] = [discovery]
        self.bind_terminal_doctor_outcome(terminal, discovery)
        terminal["presentation"].pop("tree")
        terminal["presentation"].pop("tree_features")
        terminal["presentation"].pop("health_meter")

        result = trajectory_gate.evaluate(terminal)

        self.assertEqual(result["status"], "FAIL")
        self.assertIn("retrieval.invalid_doctor_init_discovery", result["errors"])

    def test_doctor_discovery_one_location_type_sweep_fails_closed(self):
        def mutation_locations(value, path=()):
            if path:
                yield path
            if type(value) is dict:
                for key, child in value.items():
                    yield from mutation_locations(child, path + (key,))
            elif type(value) is list:
                for index, child in enumerate(value):
                    yield from mutation_locations(child, path + (index,))

        def malformed_value(value):
            if type(value) is bool:
                return 0
            if type(value) is int:
                return False
            if type(value) is str:
                return 0
            if type(value) is dict:
                return []
            if type(value) is list:
                return {}
            if value is None:
                return 0
            raise AssertionError(f"unhandled serialized type: {type(value).__name__}")

        def replace(value, path, replacement):
            target = value
            for component in path[:-1]:
                target = target[component]
            target[path[-1]] = replacement

        failures = []
        cases = (
            ("ready", True),
            ("choice-required", True),
            ("no-candidates", True),
            ("stopped", True),
            ("stopped", False),
            ("logical-missing-container", None),
            ("batch-limited", True),
            ("candidate-limit", None),
        )
        mutation_count = 0
        for status, physical in cases:
            if status == "candidate-limit":
                base = {
                    "owner": "docs",
                    "kind": "init-discovery",
                    **deepcopy(actual_doctor_candidate_limit_payload()),
                }
            elif status == "logical-missing-container":
                base = {
                    "owner": "docs",
                    "kind": "init-discovery",
                    **deepcopy(actual_doctor_logical_boundary_payload()),
                }
            else:
                base = self.doctor_discovery_action(status, physical=physical)
            scope = base["selected_scope"] or base["jurisdiction_scope"]
            for path in mutation_locations(base):
                if path[0] in {"owner", "kind"}:
                    continue
                mutation_count += 1
                discovery = deepcopy(base)
                current = discovery
                for component in path:
                    current = current[component]
                replace(discovery, path, malformed_value(current))
                label = f"{status}:{'/'.join(map(str, path))}"
                try:
                    errors = trajectory_routes.validate_route(
                        "doctor",
                        [discovery],
                        scope=scope,
                    )
                except Exception as exc:  # malformed public data must not escape
                    failures.append((label, f"{type(exc).__name__}: {exc}"))
                else:
                    if "retrieval.invalid_doctor_init_discovery" not in errors:
                        failures.append((label, errors))

        self.assertGreater(mutation_count, 500)
        self.assertEqual(failures, [])

    def test_doctor_discovery_exact_json_subclass_sweep_fails_closed(self):
        class DictSubclass(dict):
            pass

        class ListSubclass(list):
            pass

        class StringSubclass(str):
            pass

        class IntSubclass(int):
            pass

        def value_locations(value, path=()):
            if type(value) in {dict, list, str, int}:
                yield path
            if type(value) is dict:
                for key, child in value.items():
                    yield from value_locations(child, path + (key,))
            elif type(value) is list:
                for index, child in enumerate(value):
                    yield from value_locations(child, path + (index,))

        def key_locations(value, path=()):
            if type(value) is dict:
                for index, (_key, child) in enumerate(value.items()):
                    yield path, index
                    yield from key_locations(child, path + (_key,))
            elif type(value) is list:
                for index, child in enumerate(value):
                    yield from key_locations(child, path + (index,))

        def target_at(value, path):
            target = value
            for component in path:
                target = target[component]
            return target

        def replace(value, path, replacement):
            if not path:
                return replacement
            target_at(value, path[:-1])[path[-1]] = replacement
            return value

        def subclass(value):
            if type(value) is dict:
                return DictSubclass(value)
            if type(value) is list:
                return ListSubclass(value)
            if type(value) is str:
                return StringSubclass(value)
            if type(value) is int:
                return IntSubclass(value)
            raise AssertionError(type(value).__name__)

        bases = (
            ("ready", self.doctor_discovery_action()),
            ("choice", self.doctor_discovery_action("choice-required")),
            ("no-candidates", self.doctor_discovery_action("no-candidates")),
            ("physical", self.doctor_discovery_action("stopped", physical=True)),
            ("logical", self.doctor_discovery_action("stopped", physical=False)),
            (
                "missing-container",
                deepcopy(actual_doctor_logical_boundary_payload()),
            ),
            ("batch", self.doctor_discovery_action("batch-limited")),
            ("candidate", deepcopy(actual_doctor_candidate_limit_payload())),
        )
        failures = []
        mutation_count = 0
        for shape, base in bases:
            for path in value_locations(base):
                mutation_count += 1
                discovery = deepcopy(base)
                current = target_at(discovery, path)
                discovery = replace(discovery, path, subclass(current))
                label = f"{shape}:value:{'/'.join(map(str, path)) or '<action>'}"
                try:
                    errors = []
                    trajectory_discovery_contract.validate_doctor_discovery_action(
                        discovery,
                        errors,
                    )
                except Exception as exc:
                    failures.append((label, f"{type(exc).__name__}: {exc}"))
                else:
                    if "retrieval.invalid_doctor_init_discovery" not in errors:
                        failures.append((label, errors))

            for path, key_index in key_locations(base):
                mutation_count += 1
                discovery = deepcopy(base)
                target = target_at(discovery, path)
                items = list(target.items())
                key, value = items[key_index]
                items[key_index] = (StringSubclass(key), value)
                discovery = replace(discovery, path, dict(items))
                label = f"{shape}:key:{'/'.join(map(str, path))}:{key}"
                try:
                    errors = []
                    trajectory_discovery_contract.validate_doctor_discovery_action(
                        discovery,
                        errors,
                    )
                except Exception as exc:
                    failures.append((label, f"{type(exc).__name__}: {exc}"))
                else:
                    if "retrieval.invalid_doctor_init_discovery" not in errors:
                        failures.append((label, errors))

        self.assertGreater(mutation_count, 500)
        self.assertEqual(failures, [])

    def test_doctor_discovery_builder_accepts_every_real_status_shape(self):
        actions = (
            self.doctor_discovery_action(),
            self.doctor_discovery_action("choice-required"),
            self.doctor_discovery_action("no-candidates"),
            self.doctor_discovery_action("stopped", physical=True),
            self.doctor_discovery_action("stopped", physical=False),
            deepcopy(actual_doctor_logical_boundary_payload()),
            self.doctor_discovery_action("batch-limited"),
            deepcopy(actual_doctor_candidate_limit_payload()),
        )
        observed = []
        for action in actions:
            with self.subTest(
                status=action["status"],
                boundary=action["next_boundary"],
            ):
                errors = []
                trajectory_discovery_contract.validate_doctor_discovery_action(
                    action,
                    errors,
                )
                self.assertEqual(errors, [])
                self.assertRegex(action["receipt_checksum"], r"^[0-9a-f]{64}$")
                observed.append((action["status"], action["next_boundary"]))

        self.assertEqual(len(observed), 8)

    def test_doctor_discovery_requires_exact_json_object_shapes(self):
        class DictSubclass(dict):
            pass

        for field in (
            "limits",
            "observed",
            "scope_metadata",
            "content_batch",
            "prunes",
        ):
            with self.subTest(field=field):
                discovery = self.doctor_discovery_action()
                discovery[field] = DictSubclass(discovery[field])

                errors = trajectory_routes.validate_route(
                    "doctor",
                    [discovery],
                    scope="docs",
                )

                self.assertIn("retrieval.invalid_doctor_init_discovery", errors)

    def test_doctor_discovery_programming_exceptions_still_propagate(self):
        import trajectory_discovery_contract

        discovery = self.doctor_discovery_action()
        with patch.object(
            trajectory_discovery_contract,
            "_prune_reason",
            side_effect=RuntimeError("synthetic programming defect"),
        ):
            with self.assertRaisesRegex(RuntimeError, "synthetic programming defect"):
                trajectory_routes.validate_route(
                    "doctor",
                    [discovery],
                    scope="docs",
                )

    def test_explicit_package_doctor_scope_confines_pre_and_postcheck_content(self):
        accepted = trajectory_gate.evaluate(self.scoped_doctor_receipt())
        self.assertEqual(accepted["status"], "PASS", accepted["errors"])

        cases = (
            ("pre-root", 1, "README.md"),
            ("pre-root-docs", 1, "docs/guide.md"),
            ("post-root", 3, "README.md"),
            ("post-root-docs", 3, "docs/current/fix.md"),
        )
        for name, action_index, path in cases:
            with self.subTest(name=name):
                receipt = self.scoped_doctor_receipt()
                receipt["retrieval"]["actions"][action_index]["paths"] = [path]

                result = trajectory_gate.evaluate(receipt)

                self.assertEqual(result["status"], "FAIL")
                self.assertIn("retrieval.path_outside_doctor_scope", result["errors"])

    def test_mapped_doctor_route_accepts_map_and_hot_evidence_inside_package_scope(self):
        scope = "packages/pkg/docs"
        receipt = self.load("bulwark-map-accepted.json")
        receipt["command"] = "doctor"
        checker = self.mapped_actions(False)[-1]
        checker["scope"] = scope
        receipt["outcome"].update(scope=scope, findings_exhaustive=True)
        receipt["retrieval"]["actions"] = [
            {
                "owner": "docs",
                "kind": "read-map",
                "paths": [f"{scope}/README.md"],
                "status": "complete",
            },
            {
                "owner": "docs",
                "kind": "read-map",
                "paths": [f"{scope}/guide.md"],
                "status": "complete",
            },
            checker,
        ]
        receipt["presentation"].pop("tree")
        receipt["presentation"].pop("tree_features")
        self.bind_doctor_findings(receipt)

        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "PASS", result["errors"])

    def test_mapped_doctor_accepts_a_non_readme_map_evidenced_inside_scope(self):
        scope = "packages/pkg/docs"
        receipt = self.load("bulwark-map-accepted.json")
        receipt["command"] = "doctor"
        checker = self.mapped_actions(False)[-1]
        checker["scope"] = scope
        receipt["outcome"].update(scope=scope, findings_exhaustive=True)
        receipt["retrieval"]["actions"] = [
            {
                "owner": "docs",
                "kind": "read-map",
                "paths": [f"{scope}/index.md"],
                "status": "complete",
            },
            checker,
        ]
        receipt["presentation"].pop("tree")
        receipt["presentation"].pop("tree_features")
        self.bind_doctor_findings(receipt)

        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "PASS", result["errors"])

    def test_discovery_mapped_doctor_binds_map_to_real_content_plan(self):
        scope = "packages/pkg/docs"
        receipt = self.load("bulwark-map-accepted.json")
        receipt["command"] = "doctor"
        discovery = self.doctor_discovery_action(
            scope=scope,
            map_name="index.md",
        )
        checker = self.mapped_actions(False)[-1]
        checker["scope"] = scope
        receipt["outcome"].update(scope=scope, findings_exhaustive=True)
        receipt["retrieval"]["actions"] = [
            discovery,
            {
                "owner": "docs",
                "kind": "read-map",
                "paths": [f"{scope}/index.md"],
                "status": "complete",
            },
            checker,
        ]
        receipt["presentation"].pop("tree")
        receipt["presentation"].pop("tree_features")
        self.bind_doctor_findings(receipt)

        accepted = trajectory_gate.evaluate(receipt)
        self.assertEqual(accepted["status"], "PASS", accepted["errors"])

        cases = (
            ("fabricated", f"{scope}/not-planned.md", "retrieval.invalid_map_read"),
            ("outside", "docs/index.md", "retrieval.path_outside_doctor_scope"),
            (
                "pruned",
                f"{scope}/node_modules/index.md",
                "retrieval.forbidden_path",
            ),
        )
        for name, path, expected in cases:
            with self.subTest(name=name):
                candidate = deepcopy(receipt)
                candidate["retrieval"]["actions"][1]["paths"] = [path]

                result = trajectory_gate.evaluate(candidate)

                self.assertEqual(result["status"], "FAIL")
                self.assertIn(expected, result["errors"])

        absent = deepcopy(receipt)
        absent["retrieval"]["actions"][1]["paths"] = []
        absent_result = trajectory_gate.evaluate(absent)
        self.assertEqual(absent_result["status"], "FAIL")
        self.assertIn("retrieval.invalid_map_read", absent_result["errors"])

    def test_doctor_binds_discovery_checker_outcome_and_route_scope(self):
        cases = (
            (
                "selected-scope",
                lambda receipt: receipt["retrieval"]["actions"][0].update(
                    selected_scope="packages/other/docs"
                ),
                "retrieval.doctor_discovery_scope_mismatch",
            ),
            (
                "inspected-scope",
                lambda receipt: receipt["retrieval"]["actions"][0].update(
                    inspected_scope="packages/other/docs"
                ),
                "retrieval.doctor_discovery_scope_mismatch",
            ),
            (
                "normalized-jurisdiction",
                lambda receipt: receipt["retrieval"]["actions"][0].update(
                    normalized_scope="packages/other/docs",
                    jurisdiction_scope="packages/other/docs",
                ),
                "retrieval.doctor_discovery_scope_mismatch",
            ),
            (
                "checker-scope",
                lambda receipt: receipt["retrieval"]["actions"][2].update(
                    scope="packages/other/docs"
                ),
                "retrieval.checker_scope_mismatch",
            ),
            (
                "outcome-scope",
                lambda receipt: receipt["outcome"].update(scope="packages/other/docs"),
                "retrieval.doctor_discovery_scope_mismatch",
            ),
            (
                "missing-outcome-scope",
                lambda receipt: receipt["outcome"].pop("scope"),
                "outcome.missing_findings_scope",
            ),
            (
                "missing-checker-scope",
                lambda receipt: receipt["retrieval"]["actions"][2].pop("scope"),
                "retrieval.missing_checker_scope",
            ),
        )
        for name, mutate, expected in cases:
            with self.subTest(name=name):
                receipt = self.scoped_doctor_receipt()
                mutate(receipt)

                result = trajectory_gate.evaluate(receipt)

                self.assertEqual(result["status"], "FAIL")
                self.assertIn(expected, result["errors"])

        normalized_once = self.scoped_doctor_receipt()
        normalized_once["outcome"]["scope"] = "packages/pkg/docs/"
        accepted = trajectory_gate.evaluate(normalized_once)
        self.assertEqual(accepted["status"], "PASS", accepted["errors"])

    def test_doctor_scoped_paths_preserve_safety_cold_and_root_rules(self):
        cases = (
            ("cold-archive", "packages/pkg/docs/archive/old.md"),
            ("cold-generated", "packages/pkg/docs/generated/api.md"),
        )
        for name, path in cases:
            with self.subTest(name=name):
                receipt = self.scoped_doctor_receipt()
                receipt["retrieval"]["actions"][1]["paths"] = [path]

                result = trajectory_gate.evaluate(receipt)

                self.assertEqual(result["status"], "FAIL")
                self.assertIn("retrieval.forbidden_path", result["errors"])

        traversal = self.scoped_doctor_receipt()
        traversal["retrieval"]["actions"][1]["paths"] = [
            "packages/pkg/docs/../secret.md"
        ]
        with self.assertRaisesRegex(ValueError, "private material"):
            trajectory_gate.evaluate(traversal)

        root = self.load("bulwark-map-accepted.json")
        root["command"] = "doctor"
        root["outcome"]["scope"] = "."
        root["retrieval"]["actions"] = self.doctor_actions(
            (("root-evidence", ("README.md", "docs/current/guide.md")),)
        )
        next(
            action
            for action in root["retrieval"]["actions"]
            if action["kind"] == "checker"
        )["scope"] = "."
        root["presentation"].pop("tree")
        root["presentation"].pop("tree_features")
        self.bind_doctor_findings(root)

        accepted_root = trajectory_gate.evaluate(root)

        self.assertEqual(accepted_root["status"], "PASS", accepted_root["errors"])

    def test_doctor_paths_reuse_task_five_anywhere_and_root_only_prunes(self):
        scope = "packages/pkg/docs"
        for pruned_name in ANYWHERE_PRUNE_DIRS:
            with self.subTest(anywhere=pruned_name):
                receipt = self.scoped_doctor_receipt(scope)
                receipt["retrieval"]["actions"][1]["paths"] = [
                    f"{scope}/nested/{pruned_name}/private.md"
                ]

                errors = trajectory_routes.validate_route(
                    "doctor",
                    receipt["retrieval"]["actions"],
                    scope=scope,
                )

                self.assertIn("retrieval.forbidden_path", errors)

        nested_root_only = self.scoped_doctor_receipt(scope)
        nested_root_only["retrieval"]["actions"][1]["paths"] = [
            f"{scope}/build/guide.md"
        ]
        accepted = trajectory_gate.evaluate(nested_root_only)

        self.assertEqual(accepted["status"], "PASS", accepted["errors"])

    def test_doctor_scoped_receipts_reject_absolute_and_file_uri_content_paths(self):
        for path in (r"C:\outside\guide.md", "file:///outside/guide.md"):
            with self.subTest(path=path):
                receipt = self.scoped_doctor_receipt()
                receipt["retrieval"]["actions"][1]["paths"] = [path]

                with self.assertRaisesRegex(ValueError, "private material"):
                    trajectory_gate.evaluate(receipt)

    def test_map_and_check_reject_doctor_postcheck_reads_after_checker(self):
        for command in ("map", "check"):
            with self.subTest(command=command):
                receipt = self.load("bulwark-map-accepted.json")
                receipt["command"] = command
                receipt["retrieval"]["actions"] = self.mapped_actions(False) + [
                    {
                        "owner": "docs",
                        "kind": "post-check-read",
                        "paths": ["STATE.md"],
                        "status": "complete",
                    }
                ]
                if command == "check":
                    receipt["presentation"].pop("tree")
                    receipt["presentation"].pop("tree_features")

                result = trajectory_gate.evaluate(receipt)

                self.assertEqual(result["status"], "FAIL")
                self.assertIn("retrieval.checker_not_final", result["errors"])

    def test_regression_receipt_reports_behavior_cost_and_hci_failures(self):
        result = trajectory_gate.evaluate(self.load("bulwark-map-regression.json"))

        self.assertEqual(result["status"], "FAIL")
        self.assertIn("presentation.raw_exit_code", result["errors"])
        self.assertIn("retrieval.docs_action_budget", result["errors"])
        self.assertIn("retrieval.repeated_checker", result["errors"])
        self.assertIn("external.failed_lookup", result["warnings"])
        self.assertNotIn("external.action_budget", result["errors"])

    def test_incomplete_outcomes_cannot_pass(self):
        for status in ("error", "partial", None):
            with self.subTest(status=status):
                receipt = self.load("bulwark-map-accepted.json")
                if status is None:
                    receipt["outcome"].pop("status")
                else:
                    receipt["outcome"]["status"] = status

                result = trajectory_gate.evaluate(receipt)

                self.assertEqual(result["status"], "FAIL")
                self.assertIn("outcome.incomplete", result["errors"])

    def test_common_exit_status_diagnostics_fail(self):
        for diagnostic in (
            "checker exit status 1",
            "non-zero exit status 1",
            "checker exit code 0",
        ):
            with self.subTest(diagnostic=diagnostic):
                receipt = self.load("bulwark-map-accepted.json")
                receipt["presentation"]["visible_diagnostics"] = [diagnostic]

                result = trajectory_gate.evaluate(receipt)

                self.assertEqual(result["status"], "FAIL")
                self.assertIn("presentation.raw_exit_code", result["errors"])

    def test_compact_returncode_diagnostics_fail(self):
        for diagnostic in ("returncode=1", "returncode 1"):
            with self.subTest(diagnostic=diagnostic):
                receipt = self.load("bulwark-map-accepted.json")
                receipt["presentation"]["visible_diagnostics"] = [diagnostic]

                result = trajectory_gate.evaluate(receipt)

                self.assertEqual(result["status"], "FAIL")
                self.assertIn("presentation.raw_exit_code", result["errors"])

    def test_missing_reader_questions_fail_without_exact_output_snapshot(self):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["outcome"]["answers"].remove("deliberately_unloaded")

        result = trajectory_gate.evaluate(receipt)

        self.assertIn("outcome.missing_answer:deliberately_unloaded", result["errors"])

    def test_map_requires_human_scannable_tree_features(self):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["presentation"]["tree_features"].remove("cold_collapsed")

        result = trajectory_gate.evaluate(receipt)

        self.assertIn("presentation.missing_tree_feature:cold_collapsed", result["errors"])

    def test_non_map_commands_do_not_require_documentation_tree(self):
        for command in ("context", "doctor"):
            with self.subTest(command=command):
                receipt = self.load("bulwark-map-accepted.json")
                receipt["command"] = command
                if command == "context":
                    receipt["retrieval"]["actions"] = [
                        {
                            "owner": "docs",
                            "kind": "combined-read",
                            "paths": ["README.md", "STATE.md", "PRODUCT.md", "DESIGN.md"],
                            "status": "complete",
                        }
                    ]
                else:
                    receipt["retrieval"]["actions"] = [
                        self.doctor_discovery_action(),
                        dict(self.mapped_actions(False)[-1], scope="docs"),
                    ]
                    receipt["outcome"]["scope"] = "docs"
                    self.bind_doctor_findings(receipt)
                receipt["presentation"].pop("tree")
                receipt["presentation"].pop("tree_features")

                result = trajectory_gate.evaluate(receipt)

                self.assertEqual(result["status"], "PASS")
                self.assertNotIn("presentation.missing_tree", result["errors"])

    def test_check_receipts_require_one_checker_run(self):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["command"] = "check"
        actions = self.missing_map_actions()
        receipt["retrieval"]["actions"] = actions
        receipt["presentation"].pop("tree")
        receipt["presentation"].pop("tree_features")

        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["metrics"]["checker_runs"], 1)

        receipt["retrieval"]["actions"] = actions[:3]
        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "FAIL")
        self.assertIn("retrieval.missing_checker", result["errors"])

    def test_check_rejects_repository_reads_after_checker(self):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["command"] = "check"
        actions = receipt["retrieval"]["actions"]
        late_read = dict(actions[0], paths=["STATE.md"], status="complete")
        receipt["retrieval"]["actions"] = [actions[3], late_read]

        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "FAIL")
        self.assertIn("retrieval.checker_not_final", result["errors"])

    def test_map_and_check_require_successful_checker_status(self):
        for command in ("map", "check"):
            with self.subTest(command=command):
                receipt = self.load("bulwark-map-accepted.json")
                receipt["command"] = command
                receipt["retrieval"]["actions"][3]["status"] = "error"
                if command == "check":
                    actions = receipt["retrieval"]["actions"]
                    receipt["retrieval"]["actions"] = [actions[0], actions[1], actions[3]]
                    receipt["presentation"].pop("tree")
                    receipt["presentation"].pop("tree_features")

                result = trajectory_gate.evaluate(receipt)

                self.assertEqual(result["status"], "FAIL")
                self.assertIn("retrieval.checker_failed", result["errors"])

    def test_check_receipts_do_not_require_map_reader_answers(self):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["command"] = "check"
        receipt["outcome"].pop("answers")
        receipt["retrieval"]["actions"] = self.missing_map_actions()
        receipt["presentation"].pop("tree")
        receipt["presentation"].pop("tree_features")

        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "PASS")
        self.assertFalse(any(error.startswith("outcome.missing_answer:") for error in result["errors"]))

    def test_map_receipts_require_one_checker_run(self):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["retrieval"]["actions"] = receipt["retrieval"]["actions"][:3]

        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "FAIL")
        self.assertIn("retrieval.missing_checker", result["errors"])

    def test_mapped_map_receipts_use_three_action_budget(self):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["retrieval"]["actions"][0]["status"] = "complete"

        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "FAIL")
        self.assertIn("retrieval.docs_action_budget", result["errors"])

    def test_map_rejects_broad_retrieval_actions_within_budget(self):
        for kind in ("repo-wide-search", "inventory", "name-only-inventory"):
            with self.subTest(kind=kind):
                receipt = self.load("bulwark-map-accepted.json")
                actions = receipt["retrieval"]["actions"]
                receipt["retrieval"]["actions"] = [actions[0], actions[1], actions[3]]
                receipt["retrieval"]["actions"][0]["status"] = "complete"
                receipt["retrieval"]["actions"][1]["kind"] = kind

                result = trajectory_gate.evaluate(receipt)

                self.assertEqual(result["status"], "FAIL")
                self.assertIn("retrieval.broad_action", result["errors"])

    def test_check_and_context_reject_broad_retrieval_actions(self):
        for command in ("check", "context"):
            with self.subTest(command=command):
                receipt = self.load("bulwark-map-accepted.json")
                receipt["command"] = command
                actions = receipt["retrieval"]["actions"]
                receipt["retrieval"]["actions"] = [actions[0], actions[1], actions[3]]
                receipt["retrieval"]["actions"][1]["kind"] = "repo-wide-search"

                result = trajectory_gate.evaluate(receipt)

                self.assertEqual(result["status"], "FAIL")
                self.assertIn("retrieval.broad_action", result["errors"])

    def test_check_rejects_reads_outside_map_routes(self):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["command"] = "check"
        actions = receipt["retrieval"]["actions"]
        source_read = dict(actions[0], paths=["src/main.py"], status="complete")
        receipt["retrieval"]["actions"] = [source_read, actions[3]]

        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "FAIL")
        self.assertIn("retrieval.forbidden_path", result["errors"])

    def test_context_counts_loaded_paths_across_read_actions(self):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["command"] = "context"
        receipt["retrieval"]["actions"] = [
            {
                "owner": "docs",
                "kind": "combined-read",
                "paths": ["docs/README.md", "STATE.md", "PRODUCT.md", "DESIGN.md", "PLAN.md"],
                "status": "complete",
            }
        ]

        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "FAIL")
        self.assertIn("retrieval.context_file_budget", result["errors"])

    def test_context_counts_bounded_probe_paths_toward_file_budget(self):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["command"] = "context"
        receipt["presentation"].pop("tree")
        receipt["presentation"].pop("tree_features")

        receipt["retrieval"]["actions"] = [
            {
                "owner": "docs",
                "kind": "bounded-probe",
                "paths": ["README.md", "STATE.md", "PRODUCT.md", "DESIGN.md"],
                "status": "complete",
            }
        ]
        self.assertEqual(trajectory_gate.evaluate(receipt)["status"], "PASS")

        receipt["retrieval"]["actions"][0]["paths"].append("PLAN.md")
        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "FAIL")
        self.assertIn("retrieval.context_file_budget", result["errors"])

    def test_context_aggregates_path_budget_across_read_actions(self):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["command"] = "context"
        receipt["retrieval"]["actions"] = [
            {
                "owner": "docs",
                "kind": "combined-read",
                "paths": ["README.md", "STATE.md"],
                "status": "complete",
            },
            {
                "owner": "docs",
                "kind": "bounded-probe",
                "paths": ["PRODUCT.md", "DESIGN.md", "PLAN.md"],
                "status": "complete",
            },
        ]
        receipt["presentation"].pop("tree")
        receipt["presentation"].pop("tree_features")

        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "FAIL")
        self.assertIn("retrieval.context_file_budget", result["errors"])

    def test_context_requires_repository_evidence(self):
        cases = (
            ("empty", []),
            (
                "checker-only",
                [{"owner": "docs", "kind": "checker", "count": 1, "status": "clean"}],
            ),
            (
                "external-only",
                [{"owner": "host", "kind": "tool-call", "status": "complete"}],
            ),
        )
        for name, actions in cases:
            with self.subTest(name=name):
                receipt = self.load("bulwark-map-accepted.json")
                receipt["command"] = "context"
                receipt["retrieval"]["actions"] = actions
                receipt["presentation"].pop("tree")
                receipt["presentation"].pop("tree_features")

                result = trajectory_gate.evaluate(receipt)

                self.assertEqual(result["status"], "FAIL")
                self.assertIn("retrieval.missing_context_evidence", result["errors"])

    def test_context_checker_is_optional_but_executes_at_most_once(self):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["command"] = "context"
        receipt["presentation"].pop("tree")
        receipt["presentation"].pop("tree_features")

        evidence = {
            "owner": "docs",
            "kind": "combined-read",
            "paths": ["README.md"],
            "status": "complete",
        }
        receipt["retrieval"]["actions"] = [
            evidence,
            {"owner": "docs", "kind": "checker", "count": 1, "status": "clean"}
        ]
        self.assertEqual(trajectory_gate.evaluate(receipt)["status"], "PASS")

        cases = (
            (
                "count-plus-one",
                [{"owner": "docs", "kind": "checker", "count": 2, "status": "clean"}],
                "retrieval.repeated_checker",
            ),
            (
                "duplicate-checker",
                [
                    {"owner": "docs", "kind": "checker", "count": 1, "status": "clean"},
                    {"owner": "docs", "kind": "checker", "count": 1, "status": "findings"},
                ],
                "retrieval.repeated_checker",
            ),
            (
                "zero-count-checker",
                [{"owner": "docs", "kind": "checker", "count": 0, "status": "clean"}],
                "retrieval.invalid_checker_count",
            ),
        )
        for name, actions, expected in cases:
            with self.subTest(name=name):
                receipt["retrieval"]["actions"] = [evidence, *actions]

                result = trajectory_gate.evaluate(receipt)

                self.assertEqual(result["status"], "FAIL")
                self.assertIn(expected, result["errors"])

    def test_context_retrieval_actions_require_nonempty_path_lists(self):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["command"] = "context"
        receipt["retrieval"]["actions"] = [
            {
                "owner": "docs",
                "kind": "combined-read",
                "status": "complete",
            }
        ]
        receipt["presentation"].pop("tree")
        receipt["presentation"].pop("tree_features")

        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "FAIL")
        self.assertIn("retrieval.invalid_action_paths", result["errors"])

    def test_context_rejects_an_errored_optional_checker(self):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["command"] = "context"
        receipt["retrieval"]["actions"] = [
            {
                "owner": "docs",
                "kind": "combined-read",
                "paths": ["README.md"],
                "status": "complete",
            },
            {
                "owner": "docs",
                "kind": "checker",
                "count": 1,
                "status": "error",
            }
        ]
        receipt["presentation"].pop("tree")
        receipt["presentation"].pop("tree_features")

        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "FAIL")
        self.assertIn("retrieval.checker_failed", result["errors"])

    def test_mapped_routes_reject_map_files_in_batched_hot_reads(self):
        for command in ("map", "check", "doctor"):
            with self.subTest(command=command):
                receipt = self.load("bulwark-map-accepted.json")
                receipt["command"] = command
                actions = self.mapped_actions(True)
                actions[1]["paths"] = ["docs/README.md", "STATE.md"]
                receipt["retrieval"]["actions"] = actions
                if command == "doctor":
                    self.bind_doctor_findings(receipt)
                if command != "map":
                    receipt["presentation"].pop("tree")
                    receipt["presentation"].pop("tree_features")

                result = trajectory_gate.evaluate(receipt)

                self.assertEqual(result["status"], "FAIL")
                self.assertIn("retrieval.duplicate_map_read", result["errors"])

    def test_mapped_routes_allow_nested_docs_hot_path_evidence(self):
        for command in ("map", "check", "doctor"):
            with self.subTest(command=command):
                receipt = self.load("bulwark-map-accepted.json")
                receipt["command"] = command
                actions = self.mapped_actions(True)
                actions[1]["paths"] = ["docs/current/STATE.md"]
                receipt["retrieval"]["actions"] = actions
                if command == "doctor":
                    self.bind_doctor_findings(receipt)
                if command != "map":
                    receipt["presentation"].pop("tree")
                    receipt["presentation"].pop("tree_features")

                result = trajectory_gate.evaluate(receipt)

                self.assertEqual(result["status"], "PASS")
                self.assertEqual(result["errors"], [])

    def test_doctor_postcheck_allows_nested_docs_evidence(self):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["command"] = "doctor"
        receipt["retrieval"]["actions"] = self.doctor_actions(
            (("finding-1", ("docs/current/STATE.md",)),)
        )
        self.bind_doctor_findings(receipt)
        receipt["presentation"].pop("tree")
        receipt["presentation"].pop("tree_features")

        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["errors"], [])

    def test_mapped_routes_reject_cold_nested_docs_evidence(self):
        for command in ("map", "check", "doctor"):
            with self.subTest(command=command):
                receipt = self.load("bulwark-map-accepted.json")
                receipt["command"] = command
                actions = self.mapped_actions(True)
                actions[1]["paths"] = ["docs/generated/api.md"]
                receipt["retrieval"]["actions"] = actions
                if command != "map":
                    receipt["presentation"].pop("tree")
                    receipt["presentation"].pop("tree_features")

                result = trajectory_gate.evaluate(receipt)

                self.assertEqual(result["status"], "FAIL")
                self.assertIn("retrieval.forbidden_path", result["errors"])

    def test_doctor_postcheck_rejects_cold_nested_docs_evidence(self):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["command"] = "doctor"
        receipt["retrieval"]["actions"] = self.doctor_actions(
            (("finding-1", ("docs/archive/old.md",)),)
        )
        receipt["presentation"].pop("tree")
        receipt["presentation"].pop("tree_features")

        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "FAIL")
        self.assertIn("retrieval.forbidden_path", result["errors"])

    def test_checker_actions_cannot_carry_repository_paths(self):
        for command in ("map", "check", "doctor"):
            with self.subTest(command=command):
                receipt = self.load("bulwark-map-accepted.json")
                receipt["command"] = command
                actions = self.mapped_actions(True)
                actions[-1]["paths"] = ["README.md"]
                receipt["retrieval"]["actions"] = actions
                if command != "map":
                    receipt["presentation"].pop("tree")
                    receipt["presentation"].pop("tree_features")

                result = trajectory_gate.evaluate(receipt)

                self.assertEqual(result["status"], "FAIL")
                self.assertIn("retrieval.invalid_action_paths", result["errors"])

    def test_route_actions_reject_canonical_duplicate_paths(self):
        mapped = self.load("bulwark-map-accepted.json")
        mapped["retrieval"]["actions"] = self.mapped_actions(True)
        mapped["retrieval"]["actions"][1]["paths"] = [
            "docs/current/STATE.md",
            r"docs\current\STATE.md",
        ]

        missing = self.load("bulwark-map-accepted.json")
        missing["retrieval"]["actions"][2]["paths"] = ["README.md", r"README.md"]

        for name, receipt in (("mapped", mapped), ("missing", missing)):
            with self.subTest(name=name):
                result = trajectory_gate.evaluate(receipt)

                self.assertEqual(result["status"], "FAIL")
                self.assertIn("retrieval.invalid_action_paths", result["errors"])

    def test_map_receipts_require_read_map_action(self):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["retrieval"]["actions"] = [receipt["retrieval"]["actions"][3]]

        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "FAIL")
        self.assertIn("retrieval.missing_map_read", result["errors"])

    def test_map_requires_read_map_as_first_docs_action(self):
        receipt = self.load("bulwark-map-accepted.json")
        actions = receipt["retrieval"]["actions"]
        receipt["retrieval"]["actions"] = [actions[1], actions[0], actions[2], actions[3]]

        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "FAIL")
        self.assertIn("retrieval.map_read_not_first", result["errors"])

    def test_map_read_targets_docs_readme_with_valid_status(self):
        mutations = (
            ("wrong-path", lambda action: action.update(paths=["README.md"])),
            ("invalid-status", lambda action: action.update(status="error")),
        )
        for name, mutate in mutations:
            with self.subTest(name=name):
                receipt = self.load("bulwark-map-accepted.json")
                actions = receipt["retrieval"]["actions"]
                mutate(actions[0])
                receipt["retrieval"]["actions"] = [actions[0], actions[1], actions[3]]

                result = trajectory_gate.evaluate(receipt)

                self.assertEqual(result["status"], "FAIL")
                self.assertIn("retrieval.invalid_map_read", result["errors"])

    def test_map_requires_checker_as_final_docs_action(self):
        receipt = self.load("bulwark-map-accepted.json")
        actions = receipt["retrieval"]["actions"]
        receipt["retrieval"]["actions"] = [actions[0], actions[3], actions[1], actions[2]]

        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "FAIL")
        self.assertIn("retrieval.checker_not_final", result["errors"])

    def test_missing_map_fallback_rejects_forbidden_paths(self):
        receipt = self.load("bulwark-map-accepted.json")
        forbidden = ["src/main.py", "tests/test_app.py", "docs/generated/api.md"]
        receipt["retrieval"]["actions"][1]["paths"] = forbidden
        receipt["retrieval"]["actions"][2]["paths"] = forbidden

        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "FAIL")
        self.assertIn("retrieval.forbidden_path", result["errors"])

    def test_missing_map_fallback_rejects_nested_docs_candidates(self):
        for command in ("map", "check", "doctor"):
            with self.subTest(command=command):
                receipt = self.load("bulwark-map-accepted.json")
                receipt["command"] = command
                nested_candidate = ["docs/current/STATE.md"]
                receipt["retrieval"]["actions"][1]["paths"] = nested_candidate
                receipt["retrieval"]["actions"][2]["paths"] = nested_candidate
                if command != "map":
                    receipt["presentation"].pop("tree")
                    receipt["presentation"].pop("tree_features")

                result = trajectory_gate.evaluate(receipt)

                self.assertEqual(result["status"], "FAIL")
                self.assertIn(
                    "retrieval.doctor_init_discovery_required"
                    if command == "doctor"
                    else "retrieval.forbidden_path",
                    result["errors"],
                )

    def test_missing_map_combined_read_must_use_probe_evidence(self):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["retrieval"]["actions"][1]["paths"] = ["README.md"]
        receipt["retrieval"]["actions"][2]["paths"] = ["docs/hidden.md"]

        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "FAIL")
        self.assertIn("retrieval.invalid_map_route", result["errors"])

    def test_missing_map_selected_map_may_be_any_probed_candidate(self):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["retrieval"]["actions"][1]["paths"] = ["README.md", "PRODUCT.md"]
        receipt["retrieval"]["actions"][2]["paths"] = ["PRODUCT.md", "README.md"]

        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "PASS", result["errors"])

    def test_missing_map_fallback_rejects_confirmed_missing_map_reread(self):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["retrieval"]["actions"][1]["paths"].append("docs/README.md")

        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "FAIL")
        self.assertIn("retrieval.invalid_map_route", result["errors"])

    def test_map_enforces_status_specific_action_order(self):
        cases = (
            ("mapped-fallback-actions", "complete", (1, 3)),
            ("missing-read-after-combined-read", "missing", (2, 1, 3)),
        )
        for name, status, action_indexes in cases:
            with self.subTest(name=name):
                receipt = self.load("bulwark-map-accepted.json")
                actions = receipt["retrieval"]["actions"]
                actions[0]["status"] = status
                receipt["retrieval"]["actions"] = [actions[0], *(actions[index] for index in action_indexes)]

                result = trajectory_gate.evaluate(receipt)

                self.assertEqual(result["status"], "FAIL")
                self.assertIn("retrieval.invalid_map_route", result["errors"])

    def test_mapped_route_rejects_source_hot_path_reads(self):
        receipt = self.load("bulwark-map-accepted.json")
        actions = receipt["retrieval"]["actions"]
        extra_read = dict(actions[0], status="complete", paths=["src/main.py"])
        receipt["retrieval"]["actions"] = [dict(actions[0], status="complete"), extra_read, actions[3]]

        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "FAIL")
        self.assertIn("retrieval.forbidden_path", result["errors"])

    def test_mapped_route_rejects_duplicate_map_rereads(self):
        receipt = self.load("bulwark-map-accepted.json")
        actions = receipt["retrieval"]["actions"]
        first_read = dict(actions[0], status="complete")
        duplicate_read = dict(actions[0], status="complete")
        receipt["retrieval"]["actions"] = [first_read, duplicate_read, actions[3]]

        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "FAIL")
        self.assertIn("retrieval.duplicate_map_read", result["errors"])

    def test_mapped_route_requires_completed_hot_path_reads(self):
        for status in ("missing", "error"):
            with self.subTest(status=status):
                receipt = self.load("bulwark-map-accepted.json")
                actions = receipt["retrieval"]["actions"]
                first_read = dict(actions[0], status="complete")
                hot_path_read = dict(actions[0], paths=["STATE.md"], status=status)
                receipt["retrieval"]["actions"] = [first_read, hot_path_read, actions[3]]

                result = trajectory_gate.evaluate(receipt)

                self.assertEqual(result["status"], "FAIL")
                self.assertIn("retrieval.mapped_read_failed", result["errors"])

    def test_missing_map_requires_combined_read_before_checker(self):
        receipt = self.load("bulwark-map-accepted.json")
        actions = receipt["retrieval"]["actions"]
        receipt["retrieval"]["actions"] = [actions[0], actions[1], actions[3]]

        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "FAIL")
        self.assertIn("retrieval.missing_combined_read", result["errors"])

    def test_missing_map_fallback_requires_completed_retrieval_actions(self):
        for kind, status in (("bounded-probe", "error"), ("combined-read", "missing")):
            with self.subTest(kind=kind, status=status):
                receipt = self.load("bulwark-map-accepted.json")
                for action in receipt["retrieval"]["actions"]:
                    if action["kind"] == kind:
                        action["status"] = status

                result = trajectory_gate.evaluate(receipt)

                self.assertEqual(result["status"], "FAIL")
                self.assertIn("retrieval.fallback_action_failed", result["errors"])

    def test_missing_map_fallback_rejects_empty_path_lists(self):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["retrieval"]["actions"][1]["paths"] = []
        receipt["retrieval"]["actions"][2]["paths"] = []

        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "FAIL")
        self.assertIn("retrieval.empty_fallback_paths", result["errors"])

    def test_mapped_budget_uses_first_read_map_status(self):
        receipt = self.load("bulwark-map-accepted.json")
        actions = receipt["retrieval"]["actions"]
        first_read = dict(actions[0], status="complete")
        later_read = dict(actions[0], status="missing")
        receipt["retrieval"]["actions"] = [first_read, actions[1], actions[3], later_read]

        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "FAIL")
        self.assertIn("retrieval.docs_action_budget", result["errors"])

    def test_bounded_commands_reject_checker_preflight_actions(self):
        for command in ("map", "check", "context"):
            for kind in ("preflight", "availability-probe"):
                with self.subTest(command=command, kind=kind):
                    receipt = self.load("bulwark-map-accepted.json")
                    receipt["command"] = command
                    actions = receipt["retrieval"]["actions"]
                    receipt["retrieval"]["actions"] = [actions[0], actions[1], actions[3]]
                    receipt["retrieval"]["actions"][1]["kind"] = kind

                    result = trajectory_gate.evaluate(receipt)

                    self.assertEqual(result["status"], "FAIL")
                    self.assertIn("retrieval.preflight_action", result["errors"])

    def test_combined_read_paths_are_bounded(self):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["retrieval"]["actions"][2]["paths"] = ["README.md"] * 1_000

        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "FAIL")
        self.assertIn("retrieval.action_path_budget", result["errors"])

    def test_doctor_rejects_broad_and_preflight_retrieval(self):
        for kind, error in (
            ("repo-wide-search", "retrieval.broad_action"),
            ("preflight", "retrieval.preflight_action"),
        ):
            with self.subTest(kind=kind):
                receipt = self.load("bulwark-map-accepted.json")
                receipt["command"] = "doctor"
                actions = receipt["retrieval"]["actions"]
                receipt["retrieval"]["actions"] = [actions[0], actions[1], actions[3]]
                receipt["retrieval"]["actions"][1]["kind"] = kind

                result = trajectory_gate.evaluate(receipt)

                self.assertEqual(result["status"], "FAIL")
                self.assertIn(error, result["errors"])

    def test_doctor_requires_one_successful_checker_run(self):
        cases = (
            ("missing", lambda actions: [actions[0], actions[1]], "retrieval.missing_checker"),
            ("failed", lambda actions: [actions[0], actions[1], dict(actions[3], status="error")], "retrieval.checker_failed"),
        )
        for name, select_actions, error in cases:
            with self.subTest(name=name):
                receipt = self.load("bulwark-map-accepted.json")
                receipt["command"] = "doctor"
                actions = receipt["retrieval"]["actions"]
                receipt["retrieval"]["actions"] = select_actions(actions)

                result = trajectory_gate.evaluate(receipt)

                self.assertEqual(result["status"], "FAIL")
                self.assertIn(error, result["errors"])

    def test_doctor_requires_map_read_before_checker(self):
        cases = (
            ("checker-only", lambda actions: [actions[3]], "retrieval.missing_map_read"),
            ("late-map-read", lambda actions: [actions[1], actions[0], actions[3]], "retrieval.map_read_not_first"),
        )
        for name, select_actions, error in cases:
            with self.subTest(name=name):
                receipt = self.load("bulwark-map-accepted.json")
                receipt["command"] = "doctor"
                actions = receipt["retrieval"]["actions"]
                receipt["retrieval"]["actions"] = select_actions(actions)

                result = trajectory_gate.evaluate(receipt)

                self.assertEqual(result["status"], "FAIL")
                self.assertIn(error, result["errors"])

    def test_doctor_mapped_route_caps_reads_before_checker(self):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["command"] = "doctor"
        actions = receipt["retrieval"]["actions"]
        first_read = dict(actions[0], status="complete")
        state_read = dict(actions[0], paths=["STATE.md"], status="complete")
        product_read = dict(actions[0], paths=["PRODUCT.md"], status="complete")
        receipt["retrieval"]["actions"] = [first_read, state_read, product_read, actions[3]]

        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "FAIL")
        self.assertIn("retrieval.doctor_precheck_budget", result["errors"])

    def test_doctor_rejects_unknown_action_kinds_before_checker(self):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["command"] = "doctor"
        actions = receipt["retrieval"]["actions"]
        first_read = dict(actions[0], status="complete")
        source_read = dict(actions[0], kind="source-read", paths=["src/main.py"], status="complete")
        receipt["retrieval"]["actions"] = [first_read, source_read, actions[3]]

        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "FAIL")
        self.assertIn("retrieval.unknown_action_kind:source-read", result["errors"])

    def test_doctor_caps_total_postcheck_opened_files(self):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["command"] = "doctor"
        actions = receipt["retrieval"]["actions"]
        first_read = dict(actions[0], status="complete")
        postcheck_read = {
            "owner": "docs",
            "kind": "post-check-read",
            "paths": ["README.md", "STATE.md", "PRODUCT.md", "DESIGN.md", "PLAN.md"],
            "status": "complete",
        }
        receipt["retrieval"]["actions"] = [first_read, actions[3], postcheck_read]

        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "FAIL")
        self.assertIn("retrieval.doctor_postcheck_file_budget", result["errors"])

    def test_doctor_missing_map_requires_discovery_before_checker(self):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["command"] = "doctor"
        actions = receipt["retrieval"]["actions"]
        receipt["retrieval"]["actions"] = [actions[0], actions[3]]

        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "FAIL")
        self.assertIn("retrieval.doctor_init_discovery_required", result["errors"])

    def test_doctor_missing_map_fallback_precedes_checker(self):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["command"] = "doctor"
        actions = receipt["retrieval"]["actions"]
        receipt["retrieval"]["actions"] = [actions[0], actions[1], actions[3], actions[2]]

        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "FAIL")
        self.assertIn("retrieval.invalid_map_route", result["errors"])

    def test_doctor_discovery_content_rejects_forbidden_paths(self):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["command"] = "doctor"
        forbidden = [
            "src/main.py",
            "docs/tests/test_app.md",
            "docs/generated/api.md",
        ]
        checker = self.mapped_actions(False)[-1]
        checker["scope"] = "docs"
        receipt["outcome"]["scope"] = "docs"
        receipt["retrieval"]["actions"] = [
            self.doctor_discovery_action(scope="docs"),
            {
                "owner": "docs",
                "kind": "combined-read",
                "paths": forbidden,
                "status": "complete",
            },
            checker,
        ]
        receipt["presentation"].pop("tree")
        receipt["presentation"].pop("tree_features")
        self.bind_doctor_findings(receipt)

        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "FAIL")
        self.assertIn("retrieval.forbidden_path", result["errors"])

    def test_doctor_discovery_combined_read_paths_are_bounded(self):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["command"] = "doctor"
        checker = self.mapped_actions(False)[-1]
        checker["scope"] = "docs"
        receipt["outcome"]["scope"] = "docs"
        receipt["retrieval"]["actions"] = [
            self.doctor_discovery_action(scope="docs"),
            {
                "owner": "docs",
                "kind": "combined-read",
                "paths": [
                    "docs/README.md",
                    "docs/guide.md",
                    "docs/current/fix.md",
                    "docs/build/guide.md",
                ],
                "status": "complete",
            },
            checker,
        ]
        receipt["presentation"].pop("tree")
        receipt["presentation"].pop("tree_features")
        self.bind_doctor_findings(receipt)

        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "FAIL")
        self.assertIn("retrieval.action_path_budget", result["errors"])

    def test_map_rejects_unknown_action_kinds(self):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["retrieval"]["actions"][2]["kind"] = "bulk-read"
        receipt["retrieval"]["actions"][2]["paths"] = ["README.md"] * 1_000

        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "FAIL")
        self.assertIn("retrieval.unknown_action_kind:bulk-read", result["errors"])

    def test_host_growth_is_only_attributed_with_a_paired_control(self):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["usage"]["paired_control"] = {
            "responses": 1,
            "cumulative_input_tokens": 30_000,
            "cached_input_tokens": 27_000,
        }
        receipt["usage"]["cumulative_input_tokens"] = 200_000
        receipt["usage"]["responses"] = 4

        result = trajectory_gate.evaluate(receipt)

        self.assertNotIn("usage.unpaired_host_baseline", result["warnings"])
        self.assertEqual(result["metrics"]["input_per_response"], 50_000)
        self.assertEqual(result["metrics"]["paired_host_input_per_response"], 30_000)
        self.assertEqual(result["metrics"]["input_per_response_delta"], 20_000)

    def test_public_receipts_reject_sensitive_or_hidden_material_recursively(self):
        bad_values = [
            ("absolute path", {"note": r"C:\Users\person\repo"}),
            ("rooted Windows user path", {"note": r"\Users\person\repo"}),
            ("rooted Windows workspace path", {"note": r"\workspace\Skills"}),
            ("UNC path", {"note": r"\\server\share\repo"}),
            ("POSIX workspace path", {"note": "/workspace/Skills"}),
            ("POSIX temporary path", {"note": "/tmp/private"}),
            ("POSIX var path", {"note": "/var/lib/private"}),
            ("colon-prefixed POSIX path", {"note": "root:/workspace/Skills"}),
            ("file URI path", {"note": "file:///workspace/Skills"}),
            ("secret", {"metadata": {"api_token": "opaque"}}),
            ("hidden reasoning", {"hidden_reasoning": "private"}),
            ("raw session id", {"session_id": "synthetic-private-id"}),
        ]
        for label, addition in bad_values:
            with self.subTest(label=label):
                receipt = self.load("bulwark-map-accepted.json")
                receipt.update(addition)
                with self.assertRaisesRegex(ValueError, "public trajectory receipt"):
                    trajectory_gate.evaluate(receipt)

    def test_public_receipts_reject_file_uris_with_authorities(self):
        for uri in ("file://localhost/workspace/Skills", "file://server/share/repo"):
            with self.subTest(uri=uri):
                receipt = self.load("bulwark-map-accepted.json")
                receipt["note"] = uri

                with self.assertRaisesRegex(ValueError, "public trajectory receipt"):
                    trajectory_gate.evaluate(receipt)

    def test_public_receipts_reject_absolute_path_keys(self):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["diagnostics"] = {"/workspace/Skills/docs/README.md": "unresolved"}

        with self.assertRaisesRegex(ValueError, "public trajectory receipt"):
            trajectory_gate.evaluate(receipt)

    def test_public_receipts_reject_private_markers_in_values(self):
        for value in (
            "chain_of_thought: private",
            "reasoning_content: private",
            "session_id synthetic-private-id",
            "-----BEGIN PRIVATE KEY-----",
            "-----BEGIN PGP PRIVATE KEY BLOCK-----",
        ):
            with self.subTest(value=value):
                receipt = self.load("bulwark-map-accepted.json")
                receipt["note"] = value

                with self.assertRaisesRegex(ValueError, "public trajectory receipt"):
                    trajectory_gate.evaluate(receipt)

    def test_public_receipts_reject_all_github_token_prefixes(self):
        for prefix in ("ghp_", "github_pat_", "gho_", "ghu_", "ghs_", "ghr_"):
            with self.subTest(prefix=prefix):
                receipt = self.load("bulwark-map-accepted.json")
                receipt["note"] = prefix + "synthetic_token_value"

                with self.assertRaisesRegex(ValueError, "public trajectory receipt"):
                    trajectory_gate.evaluate(receipt)

    def test_public_receipts_allow_private_marker_substrings(self):
        for value in ("obsession_id is a public field", "reasoning_contents are summarized"):
            with self.subTest(value=value):
                receipt = self.load("bulwark-map-accepted.json")
                receipt["note"] = value

                result = trajectory_gate.evaluate(receipt)

                self.assertEqual(result["status"], "PASS")

    def test_public_receipts_allow_urls_and_prose_slashes(self):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["presentation"]["visible_diagnostics"] = [
            "See https://docs.example.test/map",
            "links / anchors checked",
            r"relative docs\README.md remains public",
        ]

        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "PASS")

    def test_receipt_rejects_malformed_retrieval_actions(self):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["retrieval"]["actions"].append("not-an-action")

        with self.assertRaisesRegex(ValueError, "retrieval.actions entries"):
            trajectory_gate.evaluate(receipt)

    def test_receipt_rejects_malformed_action_metadata(self):
        for field in ("owner", "kind", "status"):
            for value in (None, "", [], {}):
                with self.subTest(field=field, value=repr(value)):
                    receipt = self.load("bulwark-map-accepted.json")
                    receipt["retrieval"]["actions"][0][field] = value

                    with self.assertRaisesRegex(
                        ValueError, f"action.{field} must be a non-empty string"
                    ):
                        trajectory_gate.evaluate(receipt)

    def test_public_receipt_requires_exact_builtin_json_types_recursively(self):
        class DictSubclass(dict):
            pass

        class ListSubclass(list):
            pass

        class StringSubclass(str):
            pass

        class IntSubclass(int):
            pass

        def subclass_key(receipt):
            value = receipt.pop("run_id")
            rebuilt = {
                (StringSubclass(key) if key == "visibility" else key): item
                for key, item in receipt.items()
            }
            rebuilt["run_id"] = value
            receipt.clear()
            receipt.update(rebuilt)

        cases = (
            ("outer-receipt", lambda receipt: DictSubclass(receipt)),
            (
                "nested-object",
                lambda receipt: receipt.update(
                    retrieval=DictSubclass(receipt["retrieval"])
                )
                or receipt,
            ),
            (
                "actions-list",
                lambda receipt: receipt["retrieval"].update(
                    actions=ListSubclass(receipt["retrieval"]["actions"])
                )
                or receipt,
            ),
            (
                "action-object",
                lambda receipt: receipt["retrieval"]["actions"].__setitem__(
                    0,
                    DictSubclass(receipt["retrieval"]["actions"][0]),
                )
                or receipt,
            ),
            (
                "action-owner-string",
                lambda receipt: receipt["retrieval"]["actions"][0].update(
                    owner=StringSubclass("docs")
                )
                or receipt,
            ),
            (
                "paths-list",
                lambda receipt: receipt["retrieval"]["actions"][0].update(
                    paths=ListSubclass(receipt["retrieval"]["actions"][0]["paths"])
                )
                or receipt,
            ),
            (
                "path-string",
                lambda receipt: receipt["retrieval"]["actions"][0]["paths"].__setitem__(
                    0,
                    StringSubclass(receipt["retrieval"]["actions"][0]["paths"][0]),
                )
                or receipt,
            ),
            (
                "integer-subclass",
                lambda receipt: receipt["usage"].update(
                    responses=IntSubclass(receipt["usage"]["responses"])
                )
                or receipt,
            ),
            ("object-key-string", lambda receipt: subclass_key(receipt) or receipt),
        )
        for name, mutate in cases:
            with self.subTest(name=name):
                receipt = self.load("bulwark-map-accepted.json")
                mutated = mutate(receipt)

                with self.assertRaisesRegex(ValueError, "exact JSON"):
                    trajectory_gate.evaluate(mutated)

    def test_receipt_rejects_malformed_used_scalar_fields(self):
        cases = (
            (
                "outcome.files_changed",
                lambda receipt: receipt["outcome"].update(files_changed=False),
                "outcome.files_changed must be a non-negative integer",
            ),
            (
                "presentation.raw_exit_code_visible",
                lambda receipt: receipt["presentation"].update(
                    raw_exit_code_visible=1
                ),
                "presentation.raw_exit_code_visible must be a boolean",
            ),
        )
        for name, mutate, expected in cases:
            with self.subTest(name=name):
                receipt = self.load("bulwark-map-accepted.json")
                mutate(receipt)

                with self.assertRaisesRegex(ValueError, expected):
                    trajectory_gate.evaluate(receipt)

    def test_malformed_receipt_arrays_raise_value_error(self):
        mutations = (
            ("outcome.answers", lambda receipt: receipt["outcome"].update(answers=None)),
            ("presentation.tree_features", lambda receipt: receipt["presentation"].update(tree_features=None)),
            ("presentation.visible_diagnostics", lambda receipt: receipt["presentation"].update(visible_diagnostics=None)),
        )
        for name, mutate in mutations:
            with self.subTest(name=name):
                receipt = self.load("bulwark-map-accepted.json")
                mutate(receipt)

                with self.assertRaisesRegex(ValueError, "must be an array"):
                    trajectory_gate.evaluate(receipt)

    def test_cli_emits_json_and_uses_exit_codes_zero_one_two(self):
        cases = [
            ("bulwark-map-accepted.json", 0, "PASS"),
            ("bulwark-map-regression.json", 1, "FAIL"),
        ]
        for filename, code, status in cases:
            with self.subTest(filename=filename):
                result = subprocess.run(
                    [sys.executable, str(ROOT / "tools" / "trajectory_gate.py"), str(ROOT / "evals" / "trajectory" / filename)],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertEqual(json.loads(result.stdout)["status"], status)

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as handle:
            handle.write("not json")
            malformed = Path(handle.name)
        try:
            result = subprocess.run(
                [sys.executable, str(ROOT / "tools" / "trajectory_gate.py"), str(malformed)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 2)
        finally:
            malformed.unlink(missing_ok=True)

    def test_fable5_real_sparse_candidate_boundaries_are_accepted(self):
        cases = (
            (
                "next-sibling-docs",
                actual_doctor_sibling_docs_limit_payload(),
                "p63/docs",
                "p64/docs",
            ),
            (
                "sparse-wiki-tail",
                actual_doctor_wiki_tail_limit_payload(),
                "p21/docs",
                "p21/wiki",
            ),
        )
        for name, captured, final_candidate, boundary in cases:
            with self.subTest(name=name):
                action = deepcopy(captured)
                self.assertEqual(action["candidates"][-1]["path"], final_candidate)
                self.assertEqual(
                    action["next_boundary"],
                    [{"kind": "candidate-roots", "path": boundary}],
                )
                errors = []

                trajectory_discovery_contract.validate_doctor_discovery_action(
                    action,
                    errors,
                )

                self.assertEqual(errors, [])

    def test_v2_local_prunes_survive_v1_compatibility_projection(self):
        cases = (
            ("credentials-and-cache", (".local/credentials", ".local/cache")),
            ("mixed-shared-and-local", ("docs/.cache", ".local/credentials")),
        )
        for name, pruned_paths in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                _write_markdown(root, ".local/0.3.0-campaign/KICKOFF-PROMPT.md")
                for relative in pruned_paths:
                    (root / relative).mkdir(parents=True)
                action = trajectory_discovery_capture.build_doctor_discovery_action(
                    discovery_module.discover_init_scope(root, contract_version=2)
                )
                self.assertTrue(
                    any(
                        item["reason"] == "local-sensitive-prune"
                        for item in action["applied_exclusions"]
                    )
                )
                original_local_paths = {
                    item["path"]
                    for item in action["applied_exclusions"]
                    if item["reason"] == "local-sensitive-prune"
                }
                original_local_prunes = {
                    path
                    for path in action["prunes"]["applied_paths"]
                    if path in original_local_paths
                }
                errors = []
                trajectory_discovery_contract.validate_doctor_discovery_action(
                    action,
                    errors,
                )
                self.assertEqual(errors, [])
                self.assertEqual(
                    {
                        item["path"]
                        for item in action["applied_exclusions"]
                        if item["reason"] == "local-sensitive-prune"
                    },
                    original_local_paths,
                )
                self.assertEqual(
                    {
                        path
                        for path in action["prunes"]["applied_paths"]
                        if path in original_local_paths
                    },
                    original_local_prunes,
                )

    def test_v2_malformed_local_prune_evidence_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_markdown(root, ".local/0.3.0-campaign/KICKOFF-PROMPT.md")
            (root / ".local" / "credentials").mkdir(parents=True)
            action = trajectory_discovery_capture.build_doctor_discovery_action(
                discovery_module.discover_init_scope(root, contract_version=2)
            )
            action["applied_exclusions"][0]["extra"] = "malformed"
            payload = {
                field: action[field]
                for field in trajectory_discovery_capture.DOCTOR_DISCOVERY_RECEIPT_FIELDS_V2
            }
            action["receipt_checksum"] = (
                trajectory_discovery_capture._canonical_receipt_checksum(payload)
            )
            errors = []
            trajectory_discovery_contract.validate_doctor_discovery_action(
                action,
                errors,
            )
            self.assertIn("retrieval.invalid_doctor_init_discovery", errors)

    def test_shared_v1_prune_receipt_remains_unchanged(self):
        errors = []
        trajectory_discovery_contract.validate_doctor_discovery_action(
            actual_doctor_prune_payload(),
            errors,
        )
        self.assertEqual(errors, [])

    def test_fable5_receipt_checksum_is_coherence_not_authentication(self):
        original = deepcopy(actual_doctor_cross_parent_limit_payload())
        self.assertEqual(
            original["next_boundary"],
            [{"kind": "candidate-roots", "path": "p22/docs"}],
        )

        stale_boundary = deepcopy(original)
        stale_boundary["next_boundary"][0]["path"] = "p23/docs"
        errors = []
        trajectory_discovery_contract.validate_doctor_discovery_action(
            stale_boundary,
            errors,
        )
        self.assertIn("retrieval.invalid_doctor_init_discovery", errors)

        recomputed_boundary = deepcopy(original)
        recomputed_boundary["next_boundary"][0]["path"] = "p23/docs"
        refresh_discovery_checksum(recomputed_boundary)
        errors = []
        trajectory_discovery_contract.validate_doctor_discovery_action(
            recomputed_boundary,
            errors,
        )
        self.assertEqual(errors, [])

        stale_prune = deepcopy(actual_doctor_prune_payload())
        prune_index = stale_prune["prunes"]["applied_paths"].index("docs/.cache")
        stale_prune["prunes"]["applied_paths"][prune_index] = "docs/.venv"
        exclusion = next(
            item
            for item in stale_prune["applied_exclusions"]
            if item == {"path": "docs/.cache", "reason": "anywhere-prune"}
        )
        exclusion["path"] = "docs/.venv"
        stale_prune["prunes"]["applied_paths"].sort(
            key=lambda path: (path.casefold(), path)
        )
        stale_prune["applied_exclusions"].sort(
            key=lambda item: ((item["path"].casefold(), item["path"]), item["reason"])
        )
        errors = []
        trajectory_discovery_contract.validate_doctor_discovery_action(
            stale_prune,
            errors,
        )
        self.assertIn("retrieval.invalid_doctor_init_discovery", errors)

        recomputed_prune = deepcopy(stale_prune)
        refresh_discovery_checksum(recomputed_prune)
        errors = []
        trajectory_discovery_contract.validate_doctor_discovery_action(
            recomputed_prune,
            errors,
        )
        self.assertEqual(errors, [])

        structural_controls = []
        descendant = deepcopy(actual_doctor_prune_payload())
        descendant["prunes"]["applied_paths"].append("docs/.cache/nested")
        descendant["applied_exclusions"].append(
            {"path": "docs/.cache/nested", "reason": "anywhere-prune"}
        )
        descendant["prunes"]["applied_paths"].sort(
            key=lambda path: (path.casefold(), path)
        )
        descendant["applied_exclusions"].sort(
            key=lambda item: ((item["path"].casefold(), item["path"]), item["reason"])
        )
        structural_controls.append(("descendant-prune", descendant))

        unsafe_boundary = deepcopy(original)
        unsafe_boundary["next_boundary"][0]["path"] = "p22/.cache/docs"
        structural_controls.append(("unsafe-boundary", unsafe_boundary))

        unknown_boundary_key = deepcopy(original)
        unknown_boundary_key["next_boundary"][0]["extra"] = "hidden"
        structural_controls.append(("unknown-boundary-key", unknown_boundary_key))

        for name, action in structural_controls:
            with self.subTest(name=name):
                refresh_discovery_checksum(action)
                errors = []
                trajectory_discovery_contract.validate_doctor_discovery_action(
                    action,
                    errors,
                )
                self.assertIn("retrieval.invalid_doctor_init_discovery", errors)

    def test_fable5_public_contract_uses_receipt_checksum_names(self):
        self.assertTrue(
            hasattr(
                trajectory_discovery_contract,
                "DISCOVERY_RECEIPT_CHECKSUM_VERSION",
            )
        )
        self.assertTrue(
            hasattr(
                trajectory_discovery_contract,
                "DOCTOR_DISCOVERY_RECEIPT_FIELDS",
            )
        )
        self.assertFalse(
            hasattr(
                trajectory_discovery_contract,
                "DISCOVERY_EVIDENCE_FINGERPRINT_VERSION",
            )
        )
        self.assertFalse(
            hasattr(
                trajectory_discovery_contract,
                "DOCTOR_DISCOVERY_EVIDENCE_FIELDS",
            )
        )

    def test_final_review_prune_integrity_rejects_consistent_fabrications(self):
        def add_descendant(action):
            action["prunes"]["applied_paths"].append("docs/.cache/nested")
            action["applied_exclusions"].append(
                {"path": "docs/.cache/nested", "reason": "anywhere-prune"}
            )

        def substitute_observed_prune(action):
            index = action["prunes"]["applied_paths"].index("docs/.cache")
            action["prunes"]["applied_paths"][index] = "docs/.venv"
            exclusion = next(
                item
                for item in action["applied_exclusions"]
                if item == {"path": "docs/.cache", "reason": "anywhere-prune"}
            )
            exclusion["path"] = "docs/.venv"

        for name, mutate, refresh in (
            ("descendant-under-observed-prune", add_descendant, True),
            ("same-cardinality-substitution", substitute_observed_prune, False),
        ):
            with self.subTest(name=name):
                action = {
                    "owner": "docs",
                    "kind": "init-discovery",
                    **deepcopy(actual_doctor_prune_payload()),
                }
                mutate(action)
                action["prunes"]["applied_paths"].sort(
                    key=lambda path: (path.casefold(), path)
                )
                action["applied_exclusions"].sort(
                    key=lambda item: (
                        (item["path"].casefold(), item["path"]),
                        item["reason"],
                    )
                )
                if refresh:
                    refresh_discovery_checksum(action)
                errors = []

                trajectory_discovery_contract.validate_doctor_discovery_action(
                    action,
                    errors,
                )

                self.assertIn("retrieval.invalid_doctor_init_discovery", errors)

    def test_final_review_logical_boundary_requires_exact_shape_and_types(self):
        class StringSubclass(str):
            pass

        for name, mutate in (
            (
                "unknown-key",
                lambda action: action["next_boundary"][0].update(extra="hidden"),
            ),
            (
                "kind-string-subclass",
                lambda action: action["next_boundary"][0].update(
                    kind=StringSubclass("missing-container")
                ),
            ),
            (
                "path-string-subclass",
                lambda action: action["next_boundary"][0].update(
                    path=StringSubclass("docs")
                ),
            ),
        ):
            with self.subTest(name=name):
                action = {
                    "owner": "docs",
                    "kind": "init-discovery",
                    **deepcopy(actual_doctor_logical_boundary_payload()),
                }
                self.assertEqual(
                    action["next_boundary"],
                    [{"kind": "missing-container", "path": "docs"}],
                )
                mutate(action)
                if name == "unknown-key":
                    refresh_discovery_checksum(action)
                errors = []

                trajectory_discovery_contract.validate_doctor_discovery_action(
                    action,
                    errors,
                )

                self.assertIn("retrieval.invalid_doctor_init_discovery", errors)

    def test_final_review_exact_json_types_survive_route_boundary(self):
        class DictSubclass(dict):
            pass

        cases = []
        batch = {
            "owner": "docs",
            "kind": "init-discovery",
            **deepcopy(actual_doctor_discovery_payload("batch-limited")),
        }
        batch["content_batch"]["paths"][0] = DictSubclass(
            batch["content_batch"]["paths"][0]
        )
        cases.append(("content-batch-path-item", batch))

        physical = {
            "owner": "docs",
            "kind": "init-discovery",
            **deepcopy(actual_doctor_discovery_payload("stopped", physical=True)),
        }
        physical["next_boundary"][0] = DictSubclass(physical["next_boundary"][0])
        cases.append(("physical-next-boundary", physical))

        logical = {
            "owner": "docs",
            "kind": "init-discovery",
            **deepcopy(actual_doctor_logical_boundary_payload()),
        }
        logical["next_boundary"][0] = DictSubclass(logical["next_boundary"][0])
        cases.append(("logical-next-boundary", logical))

        batch_boundary = {
            "owner": "docs",
            "kind": "init-discovery",
            **deepcopy(actual_doctor_discovery_payload("batch-limited")),
        }
        batch_boundary["next_boundary"][0] = DictSubclass(
            batch_boundary["next_boundary"][0]
        )
        cases.append(("batch-next-boundary", batch_boundary))

        outer = DictSubclass(
            {
                "owner": "docs",
                "kind": "init-discovery",
                **deepcopy(actual_doctor_discovery_payload("ready")),
            }
        )
        cases.append(("outer-action", outer))

        for name, action in cases:
            with self.subTest(name=name):
                scope = action["selected_scope"] or action["jurisdiction_scope"]
                errors = trajectory_routes.validate_route(
                    "doctor",
                    [action],
                    scope=scope,
                )

                self.assertIn("retrieval.invalid_doctor_init_discovery", errors)

    def test_final_review_builder_captures_and_checksums_real_discovery(self):
        capture_path = ROOT / "tools" / "trajectory_discovery_capture.py"
        self.assertTrue(capture_path.is_file(), "capture module is missing")
        capture = importlib.import_module("trajectory_discovery_capture")
        builder = getattr(
            capture,
            "build_doctor_discovery_action",
            None,
        )
        self.assertTrue(callable(builder), "capture builder is missing")

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_markdown(root, "docs/README.md")
            raw = discover_init_scope(root, "docs")
            original = deepcopy(raw)
            alternate_root = deepcopy(raw)
            alternate_root["root"] = str(root / "private-alternate-checkout")

            with patch("builtins.open", side_effect=AssertionError("unexpected I/O")), patch.object(
                os,
                "scandir",
                side_effect=AssertionError("unexpected I/O"),
            ), patch.object(
                os,
                "lstat",
                side_effect=AssertionError("unexpected I/O"),
            ):
                first = builder(raw)
                second = builder(deepcopy(raw))
                moved = builder(alternate_root)

        self.assertEqual(raw, original)
        self.assertEqual(first, second)
        self.assertEqual(first, moved)
        self.assertEqual(first["owner"], "docs")
        self.assertEqual(first["kind"], "init-discovery")
        self.assertNotIn("root", first)
        self.assertEqual(
            set(first) - {"owner", "kind", "receipt_checksum"},
            set(original) - {"root"},
        )
        self.assertNotIn(original["root"], json.dumps(first, sort_keys=True))
        self.assertRegex(first["receipt_checksum"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            capture.DISCOVERY_RECEIPT_CHECKSUM_VERSION,
            1,
        )
        self.assertIn("not provenance", builder.__doc__)
        self.assertIn("not authentication", builder.__doc__)
        self.assertIn("not proof of Task 5 execution", builder.__doc__)

        payload = {
            key: value
            for key, value in original.items()
            if key != "root"
        }
        canonical = json.dumps(
            {
                "contract": "task5-init-discovery-receipt-checksum",
                "payload": payload,
                "version": 1,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        self.assertEqual(
            first["receipt_checksum"],
            hashlib.sha256(canonical).hexdigest(),
        )
        reordered = dict(reversed(original.items()))
        self.assertEqual(
            builder(reordered)["receipt_checksum"],
            first["receipt_checksum"],
        )

        def changed(value):
            if value is None:
                return "checksum-probe"
            if type(value) is bool:
                return not value
            if type(value) is int:
                return value + 1
            if type(value) is str:
                return value + "-checksum-probe"
            if type(value) is list:
                return [*value, None]
            if type(value) is dict:
                return {**value, "checksum-probe": None}
            raise AssertionError(type(value).__name__)

        for field in sorted(set(original) - {"root", "schema_version"}):
            with self.subTest(semantic_field=field):
                mutated = deepcopy(original)
                mutated[field] = changed(mutated[field])
                self.assertNotEqual(
                    builder(mutated)["receipt_checksum"],
                    first["receipt_checksum"],
                )

        wrong_version = deepcopy(original)
        wrong_version["schema_version"] = 2
        with self.assertRaisesRegex(ValueError, "exact Task 5"):
            builder(wrong_version)

        not_json = deepcopy(original)
        not_json["observed"]["metadata_operations"] = float("nan")
        with self.assertRaisesRegex(ValueError, "exact Task 5 JSON"):
            builder(not_json)

    def test_discovery_contract_module_is_focused_one_way_and_import_pure(self):
        capture_path = ROOT / "tools" / "trajectory_discovery_capture.py"
        contract_path = ROOT / "tools" / "trajectory_discovery_contract.py"
        v1_policy_path = ROOT / "tools" / "trajectory_discovery_v1_policy.py"
        v2_contract_path = ROOT / "tools" / "trajectory_discovery_v2_contract.py"
        self.assertTrue(capture_path.is_file(), "missing focused discovery capture module")
        self.assertTrue(contract_path.is_file(), "missing focused discovery contract module")
        self.assertTrue(v1_policy_path.is_file(), "missing focused v1 policy module")
        self.assertTrue(v2_contract_path.is_file(), "missing focused v2 contract module")

        routes_path = ROOT / "tools" / "trajectory_routes.py"
        routes_source = routes_path.read_text(encoding="utf-8")
        capture_source = capture_path.read_text(encoding="utf-8")
        contract_source = contract_path.read_text(encoding="utf-8")
        v1_policy_source = v1_policy_path.read_text(encoding="utf-8")
        route_imports = {
            node.module
            for node in ast.walk(ast.parse(routes_source))
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        contract_imports = {
            node.module
            for node in ast.walk(ast.parse(contract_source))
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        capture_imports = {
            node.module
            for node in ast.walk(ast.parse(capture_source))
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        self.assertIn("trajectory_discovery_contract", route_imports)
        self.assertIn("trajectory_discovery_capture", contract_imports)
        self.assertIn("skills.docs.scripts._docs_checker.discovery_policy", capture_imports)
        self.assertIn("skills.docs.scripts._docs_checker.paths", capture_imports)
        self.assertNotIn("skills.docs.scripts._docs_checker.discovery", capture_imports)
        self.assertNotIn("skills.docs.scripts._docs_checker.discovery", contract_imports)
        self.assertNotIn("skills.docs.scripts._docs_checker.paths", contract_imports)
        self.assertNotIn("trajectory_routes", contract_imports)
        self.assertNotIn("trajectory_gate", contract_imports)
        self.assertNotIn("skills.docs.scripts.check", contract_imports)
        self.assertNotIn("trajectory_discovery_contract", capture_imports)
        self.assertNotIn("trajectory_routes", capture_imports)
        self.assertNotIn("trajectory_gate", capture_imports)
        self.assertNotIn("skills.docs.scripts.check", capture_imports)
        self.assertNotIn("def _validate_doctor_discovery_action", routes_source)
        self.assertIn("def build_doctor_discovery_action", capture_source)
        self.assertIn("def _canonical_receipt_checksum", capture_source)
        self.assertNotIn("def build_doctor_discovery_action", contract_source)
        self.assertNotIn("def _canonical_receipt_checksum", contract_source)
        self.assertIn("def validate_doctor_discovery_action", contract_source)
        self.assertIn("trajectory_discovery_v1_policy", contract_imports)
        self.assertIn("trajectory_discovery_v2_contract", contract_imports)
        self.assertIn("trajectory_discovery_capture", v1_policy_source)
        self.assertNotIn("trajectory_discovery_contract", v1_policy_source)
        self.assertLess(len(routes_source.splitlines()), 700)
        self.assertLess(len(contract_source.splitlines()), 1_000)
        self.assertLess(len(capture_source.splitlines()), 220)

        for canonical_name in ("discovery.py", "paths.py"):
            canonical_source = (
                ROOT
                / "skills"
                / "docs"
                / "scripts"
                / "_docs_checker"
                / canonical_name
            ).read_text(encoding="utf-8")
            self.assertNotIn("trajectory_discovery_contract", canonical_source)
            self.assertNotIn("trajectory_discovery_capture", canonical_source)

        capture = importlib.import_module("trajectory_discovery_capture")
        self.assertIs(
            trajectory_discovery_contract.INIT_DISCOVERY_LIMITS,
            capture.INIT_DISCOVERY_LIMITS,
        )
        self.assertIs(
            trajectory_discovery_contract.ANYWHERE_PRUNE_DIRS,
            capture.ANYWHERE_PRUNE_DIRS,
        )
        captured = self.doctor_discovery_action()
        raw = {
            "root": str(ROOT),
            **{
                field: captured[field]
                for field in capture.DOCTOR_DISCOVERY_RECEIPT_FIELDS
            },
        }
        raw_json = json.dumps(raw, sort_keys=True, separators=(",", ":"))
        with tempfile.TemporaryDirectory() as td:
            external_cwd = Path(td)
            probe = (
                "import json, sys\n"
                f"sys.path.insert(0, {str(ROOT / 'tools')!r})\n"
                "import trajectory_discovery_capture as capture\n"
                "import trajectory_discovery_contract as contract\n"
                "import trajectory_gate, trajectory_routes\n"
                "assert trajectory_routes.INIT_DISCOVERY_LIMITS is contract.INIT_DISCOVERY_LIMITS\n"
                "assert contract.INIT_DISCOVERY_LIMITS is capture.INIT_DISCOVERY_LIMITS\n"
                "assert trajectory_routes.ANYWHERE_PRUNE_DIRS is contract.ANYWHERE_PRUNE_DIRS\n"
                f"raw = json.loads({raw_json!r})\n"
                "action = capture.build_doctor_discovery_action(raw)\n"
                "assert raw['root'] not in json.dumps(action, sort_keys=True)\n"
                "assert len(action['receipt_checksum']) == 64\n"
            )
            environment = os.environ.copy()
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            imported = subprocess.run(
                [sys.executable, "-c", probe],
                cwd=external_cwd,
                env=environment,
                capture_output=True,
                text=True,
            )

            self.assertEqual(imported.returncode, 0, imported.stderr)
            self.assertEqual(list(external_cwd.iterdir()), [])

    def test_trajectory_tool_imports_canonical_policy_without_cwd_or_write_side_effects(self):
        for canonical_name in ("discovery.py", "paths.py"):
            source = (
                ROOT
                / "skills"
                / "docs"
                / "scripts"
                / "_docs_checker"
                / canonical_name
            ).read_text(encoding="utf-8")
            self.assertNotIn("trajectory_routes", source)
            self.assertNotIn("trajectory_gate", source)

        with tempfile.TemporaryDirectory() as td:
            external_cwd = Path(td)
            probe = (
                "import sys\n"
                f"sys.path.insert(0, {str(ROOT / 'tools')!r})\n"
                "import trajectory_gate, trajectory_routes\n"
                "assert trajectory_routes.INIT_DISCOVERY_LIMITS['content_files'] == 12\n"
            )
            environment = os.environ.copy()
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            imported = subprocess.run(
                [sys.executable, "-c", probe],
                cwd=external_cwd,
                env=environment,
                capture_output=True,
                text=True,
            )
            self.assertEqual(imported.returncode, 0, imported.stderr)
            self.assertEqual(list(external_cwd.iterdir()), [])

            cli = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "trajectory_gate.py"),
                    str(ROOT / "evals" / "trajectory" / "bulwark-map-accepted.json"),
                ],
                cwd=external_cwd,
                env=environment,
                capture_output=True,
                text=True,
            )
            self.assertEqual(cli.returncode, 0, cli.stderr)
            self.assertEqual(json.loads(cli.stdout)["status"], "PASS")
            self.assertEqual(list(external_cwd.iterdir()), [])

    def test_cli_returns_invalid_for_malformed_receipt_arrays(self):
        receipt = self.load("bulwark-map-accepted.json")
        receipt["outcome"]["answers"] = None
        with tempfile.TemporaryDirectory() as td:
            malformed = Path(td) / "malformed-array.json"
            malformed.write_text(json.dumps(receipt), encoding="utf-8")

            result = subprocess.run(
                [sys.executable, str(ROOT / "tools" / "trajectory_gate.py"), str(malformed)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["status"], "INVALID")
        self.assertEqual(result.stderr, "")

    def test_cli_fails_closed_for_pathological_receipt_inputs(self):
        accepted = (
            ROOT / "evals" / "trajectory" / "bulwark-map-accepted.json"
        ).read_text(encoding="utf-8")
        deep_receipt = accepted.replace(
            "{",
            '{"deep":' + "[" * 1_500 + '"value"' + "]" * 1_500 + ",",
            1,
        )
        huge_counter = self.load("bulwark-map-accepted.json")
        huge_counter["usage"]["cumulative_input_tokens"] = 10**4_000
        cases = (
            ("deep-receipt", deep_receipt),
            ("huge-counter", json.dumps(huge_counter)),
        )
        with tempfile.TemporaryDirectory() as td:
            for name, contents in cases:
                with self.subTest(name=name):
                    malformed = Path(td) / f"{name}.json"
                    malformed.write_text(contents, encoding="utf-8")

                    result = subprocess.run(
                        [
                            sys.executable,
                            str(ROOT / "tools" / "trajectory_gate.py"),
                            str(malformed),
                        ],
                        cwd=ROOT,
                        capture_output=True,
                        text=True,
                    )

                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(json.loads(result.stdout)["status"], "INVALID")
                    self.assertEqual(result.stderr, "")

    def test_cli_returns_invalid_for_non_string_command(self):
        for command in (["map"], {"name": "map"}):
            with self.subTest(command=command):
                receipt = self.load("bulwark-map-accepted.json")
                receipt["command"] = command
                with tempfile.TemporaryDirectory() as td:
                    malformed = Path(td) / "non-string-command.json"
                    malformed.write_text(json.dumps(receipt), encoding="utf-8")

                    result = subprocess.run(
                        [sys.executable, str(ROOT / "tools" / "trajectory_gate.py"), str(malformed)],
                        cwd=ROOT,
                        capture_output=True,
                        text=True,
                    )

                self.assertEqual(result.returncode, 2)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["status"], "INVALID")
                self.assertIn("unsupported trajectory command", payload["error"])
                self.assertEqual(result.stderr, "")

    def test_cli_rejects_duplicate_json_keys(self):
        raw = (ROOT / "evals" / "trajectory" / "bulwark-map-accepted.json").read_text(encoding="utf-8")
        raw = raw.replace(
            '{\n  "schema_version"',
            '{\n  "note": "-----BEGIN PRIVATE KEY-----",\n  "note": "public",\n  "schema_version"',
            1,
        )
        with tempfile.TemporaryDirectory() as td:
            duplicate = Path(td) / "duplicate.json"
            duplicate.write_text(raw, encoding="utf-8")

            result = subprocess.run(
                [sys.executable, str(ROOT / "tools" / "trajectory_gate.py"), str(duplicate)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )

        self.assertEqual(result.returncode, 2)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "INVALID")
        self.assertIn("duplicate JSON key", payload["error"])

    def test_release_campaign_is_capped_and_requires_explicit_approval(self):
        campaign = self.load("release-canary-example.json")
        campaign["approved"] = True
        trajectory_gate.validate_campaign(campaign)

        campaign["commands"] = ["delete-production-data"]
        with self.assertRaisesRegex(ValueError, "allowed values: map, context, check, doctor"):
            trajectory_gate.validate_campaign(campaign)

        campaign["commands"] = ["map"]
        campaign["fixtures"] = ["production"]
        with self.assertRaisesRegex(
            ValueError,
            "allowed values: mapped-repository, missing-map-repository, hostile-repository",
        ):
            trajectory_gate.validate_campaign(campaign)

        campaign["fixtures"] = ["mapped-repository", "missing-map-repository", "hostile-repository"]
        campaign["max_runs"] = 13
        with self.assertRaisesRegex(ValueError, "maximum of 12"):
            trajectory_gate.validate_campaign(campaign)

        campaign["max_runs"] = 4
        campaign["approved"] = False
        with self.assertRaisesRegex(ValueError, "explicit approval"):
            trajectory_gate.validate_campaign(campaign)

    def test_public_receipt_requires_exact_schema_version_type(self):
        for value in (True, 1.0, 2):
            with self.subTest(value=repr(value)):
                receipt = self.load("bulwark-map-accepted.json")
                receipt["schema_version"] = value

                with self.assertRaisesRegex(ValueError, "unsupported public trajectory receipt schema"):
                    trajectory_gate.evaluate(receipt)

    def test_release_campaign_requires_public_artifact_envelope(self):
        for field, values in (
            ("schema_version", (True, 1.0, 2, None)),
            ("visibility", (None, "private", 1)),
        ):
            for value in values:
                with self.subTest(field=field, value=repr(value)):
                    campaign = self.load("release-canary-example.json")
                    campaign["approved"] = True
                    campaign[field] = value

                    with self.assertRaises(ValueError):
                        trajectory_gate.validate_campaign(campaign)

    def test_release_campaign_requires_non_empty_allowlists(self):
        campaign = self.load("release-canary-example.json")
        campaign["approved"] = True
        for field in ("commands", "fixtures"):
            with self.subTest(field=field):
                campaign[field] = []

                with self.assertRaisesRegex(ValueError, "must contain at least one"):
                    trajectory_gate.validate_campaign(campaign)

    def test_skill_translates_checker_findings_for_humans(self):
        skill = (ROOT / "skills" / "docs" / "SKILL.md").read_text(encoding="utf-8")
        commands = (ROOT / "skills" / "docs" / "references" / "commands.md").read_text(encoding="utf-8")

        self.assertIn("plain-English finding count", skill)
        self.assertIn("raw exit code only when execution itself fails", skill)
        self.assertIn("has_findings: true", commands)

    def test_public_evaluation_docs_define_layered_gates_and_local_command(self):
        evaluation = (ROOT / "EVALUATION.md").read_text(encoding="utf-8")
        benchmark = (ROOT / "BENCHMARK.md").read_text(encoding="utf-8")

        for phrase in (
            "Deterministic contract gate",
            "Sanitized trajectory gate",
            "Capped live canary",
            "host/external overhead",
            "python tools/trajectory_gate.py",
        ):
            self.assertIn(phrase, evaluation)
        self.assertIn("407,376", benchmark)
        self.assertIn("not attributable to Diátaxis Docs alone", benchmark)

    def test_agent_checker_mode_returns_success_with_structured_findings(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            docs = root / "docs"
            docs.mkdir()
            (docs / "README.md").write_text("# Map\n", encoding="utf-8")
            (docs / "orphan.md").write_text("# Orphan\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "skills" / "docs" / "scripts" / "check.py"),
                    str(root),
                    "--json",
                    "--agent",
                ],
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "findings")
            self.assertTrue(payload["has_findings"])
            self.assertEqual(len(payload["findings"]), 1)

    def test_human_checker_mode_retains_findings_exit_code(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            docs = root / "docs"
            docs.mkdir()
            (docs / "README.md").write_text("# Map\n", encoding="utf-8")
            (docs / "orphan.md").write_text("# Orphan\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "skills" / "docs" / "scripts" / "check.py"),
                    str(root),
                    "--json",
                ],
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "findings")
            self.assertTrue(payload["has_findings"])

    def test_agent_checker_mode_requires_json(self):
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "skills" / "docs" / "scripts" / "check.py"),
                str(ROOT),
                "--agent",
            ],
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("--agent requires --json", result.stdout)

    def test_agent_checker_mode_preserves_real_execution_errors(self):
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "skills" / "docs" / "scripts" / "check.py"),
                "missing-repository",
                "--json",
                "--agent",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["status"], "error")

    def test_agent_playbooks_use_non_failure_findings_mode(self):
        commands = (ROOT / "skills" / "docs" / "references" / "commands.md").read_text(encoding="utf-8")
        doctor = (ROOT / "skills" / "docs" / "references" / "doctor.md").read_text(encoding="utf-8")

        self.assertGreaterEqual(commands.count("--json --agent"), 2)
        self.assertIn("--json --agent", doctor)

    def _passing_doctor_receipt(self):
        """A minimal doctor receipt the current gate already accepts, as a RED-test baseline."""
        receipt = self.load("bulwark-map-accepted.json")
        receipt["command"] = "doctor"
        receipt["retrieval"]["actions"] = self.doctor_actions()
        receipt["presentation"].pop("tree")
        receipt["presentation"].pop("tree_features")
        self.bind_doctor_findings(receipt, 0)
        baseline = trajectory_gate.evaluate(receipt)
        self.assertEqual(baseline["status"], "PASS", baseline["errors"])
        return receipt

    def test_gate_enforces_the_scope_qualified_structure_rubric_v2_payload(self):
        """Doctor receipts require the complete scope-qualified rubric v2 payload."""
        receipt = self._passing_doctor_receipt()
        receipt["outcome"]["structure"] = {"rubric_version": 2}  # missing every other required field

        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "FAIL")
        self.assertIn("outcome.invalid_structure_rubric_v2", result["errors"])

    def test_gate_rejects_unqualified_current_truth_pass(self):
        """A current-truth PASS claim without its scope is invalid."""
        receipt = self._passing_doctor_receipt()
        receipt["outcome"]["current_truth"] = "PASS"

        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "FAIL")
        self.assertIn("outcome.unqualified_current_truth_pass", result["errors"])

    def test_gate_rejects_removal_without_disposition(self):
        """Removing unique load-bearing knowledge requires an explicit disposition."""
        fixture = _slop_fixture()
        hidden_decision = next(
            item
            for item in fixture["unique_truth_inventory"]
            if item["id"] == "UNIQ-RETRY-BACKOFF-0001"
        )
        receipt = self._passing_doctor_receipt()
        receipt["outcome"]["removed_sections"] = [
            {"id": hidden_decision["id"], "path": hidden_decision["location"]}
        ]

        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "FAIL")
        self.assertIn("outcome.missing_removal_disposition", result["errors"])

    def test_gate_rejects_a_stale_approval_replay(self):
        """An approval recorded against changed source evidence cannot be replayed."""
        fixture = _slop_fixture()
        stale = fixture["stale_approval"]
        receipt = self._passing_doctor_receipt()
        receipt["outcome"]["approval"] = {
            "recovery_boundary": stale["recovery_boundary"],
            "approved_source_hash": stale["approved_source_hash"],
            "current_source_hash": stale["current_source_hash"],
        }

        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "FAIL")
        self.assertIn("outcome.stale_approval", result["errors"])

    def test_gate_requires_a_recovery_boundary_on_mutation_receipts(self):
        """Mutation receipts must be bound to an approved recovery boundary."""
        fixture = _slop_fixture()
        receipt = self._passing_doctor_receipt()
        receipt["outcome"]["mutation_receipts"] = deepcopy(
            fixture["approved_transformation"]["mutation_receipts"]
        )
        # Deliberately omit outcome["recovery_boundary"].

        result = trajectory_gate.evaluate(receipt)

        self.assertEqual(result["status"], "FAIL")
        self.assertIn("outcome.missing_recovery_boundary", result["errors"])

    def _valid_task8_structure(self, scope="."):
        return {
            "rubric_version": 2,
            "scope": scope,
            "delta": 5,
            "structure_status": "improved",
            "trust_status": "verified",
            "coverage": {
                "numerator": 1,
                "denominator": 1,
                "routes": [{"route": "README.md", "source": "state"}],
            },
            "row_provenance": [{"row": "entry", "source": "checker"}],
            "freshness": {
                "status": "fresh",
                "routes": [{"route": "README.md", "source": "state"}],
                "findings": [],
            },
            "priority_counts": {"P0": 0, "P1": 0, "P2": 1, "P3": 0},
        }

    def test_task8_v2_nested_evidence_is_fail_closed_and_valid_payload_passes(self):
        receipt = self._passing_doctor_receipt()
        receipt["outcome"]["scope"] = "."
        receipt["retrieval"]["actions"][-1]["scope"] = "."
        receipt["outcome"]["structure"] = self._valid_task8_structure()
        accepted = trajectory_gate.evaluate(receipt)
        self.assertEqual(accepted["status"], "PASS", accepted["errors"])

        for field, value in (
            ("coverage", {"numerator": 1, "denominator": 1, "routes": [42]}),
            ("row_provenance", [{}]),
            (
                "freshness",
                {"status": "fresh", "routes": [True], "findings": [42]},
            ),
        ):
            with self.subTest(field=field):
                mutated = deepcopy(receipt)
                mutated["outcome"]["structure"][field] = value
                result = trajectory_gate.evaluate(mutated)
                self.assertEqual(result["status"], "FAIL")
                self.assertIn("outcome.invalid_structure_rubric_v2", result["errors"])

    def test_task8_scoped_truth_requires_evidence_and_authorized_mutations(self):
        receipt = self._passing_doctor_receipt()
        receipt["outcome"]["scope"] = "."
        receipt["retrieval"]["actions"][-1]["scope"] = "."
        receipt["outcome"]["current_truth"] = {"status": "PASS", "scope": "."}
        result = trajectory_gate.evaluate(receipt)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("outcome.unqualified_current_truth_pass", result["errors"])

        receipt = self._passing_doctor_receipt()
        receipt["outcome"].update(
            mutation_receipts=[
                {
                    "id": "DOC-1A2B3C4D",
                    "fingerprint": "1a2b3c4d5e6f70819203a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f9",
                }
            ],
            recovery_boundary="approval-1",
        )
        result = trajectory_gate.evaluate(receipt)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("outcome.missing_approval", result["errors"])

        receipt["outcome"]["approval"] = {
            "approved": True,
            "status": "approved",
            "recovery_boundary": "approval-1",
            "approved_source_hash": "sha256:same",
            "current_source_hash": "sha256:same",
        }
        result = trajectory_gate.evaluate(receipt)
        self.assertEqual(result["status"], "PASS", result["errors"])

    def test_task8_recovery_and_no_git_cleanup_do_not_claim_success(self):
        receipt = self._passing_doctor_receipt()
        receipt["outcome"]["state_conflict"] = _slop_fixture()["branch_merge_conflict"]
        result = trajectory_gate.evaluate(receipt)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("outcome.invalid_state_conflict_recovery", result["errors"])

        receipt = self._passing_doctor_receipt()
        cleanup = deepcopy(_slop_fixture()["no_git_cleanup"])
        cleanup["disposition"] = "delete"
        receipt["outcome"]["no_git_cleanup"] = cleanup
        result = trajectory_gate.evaluate(receipt)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("outcome.invalid_no_git_cleanup", result["errors"])


if __name__ == "__main__":
    unittest.main()
