import hashlib
import inspect
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).parents[1]
SKILL = ROOT / "skills" / "docs"
sys.path.insert(0, str(SKILL / "scripts"))
import check as docs_checker
from _docs_checker.lifecycle import init_event_fingerprint
from _docs_checker.memory import (
    operational_findings_digest,
    operational_state_digest,
)


EVENT_ID = "EVT-93A10AFF"
DIGEST_A = "sha256-text:" + hashlib.sha256(b"# State\n").hexdigest()
DIGEST_B = "sha256-text:" + hashlib.sha256(b"VALUE = 1\n").hexdigest()
MAX_MANIFEST_BYTES = 1024 * 1024

BASE_DOCUMENTS = {
    "docs/DESIGN.md": b"# Design\n\n## Visual language\n",
    "docs/README.md": (
        b"# Documentation\n\n[State](STATE.md)\n[Design](DESIGN.md)\n"
    ),
    "docs/STATE.md": b"# State\n",
}


def canonical_bytes(value):
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode("utf-8")


def corpus_v3():
    paths = sorted(BASE_DOCUMENTS, key=lambda path: (path.casefold(), path))
    paths_digest = hashlib.sha256(
        canonical_bytes(
            {
                "ordering_version": "repo-relative-casefold-v1",
                "paths": paths,
            }
        )
    ).hexdigest()
    return {
        "coverage_version": "init-corpus-v1",
        "coverage_mode": "selected-scope-exact",
        "ordering_version": "repo-relative-casefold-v1",
        "selected_scope": "docs",
        "write_boundary": "docs",
        "path_count": len(paths),
        "paths_digest": "sha256:" + paths_digest,
    }


def manifest_payload():
    corpus = corpus_v3()
    dispositions = []
    for path, data in sorted(
        BASE_DOCUMENTS.items(), key=lambda item: (item[0].casefold(), item[0])
    ):
        dispositions.append(
            {
                "item_id": f"{path}#<whole-file>",
                "path": path,
                "section": {"kind": "whole-file"},
                "disposition": "RETAIN",
                "reason": "Retain the verified whole document.",
                "source_digest": "sha256:" + hashlib.sha256(data).hexdigest(),
            }
        )
    return {
        "schema_version": 3,
        "approval_identity": hashlib.sha256(canonical_bytes([])).hexdigest(),
        "corpus_transition": {"starting": corpus, "result": corpus},
        "dispositions": dispositions,
        "document_results": [],
    }


def complete_measurements(**overrides):
    measurements = {
        "map_exists": True,
        "map_has_h1": True,
        "map_has_body": True,
        "map_has_h2": True,
        "maintained_files": 2,
        "maintained_paths": 2,
        "safe_maintained_paths": 2,
        "checked_links": 1,
        "valid_links": 1,
        "checked_anchors": 0,
        "valid_anchors": 0,
        "valid_navigation_routes": 1,
        "reachable_files": 2,
        "usable_unique_titles": 2,
        "hot_bytes": 128,
        "hot_path_files": [
            {"path": "docs/README.md", "bytes": 64},
            {"path": "docs/STATE.md", "bytes": 64},
        ],
    }
    unknown = sorted(set(overrides) - set(measurements))
    if unknown:
        raise TypeError(f"unknown measurement override(s): {', '.join(unknown)}")
    measurements.update(overrides)
    return measurements


def finding(**overrides):
    record = {
        "id": "DOC-12345678",
        "kind": "test-finding",
        "priority": "P2",
        "status": "Proposed",
    }
    unknown = sorted(set(overrides) - set(record))
    if unknown:
        raise TypeError(f"unknown finding override(s): {', '.join(unknown)}")
    record.update(overrides)
    return record


def verified_document_fixture(**overrides):
    values = {
        "document": "docs/STATE.md",
        "document_digest": DIGEST_A,
        "source": "src/config.py",
        "source_digest": DIGEST_B,
        "verified_event": EVENT_ID,
    }
    unknown = sorted(set(overrides) - set(values))
    if unknown:
        raise TypeError(f"unknown verified-document override(s): {', '.join(unknown)}")
    values.update(overrides)
    return {
        "document": values["document"],
        "digest": values["document_digest"],
        "sources": [
            {"path": values["source"], "digest": values["source_digest"]}
        ],
        "verified_event": values["verified_event"],
    }


def run_checker_fixture(*, cold_paths=(), map_links=(), hot_sources=()):
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        control = create_repository(root)
        archive = root / "docs" / "archive"
        archive.mkdir()
        (archive / "current.md").write_text(
            "# Archived current route\n", encoding="utf-8"
        )
        state = valid_state()
        state["cold_paths"] = list(cold_paths)
        write_json(control / "state.json", state)
        links = "\n".join(f"[Route]({route})" for route in map_links)
        (root / "docs" / "README.md").write_text(
            "# Documentation\n\nStart here.\n\n" + links + "\n",
            encoding="utf-8",
        )
        if hot_sources:
            anchors = ", ".join(f"`{route}`" for route in hot_sources)
            (root / "docs" / "STATE.md").write_text(
                f"# State\n\nSources: {anchors}\n", encoding="utf-8"
            )
        proc = subprocess.run(
            [
                sys.executable,
                str(SKILL / "scripts" / "check.py"),
                str(root),
                "--json",
                "--agent",
                "--hot",
                "docs/STATE.md",
            ],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise AssertionError(proc.stdout + proc.stderr)
        return json.loads(proc.stdout)


def _state_payload(
    *,
    last_completed_event=EVENT_ID,
    manifest_identity=None,
    result_corpus=None,
    document_results_digest=None,
):
    manifest_identity = manifest_identity or hashlib.sha256(manifest_bytes()).hexdigest()
    result_corpus = result_corpus or corpus_v3()
    document_results_digest = document_results_digest or (
        "sha256:" + hashlib.sha256(canonical_bytes([])).hexdigest()
    )
    return {
        "schema_version": 3,
        "initialized": {
            "completed": True,
            "skill_version": "0.3.0",
            "map": "docs/README.md",
            "hot_paths": ["docs/STATE.md"],
        },
        "rubric": {
            "version": 2,
            "last_verified_score": 84,
            "last_verified_status": "needs-attention",
        },
        "cold_paths": ["docs/archive/**"],
        "verified_documents": [
            {
                "document": "docs/STATE.md",
                "digest": DIGEST_A,
                "sources": [{"path": "src/config.py", "digest": DIGEST_B}],
                "verified_event": last_completed_event,
            }
        ],
        "protected_intent": [
            {
                "id": "INTENT-001",
                "intent_key": "primary-action-color",
                "source": "docs/DESIGN.md#visual-language",
                "preserve": True,
                "status": "active",
            }
        ],
        "last_completed_event": last_completed_event,
        "scope": {"selected": "docs", "inspected": "docs"},
        "structural_scores": {"before": 30, "after": 84},
        "hot_path_bytes": {
            "before": {
                "value": 96,
                "unit": "bytes",
                "provenance": [
                    {
                        "route": "docs/legacy.md",
                        "bytes": 96,
                        "source": "filesystem-stat",
                    }
                ],
            },
            "after": {
                "value": 128,
                "unit": "bytes",
                "provenance": [
                    {
                        "route": "docs/README.md",
                        "bytes": 64,
                        "source": "filesystem-stat",
                    },
                    {
                        "route": "docs/STATE.md",
                        "bytes": 64,
                        "source": "filesystem-stat",
                    },
                ],
            },
        },
        "trust_coverage": {
            "status": "verified",
            "numerator": 2,
            "denominator": 2,
            "routes": [
                {
                    "route": "docs/STATE.md",
                    "verified": True,
                    "freshness": "fresh",
                    "sources": [
                        "state:initialized-hot-path",
                        "state:verified-document",
                    ],
                },
                {
                    "route": "src/config.py",
                    "verified": True,
                    "freshness": "fresh",
                    "sources": ["state:verified-source"],
                },
            ],
        },
        "initialization": {
            "manifest_identity": manifest_identity,
            "result_corpus": result_corpus,
            "document_results_digest": document_results_digest,
        },
    }


def valid_state():
    draft = _state_payload()
    event = valid_event(state_semantic_digest_value=operational_state_digest(draft))
    return _state_payload(last_completed_event=event["event_id"])


def complete_init_state(root, *, manifest_identity=None, **overrides):
    baseline = valid_state()
    values = {
        "skill_version": "0.3.0",
        "selected_scope": "docs",
        "inspected_scope": "docs",
        "map_path": "docs/README.md",
        "current_truth_routes": ["docs/STATE.md"],
        "rubric_version": 2,
        "score_before": 30,
        "score_after": 84,
        "rubric_status": "needs-attention",
        "cold_paths": ["docs/archive/**"],
        "verified_documents": baseline["verified_documents"],
        "protected_intent": baseline["protected_intent"],
        "hot_path_bytes": baseline["hot_path_bytes"],
        "trust_coverage": baseline["trust_coverage"],
        "manifest_identity": manifest_identity
        or baseline["initialization"]["manifest_identity"],
        "result_corpus": baseline["initialization"]["result_corpus"],
        "document_results_digest": baseline["initialization"][
            "document_results_digest"
        ],
        "last_completed_event": baseline["last_completed_event"],
    }
    unknown = sorted(set(overrides) - set(values))
    if unknown:
        raise TypeError(f"unknown complete-state override(s): {', '.join(unknown)}")
    values.update(overrides)
    return docs_checker.build_initialization_state(root, **values)


def valid_findings():
    fingerprint = "7f2a91c4" + "a" * 56
    return {
        "schema_version": 1,
        "findings": [
            {
                "id": "DOC-7F2A91C4",
                "fingerprint": fingerprint,
                "priority": "P1",
                "status": "Proposed",
                "summary": "Maintained release instructions are unreachable from the map.",
                "why": "Readers cannot discover the maintained procedure from the entry point.",
                "evidence": [{"path": "docs/release-local.md", "line": 1}],
                "recommended_action": "Link the procedure from the how-to route.",
            }
        ],
    }


def valid_event(*, state_semantic_digest_value=None, findings_digest_value=None):
    payload = manifest_payload()
    manifest_data = canonical_bytes(payload)
    manifest_identity = hashlib.sha256(manifest_data).hexdigest()
    transition = payload["corpus_transition"]
    state_semantic_digest_value = state_semantic_digest_value or (
        operational_state_digest(_state_payload())
    )
    findings_digest_value = findings_digest_value or operational_findings_digest(
        valid_findings()
    )
    event = {
        "event_id": EVENT_ID,
        "kind": "init",
        "completed_at": "2026-07-13T12:00:00Z",
        "skill_version": "0.3.0",
        "approved_ids": [],
        "score_before": 30,
        "score_after": 84,
        "reason": "The repository had no bounded documentation entry point.",
        "summary": "Adopted the repository into the mapped documentation system.",
        "worktree_kind": "filesystem",
        "repository_identity": "1" * 64,
        "worktree_identity": "2" * 64,
        "worktree_state_identity": "3" * 64,
        "changed_paths": [
            ".diataxis/events.jsonl",
            ".diataxis/findings.json",
            "docs/README.md",
            ".diataxis/state.json",
            "manifest",
        ],
        "transaction_id": "TXN-1234567890ABCDEF",
        "transaction_schema_version": 3,
        "transaction_policy_version": "init-closeout-v3",
        "starting_digests": {
            ".diataxis/events.jsonl": "sha256:ABSENT",
            ".diataxis/findings.json": "sha256:ABSENT",
            ".diataxis/state.json": "sha256:ABSENT",
            "manifest": "sha256:ABSENT",
        },
        "state_semantic_digest": state_semantic_digest_value,
        "findings_digest": findings_digest_value,
        "transaction_targets": [
            ".diataxis/events.jsonl",
            ".diataxis/findings.json",
            ".diataxis/state.json",
            "manifest",
        ],
        "target_roles": {
            ".diataxis/events.jsonl": "event",
            ".diataxis/findings.json": "findings",
            ".diataxis/state.json": "state",
            "manifest": "manifest",
        },
        "replacement_order": [
            "manifest",
            ".diataxis/state.json",
            ".diataxis/findings.json",
            ".diataxis/events.jsonl",
        ],
        "approval_bindings": [],
        "selected_boundary": "docs",
        "visibility": ["shared"],
        "manifest": {
            "path": f".diataxis/manifests/{EVENT_ID}.json",
            "digest": "sha256:" + manifest_identity,
        },
        "manifest_digest": "sha256:" + manifest_identity,
        "manifest_schema_version": 3,
        "manifest_identity": manifest_identity,
        "approval_identity": payload["approval_identity"],
        "corpus_transition": transition,
        "corpus_transition_digest": "sha256:"
        + hashlib.sha256(canonical_bytes(transition)).hexdigest(),
        "document_results_digest": "sha256:"
        + hashlib.sha256(canonical_bytes(payload["document_results"])).hexdigest(),
    }
    identifier = "EVT-" + init_event_fingerprint(event)[:8].upper()
    event["event_id"] = identifier
    event["manifest"]["path"] = f".diataxis/manifests/{identifier}.json"
    return event


def write_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def write_events(path, events):
    path.write_text(
        "".join(json.dumps(event, sort_keys=True) + "\n" for event in events),
        encoding="utf-8",
    )


def manifest_bytes():
    return canonical_bytes(manifest_payload())


def attach_manifest(control, event, data=None, *, digest=None, relative=None):
    data = manifest_bytes() if data is None else data
    relative = relative or f".diataxis/manifests/{event['event_id']}.json"
    manifest_path = control.parent / Path(relative)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_bytes(data)
    event["manifest"] = {
        "path": relative,
        "digest": digest or "sha256:" + hashlib.sha256(data).hexdigest(),
    }
    return manifest_path


def create_repository(root):
    docs = root / "docs"
    src = root / "src"
    docs.mkdir()
    src.mkdir()
    for relative, data in BASE_DOCUMENTS.items():
        (root / relative).write_bytes(data)
    (src / "config.py").write_text("VALUE = 1\n", encoding="utf-8")
    control = root / ".diataxis"
    control.mkdir()
    state = valid_state()
    findings = valid_findings()
    event = valid_event(
        state_semantic_digest_value=operational_state_digest(state),
        findings_digest_value=operational_findings_digest(findings),
    )
    self_consistent_state = _state_payload(last_completed_event=event["event_id"])
    write_json(control / "state.json", self_consistent_state)
    write_json(control / "findings.json", findings)
    write_events(control / "events.jsonl", [event])
    manifest_path = root / event["manifest"]["path"]
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_bytes(manifest_bytes())
    return control


def file_snapshot(root):
    return {
        path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }


class RepositoryMemoryTests(unittest.TestCase):
    def test_rubric_v2_uses_exact_structural_weights(self):
        summary = docs_checker.health_summary(complete_measurements())

        self.assertEqual(summary["rubric_version"], 2)
        self.assertEqual(
            {
                name: category["weight"]
                for name, category in summary["categories"].items()
            },
            {
                "entry": 20,
                "path_safety": 15,
                "links": 20,
                "anchors": 10,
                "reachability": 25,
                "titles": 10,
            },
        )
        self.assertNotIn("hot_path", summary["categories"])
        self.assertEqual(summary["percentage"], 100)
        self.assertEqual(summary["structure_status"], "healthy")

    def test_unresolved_structural_loss_cannot_round_up_to_healthy(self):
        summary = docs_checker.health_summary(
            complete_measurements(checked_links=40, valid_links=39),
            freshness={"status": "fresh", "routes": []},
            coverage={
                "numerator": 1,
                "denominator": 1,
                "routes": [
                    {
                        "route": "docs/STATE.md",
                        "verified": True,
                        "sources": ["state:verified-document"],
                    }
                ],
            },
        )

        self.assertLess(summary["percentage"], 100)
        self.assertEqual(summary["structure_status"], "needs-attention")
        self.assertEqual(summary["verdict"], "needs-attention")

    def test_local_map_bytes_must_match_latest_event_reference(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            control = create_repository(root)
            local_map = control / "local-map.json"
            original = b'{"schema_version":1}\n'
            local_map.write_bytes(original)
            event = valid_event()
            event["local_map_digest"] = "sha256:" + hashlib.sha256(original).hexdigest()
            write_events(control / "events.jsonl", [event])

            local_map.write_bytes(b'{"schema_version":2}\n')
            findings = docs_checker.inspect_operational_memory(root)

            self.assertTrue(
                any(
                    item["path"] == ".diataxis/local-map.json"
                    and item["detail"]
                    == "local map does not match its verified event reference"
                    for item in findings
                )
            )

    def test_stub_map_cannot_earn_reachability_or_navigation_credit(self):
        measurements = complete_measurements(
            map_has_body=False,
            map_has_h2=False,
            maintained_files=1,
            maintained_paths=1,
            safe_maintained_paths=1,
            checked_links=0,
            valid_links=0,
            valid_navigation_routes=0,
            reachable_files=1,
            usable_unique_titles=1,
            hot_bytes=10,
            hot_path_files=[{"path": "docs/README.md", "bytes": 10}],
        )

        summary = docs_checker.health_summary(measurements)

        self.assertEqual(summary["categories"]["links"]["earned"], 0)
        self.assertEqual(summary["categories"]["anchors"]["earned"], 0)
        self.assertEqual(summary["categories"]["reachability"]["earned"], 0)
        self.assertLess(summary["percentage"], 40)

    def test_complete_single_document_entry_is_useful_without_self_links(self):
        measurements = complete_measurements(
            maintained_files=1,
            maintained_paths=1,
            safe_maintained_paths=1,
            checked_links=0,
            valid_links=0,
            valid_navigation_routes=0,
            reachable_files=1,
            usable_unique_titles=1,
            hot_path_files=[{"path": "docs/README.md", "bytes": 128}],
        )

        summary = docs_checker.health_summary(measurements)

        self.assertEqual(summary["percentage"], 100)
        self.assertEqual(summary["categories"]["entry"]["earned"], 20)
        self.assertEqual(summary["categories"]["reachability"]["earned"], 25)

    def test_hot_path_bytes_are_provenance_tagged_telemetry_only(self):
        small = docs_checker.health_summary(
            complete_measurements(
                hot_bytes=1,
                hot_path_files=[{"path": "docs/README.md", "bytes": 1}],
            )
        )
        large = docs_checker.health_summary(
            complete_measurements(
                hot_bytes=10_000_000,
                hot_path_files=[
                    {"path": "docs/README.md", "bytes": 10_000_000}
                ],
            )
        )

        self.assertEqual(small["percentage"], large["percentage"])
        self.assertEqual(small["structure_status"], large["structure_status"])
        self.assertEqual(small["verdict"], large["verdict"])
        self.assertEqual(large["hot_path_bytes"]["value"], 10_000_000)
        self.assertEqual(
            large["hot_path_bytes"]["provisional_target_bytes"], 16_384
        )
        self.assertIn("provenance", large["hot_path_bytes"])
        self.assertNotIn("limit", large["hot_path_bytes"])

    def test_p0_blocks_trust_without_changing_structural_percentage(self):
        self.assertIn(
            "findings", inspect.signature(docs_checker.health_summary).parameters
        )
        summary = docs_checker.health_summary(
            complete_measurements(),
            findings=[finding(priority="P0")],
            freshness={"status": "fresh", "routes": []},
            coverage={
                "numerator": 1,
                "denominator": 1,
                "routes": [
                    {
                        "route": "docs/STATE.md",
                        "verified": True,
                        "freshness": "fresh",
                        "sources": ["state:verified-document"],
                    }
                ],
            },
        )

        self.assertEqual(summary["percentage"], 100)
        self.assertEqual(summary["structure_status"], "healthy")
        self.assertEqual(summary["trust_status"], "blocked")
        self.assertEqual(summary["open_priorities"]["P0"], 1)
        self.assertEqual(summary["verdict"], "blocked")

    def test_trust_union_is_normalized_deduplicated_and_provenance_complete(self):
        self.assertTrue(hasattr(docs_checker, "evaluate_coverage"))
        coverage = docs_checker.evaluate_coverage(
            configured_routes=["docs\\STATE.md", "docs/./STATE.md"],
            state={
                "initialized": {
                    "hot_paths": ["docs/STATE.md", "docs/GUIDE.md"]
                },
                "verified_documents": [
                    verified_document_fixture()
                ],
            },
            map_routes=[
                {"route": "docs/./GUIDE.md", "marker": "current"},
                {"route": "docs/API.md", "marker": "authoritative"},
            ],
            freshness={
                "status": "fresh",
                "routes": [
                    {"route": "docs/STATE.md", "status": "fresh"},
                    {"route": "src/config.py", "status": "fresh"},
                ],
            },
        )

        self.assertEqual(coverage["numerator"], 2)
        self.assertEqual(coverage["denominator"], 4)
        self.assertEqual(
            [row["route"] for row in coverage["routes"]],
            ["docs/API.md", "docs/GUIDE.md", "docs/STATE.md", "src/config.py"],
        )
        state_row = next(
            row for row in coverage["routes"] if row["route"] == "docs/STATE.md"
        )
        self.assertEqual(
            state_row["sources"],
            [
                "configured:hot-path",
                "state:initialized-hot-path",
                "state:verified-document",
            ],
        )

    def test_trust_union_uses_filesystem_identity_across_declaration_sources(self):
        coverage = docs_checker.evaluate_coverage(
            configured_routes=["docs/STATE.md"],
            state={
                "initialized": {"hot_paths": ["DOCS/state.md"]},
                "verified_documents": [verified_document_fixture()],
            },
            map_routes=[{"route": "docs/STATE.md", "marker": "current"}],
            freshness={
                "status": "fresh",
                "routes": [
                    {"route": "docs/STATE.md", "status": "fresh"},
                    {"route": "src/config.py", "status": "fresh"},
                ],
            },
        )
        same_identity = (
            docs_checker._path_identity("docs/STATE.md")
            == docs_checker._path_identity("DOCS/state.md")
        )
        if same_identity:
            self.assertEqual(
                (
                    coverage["numerator"],
                    coverage["denominator"],
                    coverage["status"],
                ),
                (2, 2, "verified"),
            )
            state_rows = [
                row
                for row in coverage["routes"]
                if docs_checker._path_identity(row["route"])
                == docs_checker._path_identity("docs/STATE.md")
            ]
            self.assertEqual(len(state_rows), 1)
            self.assertEqual(state_rows[0]["freshness"], "fresh")
            self.assertEqual(
                state_rows[0]["sources"],
                [
                    "configured:hot-path",
                    "map:current",
                    "state:initialized-hot-path",
                    "state:verified-document",
                ],
            )
        else:
            self.assertEqual(
                (
                    coverage["numerator"],
                    coverage["denominator"],
                    coverage["status"],
                ),
                (2, 3, "partial"),
            )
            alias_row = next(
                row for row in coverage["routes"] if row["route"] == "DOCS/state.md"
            )
            self.assertEqual(alias_row["freshness"], "unverified")
            self.assertEqual(alias_row["sources"], ["state:initialized-hot-path"])

    def test_exact_map_current_marker_adds_only_its_valid_route_to_trust(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            create_repository(root)
            (root / "docs" / "GUIDE.md").write_text("# Guide\n", encoding="utf-8")
            (root / "docs" / "README.md").write_text(
                "# Documentation\n\n"
                "Start here.\n\n"
                "[Guide](GUIDE.md) <!-- docs:authoritative -->\n"
                "[Trailing](STATE.md) <!-- docs:current --> trailing prose\n"
                "[Ordinary](DESIGN.md)\n",
                encoding="utf-8",
            )

            proc = subprocess.run(
                [
                    sys.executable,
                    str(SKILL / "scripts" / "check.py"),
                    str(root),
                    "--json",
                    "--agent",
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            routes = {
                row["route"]: row["sources"]
                for row in json.loads(proc.stdout)["health"]["coverage"]["routes"]
            }

            self.assertIn("docs/GUIDE.md", routes)
            self.assertEqual(routes["docs/GUIDE.md"], ["map:authoritative"])
            self.assertNotIn("map:current", routes["docs/STATE.md"])
            self.assertNotIn("map:current", routes.get("docs/DESIGN.md", []))

    def test_unsafe_map_current_marker_is_a_finding_not_a_trust_route(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "docs").mkdir()
            (root / "outside.md").write_text("# Outside\n", encoding="utf-8")
            (root / "docs" / "README.md").write_text(
                "# Documentation\n\n"
                "[Outside](../../outside.md) <!-- docs:current -->\n",
                encoding="utf-8",
            )

            proc = subprocess.run(
                [
                    sys.executable,
                    str(SKILL / "scripts" / "check.py"),
                    str(root),
                    "--json",
                    "--agent",
                ],
                capture_output=True,
                text=True,
            )

            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertIn("outside-link", [row["kind"] for row in payload["findings"]])
            self.assertEqual(payload["health"]["coverage"]["denominator"], 0)

    def test_trust_precedence_and_empty_union_are_explicit(self):
        self.assertIn(
            "freshness", inspect.signature(docs_checker.health_summary).parameters
        )
        empty = docs_checker.health_summary(
            complete_measurements(),
            freshness={"status": "unverified", "routes": []},
            coverage={"numerator": 0, "denominator": 0, "routes": []},
        )
        self.assertEqual(empty["trust_status"], "unverified")
        self.assertNotEqual(empty["verdict"], "healthy")

        partial_coverage = {
            "numerator": 0,
            "denominator": 1,
            "routes": [
                {
                    "route": "docs/STATE.md",
                    "verified": False,
                    "freshness": "unverified",
                    "sources": ["configured:hot-path"],
                }
            ],
        }
        partial = docs_checker.health_summary(
            complete_measurements(),
            freshness={"status": "fresh", "routes": []},
            coverage=partial_coverage,
        )
        stale = docs_checker.health_summary(
            complete_measurements(),
            freshness={"status": "stale", "routes": []},
            coverage=partial_coverage,
        )
        blocked = docs_checker.health_summary(
            complete_measurements(),
            findings=[finding(priority="P0")],
            freshness={"status": "stale", "routes": []},
            coverage=partial_coverage,
        )
        self.assertEqual(partial["trust_status"], "partial")
        self.assertEqual(stale["trust_status"], "stale")
        self.assertEqual(blocked["trust_status"], "blocked")

    def test_p1_prevents_overall_healthy_while_preserving_verified_trust(self):
        self.assertIn(
            "findings", inspect.signature(docs_checker.health_summary).parameters
        )
        coverage = {
            "numerator": 1,
            "denominator": 1,
            "routes": [
                {
                    "route": "docs/STATE.md",
                    "verified": True,
                    "freshness": "fresh",
                    "sources": ["state:verified-document"],
                }
            ],
        }
        summary = docs_checker.health_summary(
            complete_measurements(),
            findings=[finding(priority="P1")],
            freshness={"status": "fresh", "routes": []},
            coverage=coverage,
        )

        self.assertEqual(summary["trust_status"], "verified")
        self.assertEqual(summary["structure_status"], "healthy")
        self.assertEqual(summary["verdict"], "needs-attention")

    def test_text_digest_is_cross_platform_newline_and_nfc_stable(self):
        self.assertTrue(hasattr(docs_checker, "normalized_content_digest"))
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            lf = root / "lf.txt"
            crlf = root / "crlf.txt"
            decomposed = root / "decomposed.txt"
            lf.write_bytes("alpha\nbéta\n".encode("utf-8"))
            crlf.write_bytes("alpha\r\nbéta\r\n".encode("utf-8"))
            decomposed.write_bytes("alpha\r\nbe\u0301ta\r\n".encode("utf-8"))

            expected = docs_checker.normalized_content_digest(lf)
            self.assertEqual(expected, docs_checker.normalized_content_digest(crlf))
            self.assertEqual(expected, docs_checker.normalized_content_digest(decomposed))

    def test_non_utf8_digest_uses_bytes_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "binary.dat"
            path.write_bytes(b"\xff\x00\r\n")

            digest = docs_checker.normalized_content_digest(path)

            self.assertEqual(
                digest,
                "sha256-bytes:" + hashlib.sha256(b"\xff\x00\r\n").hexdigest(),
            )

    def test_freshness_union_uses_filesystem_identity_and_merges_provenance(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "docs").mkdir()
            (root / "docs" / "STATE.md").write_text("# State\n", encoding="utf-8")
            declaration = {
                "document": "docs/STATE.md",
                "digest": DIGEST_A,
                "sources": [{"path": "DOCS/state.md", "digest": DIGEST_A}],
                "verified_event": EVENT_ID,
            }

            freshness = docs_checker.evaluate_freshness(root, [declaration])
            same_identity = (
                docs_checker._path_identity("docs/STATE.md")
                == docs_checker._path_identity("DOCS/state.md")
            )

            if same_identity:
                self.assertEqual(freshness["status"], "fresh")
                self.assertEqual(len(freshness["routes"]), 1)
                self.assertEqual(
                    freshness["routes"][0]["provenance"],
                    ["state:verified-document", "state:verified-source"],
                )
            else:
                self.assertEqual(freshness["status"], "stale")
                self.assertEqual(len(freshness["routes"]), 2)

    def test_freshness_rejects_traversal_and_reparse_routes_without_outside_reads(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "docs").mkdir()
            outside = root / "outside.md"
            outside.write_text("# Outside\n", encoding="utf-8")
            traversal = verified_document_fixture(document="../outside.md")

            with self.assertRaises(ValueError):
                docs_checker.evaluate_freshness(root / "docs", [traversal])

            link = root / "docs" / "STATE.md"
            try:
                link.symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("file symlinks unavailable")
            before = (outside.read_bytes(), outside.stat().st_mtime_ns)
            with self.assertRaises(ValueError):
                docs_checker.evaluate_freshness(root, [verified_document_fixture()])
            self.assertEqual((outside.read_bytes(), outside.stat().st_mtime_ns), before)

    def test_changed_verified_source_is_stale_without_score_change(self):
        self.assertTrue(hasattr(docs_checker, "evaluate_freshness"))
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "docs").mkdir()
            (root / "src").mkdir()
            document = root / "docs" / "STATE.md"
            source = root / "src" / "config.py"
            document.write_text("# State\n", encoding="utf-8")
            source.write_text("VALUE = 2\n", encoding="utf-8")
            baseline = verified_document_fixture(source_digest=DIGEST_B)

            freshness = docs_checker.evaluate_freshness(root, [baseline])
            structural = docs_checker.health_summary(complete_measurements())
            stale = docs_checker.health_summary(
                complete_measurements(), freshness=freshness
            )

            self.assertEqual(freshness["status"], "stale")
            self.assertEqual(freshness["findings"][0]["kind"], "stale-evidence")
            self.assertEqual(stale["percentage"], structural["percentage"])
            self.assertEqual(stale["trust_status"], "stale")

    def test_missing_verified_content_has_stable_stale_evidence_id(self):
        self.assertTrue(hasattr(docs_checker, "evaluate_freshness"))
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "docs").mkdir()
            (root / "src").mkdir()
            (root / "src" / "config.py").write_text(
                "VALUE = 1\n", encoding="utf-8"
            )
            baseline = verified_document_fixture()

            first = docs_checker.evaluate_freshness(root, [baseline])
            second = docs_checker.evaluate_freshness(root, [baseline])

            first_finding = next(
                item for item in first["findings"] if item["path"] == "docs/STATE.md"
            )
            second_finding = next(
                item for item in second["findings"] if item["path"] == "docs/STATE.md"
            )
            self.assertEqual(first_finding["id"], second_finding["id"])
            self.assertEqual(first_finding["fingerprint"], second_finding["fingerprint"])

    def test_cold_route_reachable_from_map_is_deterministic_conflict(self):
        first = run_checker_fixture(
            cold_paths=["docs/archive/**"],
            map_links=["archive/current.md"],
        )
        second = run_checker_fixture(
            cold_paths=["docs/archive/**"],
            map_links=["archive/current.md"],
        )

        first_conflicts = [
            item for item in first["findings"] if item["kind"] == "cold-current-conflict"
        ]
        second_conflicts = [
            item for item in second["findings"] if item["kind"] == "cold-current-conflict"
        ]
        self.assertEqual(len(first_conflicts), 1)
        self.assertEqual(first_conflicts[0]["id"], second_conflicts[0]["id"])

    def test_cold_route_named_by_hot_sources_anchor_is_a_conflict(self):
        payload = run_checker_fixture(
            cold_paths=["docs/archive/**"],
            hot_sources=["docs/archive/current.md"],
        )

        self.assertIn(
            "cold-current-conflict",
            [item["kind"] for item in payload["findings"]],
        )

    def test_cold_patterns_support_valid_character_classes(self):
        self.assertTrue(
            docs_checker.route_matches_patterns(
                "docs/archive/a.md", ["docs/archive/[ab].md"]
            )
        )
        self.assertFalse(
            docs_checker.route_matches_patterns(
                "docs/archive/c.md", ["docs/archive/[ab].md"]
            )
        )

    def test_event_id_ignores_timestamp_and_set_order_but_changes_with_semantics(self):
        self.assertTrue(hasattr(docs_checker, "event_fingerprint"))
        self.assertTrue(hasattr(docs_checker, "event_id"))
        event = valid_event()
        moved = dict(event)
        moved["completed_at"] = "2030-01-01T00:00:00Z"
        moved["changed_paths"] = list(reversed(event["changed_paths"]))
        changed = dict(event)
        changed["kind"] = "fix"

        first_fingerprint = docs_checker.event_fingerprint(event)
        moved_fingerprint = docs_checker.event_fingerprint(moved)
        changed_fingerprint = docs_checker.event_fingerprint(changed)

        self.assertEqual(first_fingerprint, moved_fingerprint)
        self.assertEqual(
            docs_checker.event_id(first_fingerprint, {}),
            docs_checker.event_id(moved_fingerprint, {}),
        )
        self.assertNotEqual(first_fingerprint, changed_fingerprint)
        self.assertRegex(
            docs_checker.event_id(first_fingerprint, {}), r"^EVT-[0-9A-F]+$"
        )

    def test_event_id_extends_on_hash_prefix_collision(self):
        first = "7f2a91c4" + "a" * 56
        second = "7f2a91c4" + "b" * 56

        identifier = docs_checker.event_id(first, {})
        extended = docs_checker.event_id(second, {identifier: first})

        self.assertEqual(identifier, "EVT-7F2A91C4")
        self.assertEqual(extended, "EVT-7F2A91C4BBBB")

    def test_duplicate_event_timestamp_movement_is_not_a_conflict(self):
        event = {
            "event_id": EVENT_ID,
            "kind": "fix",
            "completed_at": "2026-07-13T12:00:00Z",
            "changed_paths": ["docs/README.md"],
            "summary": "Refresh the documentation map.",
        }
        self.assertTrue(hasattr(docs_checker, "event_fingerprint"))
        self.assertTrue(hasattr(docs_checker, "event_id"))
        event["event_id"] = docs_checker.event_id(
            docs_checker.event_fingerprint(event), {}
        )
        moved = dict(event, completed_at="2030-01-01T00:00:00Z")

        findings = docs_checker.validate_operational_events([event, moved])

        self.assertEqual(findings, [])

    def test_valid_state_is_loaded_without_writing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            control = create_repository(root)
            state_path = control / "state.json"
            before = state_path.read_bytes()

            loaded = docs_checker.load_operational_state(root)

            self.assertEqual(loaded["schema_version"], 3)
            self.assertEqual(loaded["initialized"]["map"], "docs/README.md")
            self.assertEqual(
                loaded["initialization"]["result_corpus"], corpus_v3()
            )
            self.assertEqual(state_path.read_bytes(), before)

    def test_schema1_and_schema2_state_are_explicitly_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            create_repository(root)
            for version in (1, 2):
                with self.subTest(schema_version=version):
                    state = json.loads(json.dumps(valid_state()))
                    state["schema_version"] = version
                    with self.assertRaisesRegex(ValueError, "unsupported"):
                        docs_checker.validate_operational_state(state, root)

    def test_complete_init_state_records_and_strictly_validates_verified_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            create_repository(root)
            state = complete_init_state(root)
            normalized = docs_checker.validate_operational_state(state, root)

            self.assertEqual(normalized, state)
            self.assertEqual(state["schema_version"], 3)
            self.assertEqual(state["scope"], {"selected": "docs", "inspected": "docs"})
            self.assertEqual(state["structural_scores"], {"before": 30, "after": 84})
            self.assertEqual(state["hot_path_bytes"]["after"]["value"], 128)
            self.assertEqual(state["trust_coverage"]["numerator"], 2)
            self.assertEqual(state["trust_coverage"]["denominator"], 2)
            self.assertEqual(
                state["initialization"]["manifest_identity"],
                hashlib.sha256(manifest_bytes()).hexdigest(),
            )
            self.assertEqual(
                state["initialization"]["result_corpus"], corpus_v3()
            )
            self.assertNotIn(str(root), json.dumps(state, sort_keys=True))

            invalid_states = []
            extra = json.loads(json.dumps(state))
            extra["private_topic"] = "release-alias"
            invalid_states.append(extra)
            absolute = json.loads(json.dumps(state))
            absolute["initialized"]["hot_paths"] = ["C:/private/checkout/docs"]
            invalid_states.append(absolute)
            for path in (".local/private-plan.md", ".LOCAL/private-plan.md"):
                private = json.loads(json.dumps(state))
                private["verified_documents"][0]["sources"][0]["path"] = path
                invalid_states.append(private)
            mismatch = json.loads(json.dumps(state))
            mismatch["scope"]["inspected"] = "."
            invalid_states.append(mismatch)
            score = json.loads(json.dumps(state))
            score["structural_scores"]["after"] = 101
            invalid_states.append(score)
            byte_provenance = json.loads(json.dumps(state))
            byte_provenance["hot_path_bytes"]["after"]["value"] += 1
            invalid_states.append(byte_provenance)
            trust = json.loads(json.dumps(state))
            trust["trust_coverage"]["numerator"] = True
            invalid_states.append(trust)
            manifest = json.loads(json.dumps(state))
            manifest["initialization"]["manifest_identity"] = "SHA256:" + "A" * 64
            invalid_states.append(manifest)
            corpus = json.loads(json.dumps(state))
            corpus["initialization"]["result_corpus"]["legacy"] = True
            invalid_states.append(corpus)
            results = json.loads(json.dumps(state))
            results["initialization"]["document_results_digest"] = (
                "sha256:" + "a" * 63
            )
            invalid_states.append(results)

            for invalid in invalid_states:
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    docs_checker.validate_operational_state(invalid, root)

    def test_state_v3_cross_binds_after_bytes_and_trust_to_declared_routes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            create_repository(root)
            state = complete_init_state(root)
            self.assertEqual(docs_checker.validate_operational_state(state, root), state)

            invalid_states = []
            empty_after = json.loads(json.dumps(state))
            empty_after["hot_path_bytes"]["after"] = {
                "value": 0,
                "unit": "bytes",
                "provenance": [],
            }
            invalid_states.append(empty_after)
            missing_after = json.loads(json.dumps(state))
            missing_after["hot_path_bytes"]["after"] = {
                "value": 64,
                "unit": "bytes",
                "provenance": [
                    {
                        "route": "docs/README.md",
                        "bytes": 64,
                        "source": "filesystem-stat",
                    }
                ],
            }
            invalid_states.append(missing_after)
            extra_after = json.loads(json.dumps(state))
            extra_after["hot_path_bytes"]["after"]["value"] += 1
            extra_after["hot_path_bytes"]["after"]["provenance"].append(
                {"route": "src/config.py", "bytes": 1, "source": "filesystem-stat"}
            )
            invalid_states.append(extra_after)
            empty_trust = json.loads(json.dumps(state))
            empty_trust["trust_coverage"] = {
                "status": "unverified",
                "numerator": 0,
                "denominator": 0,
                "routes": [],
            }
            invalid_states.append(empty_trust)
            missing_trust = json.loads(json.dumps(state))
            missing_trust["trust_coverage"]["routes"] = missing_trust[
                "trust_coverage"
            ]["routes"][1:]
            missing_trust["trust_coverage"]["numerator"] = 1
            missing_trust["trust_coverage"]["denominator"] = 1
            invalid_states.append(missing_trust)
            fabricated = json.loads(json.dumps(state))
            fabricated["trust_coverage"]["routes"][0]["sources"].append(
                "state:verified-source"
            )
            invalid_states.append(fabricated)
            invented_map = json.loads(json.dumps(state))
            invented_map["trust_coverage"]["routes"][0]["sources"].append(
                "state:initialized-map"
            )
            invalid_states.append(invented_map)

            for invalid in invalid_states:
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    docs_checker.validate_operational_state(invalid, root)

    def test_initialization_state_accepts_canonical_evaluate_coverage_output(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            create_repository(root)
            source_state = valid_state()
            coverage = docs_checker.evaluate_coverage(
                state={
                    "initialized": source_state["initialized"],
                    "verified_documents": source_state["verified_documents"],
                },
                freshness={
                    "status": "fresh",
                    "routes": [
                        {"route": "docs/STATE.md", "status": "fresh"},
                        {"route": "src/config.py", "status": "fresh"},
                    ],
                },
            )

            self.assertEqual(
                [row["route"] for row in coverage["routes"]],
                ["docs/STATE.md", "src/config.py"],
            )
            state = complete_init_state(root, trust_coverage=coverage)
            self.assertEqual(state["trust_coverage"], coverage)
            self.assertEqual(docs_checker.validate_operational_state(state, root), state)

    def test_state_paths_are_normalized_and_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            create_repository(root)
            state = valid_state()
            state["initialized"]["map"] = "docs\\.\\README.md"
            normalized = docs_checker.validate_operational_state(state, root)
            self.assertEqual(normalized["initialized"]["map"], "docs/README.md")

            state["initialized"]["map"] = "../outside.md"
            with self.assertRaises(ValueError):
                docs_checker.validate_operational_state(state, root)

    def test_finding_id_is_stable_without_persistence(self):
        fingerprint = docs_checker.finding_fingerprint(
            "unreachable", [{"path": "docs/release.md", "target": "docs/README.md"}]
        )

        self.assertEqual(
            docs_checker.finding_id(fingerprint, {}),
            "DOC-" + fingerprint[:8].upper(),
        )
        self.assertEqual(
            docs_checker.finding_id(fingerprint, {}),
            docs_checker.finding_id(fingerprint, {}),
        )

    def test_line_movement_and_volatile_metadata_preserve_finding_identity(self):
        first = docs_checker.finding_fingerprint(
            "missing-link",
            [
                {
                    "path": "docs\\release.md",
                    "target": "docs/README.md",
                    "line": 12,
                    "byte_offset": 340,
                    "timestamp": "2026-07-13T12:00:00Z",
                    "priority": "P0",
                    "status": "Proposed",
                    "message": "First prose description.",
                }
            ],
        )
        moved = docs_checker.finding_fingerprint(
            "missing-link",
            [
                {
                    "path": "docs/release.md",
                    "target": "docs/./README.md",
                    "line": 98,
                    "byte_offset": 6400,
                    "timestamp": "2027-01-01T00:00:00Z",
                    "priority": "P2",
                    "status": "Parked",
                    "message": "Different prose description.",
                }
            ],
        )

        self.assertEqual(first, moved)
        self.assertEqual(docs_checker.finding_id(first, {}), docs_checker.finding_id(moved, {}))

    def test_true_semantic_identity_change_changes_finding_id(self):
        first = docs_checker.finding_fingerprint(
            "missing-link", [{"path": "docs/release.md", "target": "docs/README.md"}]
        )
        changed = docs_checker.finding_fingerprint(
            "missing-link", [{"path": "docs/release.md", "target": "docs/START.md"}]
        )

        self.assertNotEqual(first, changed)
        self.assertNotEqual(docs_checker.finding_id(first, {}), docs_checker.finding_id(changed, {}))

    def test_evidence_order_and_absolute_checkout_metadata_do_not_change_fingerprint(self):
        evidence = [
            {"path": "docs/a.md", "target": "docs/README.md"},
            {"path": "docs/b.md", "target": "docs/README.md"},
        ]
        first = docs_checker.finding_fingerprint(
            "unreachable",
            [
                {**evidence[0], "absolute_path": "C:/checkout-one/docs/a.md"},
                evidence[1],
            ],
        )
        second = docs_checker.finding_fingerprint(
            "unreachable",
            [
                evidence[1],
                {**evidence[0], "absolute_path": "D:/checkout-two/docs/a.md"},
            ],
        )

        self.assertEqual(first, second)

    def test_short_id_collision_extends_instead_of_aliasing(self):
        existing = {"DOC-7F2A91C4": "7f2a91c4aaaaaaaa"}

        self.assertEqual(
            docs_checker.finding_id("7f2a91c4bbbbbbbb", existing),
            "DOC-7F2A91C4BBBB",
        )

    def test_existing_extended_id_is_reused_for_the_same_fingerprint(self):
        fingerprint = "7f2a91c4bbbbbbbb"
        existing = {
            "DOC-7F2A91C4": "7f2a91c4aaaaaaaa",
            "DOC-7F2A91C4BBBB": fingerprint,
        }

        self.assertEqual(
            docs_checker.finding_id(fingerprint, existing),
            "DOC-7F2A91C4BBBB",
        )

    def test_valid_findings_and_events_are_loaded_without_writing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            create_repository(root)
            before = file_snapshot(root / ".diataxis")

            findings = docs_checker.load_operational_findings(root)
            events = docs_checker.load_operational_events(root)

            self.assertEqual(findings["findings"][0]["id"], "DOC-7F2A91C4")
            self.assertEqual(events[0]["event_id"], valid_event()["event_id"])
            self.assertEqual(file_snapshot(root / ".diataxis"), before)

    def test_finding_evidence_normalizes_every_path_bearing_identity_field(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            control = create_repository(root)
            payload = valid_findings()
            payload["findings"][0]["evidence"] = [
                {
                    "path": "docs\\.\\release-local.md",
                    "map": "docs\\README.md",
                    "document": "docs\\STATE.md",
                    "source": "src\\.\\config.py",
                    "destination": "docs\\archive\\release-local.md",
                    "target": "docs\\.\\README.md#documentation",
                    "paths": ["docs\\STATE.md", "docs/./DESIGN.md"],
                    "targets": ["docs\\STATE.md", "docs/./DESIGN.md#visual-language"],
                    "sources": ["src\\config.py", "docs/./STATE.md"],
                }
            ]
            write_json(control / "findings.json", payload)

            evidence = docs_checker.load_operational_findings(root)["findings"][0]["evidence"][0]

            self.assertEqual(evidence["path"], "docs/release-local.md")
            self.assertEqual(evidence["map"], "docs/README.md")
            self.assertEqual(evidence["document"], "docs/STATE.md")
            self.assertEqual(evidence["source"], "src/config.py")
            self.assertEqual(evidence["destination"], "docs/archive/release-local.md")
            self.assertEqual(evidence["target"], "docs/README.md#documentation")
            self.assertEqual(evidence["paths"], ["docs/STATE.md", "docs/DESIGN.md"])
            self.assertEqual(
                evidence["targets"],
                ["docs/STATE.md", "docs/DESIGN.md#visual-language"],
            )
            self.assertEqual(evidence["sources"], ["src/config.py", "docs/STATE.md"])

    def test_finding_evidence_rejects_escape_in_every_path_bearing_identity_field(self):
        cases = {
            "path": "../outside.md",
            "map": "../outside.md",
            "document": "../outside.md",
            "source": "../outside.md",
            "destination": "../outside.md",
            "target": "../outside.md#section",
            "paths": ["docs/STATE.md", "../outside.md"],
            "targets": ["docs/STATE.md", "../outside.md#section"],
            "sources": ["src/config.py", "../outside.py"],
        }
        for field, value in cases.items():
            with self.subTest(field=field), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                control = create_repository(root)
                payload = valid_findings()
                evidence = {"path": "docs/release-local.md", field: value}
                payload["findings"][0]["evidence"] = [evidence]
                write_json(control / "findings.json", payload)

                findings, _ = docs_checker.check(root)

                self.assertTrue(
                    any(
                        item["kind"] == "state-conflict"
                        and item["priority"] == "P0"
                        and item["path"] == ".diataxis/findings.json"
                        for item in findings
                    )
                )

    def test_event_manifest_route_must_remain_in_the_committed_control_plane(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            control = create_repository(root)
            event = valid_event()
            event["manifest"] = {
                "path": "../outside.json",
                "digest": "sha256:" + "c" * 64,
            }
            write_events(control / "events.jsonl", [event])
            before = file_snapshot(control)

            with self.assertRaises(ValueError):
                docs_checker.load_operational_events(root)
            findings, _ = docs_checker.check(root)
            self.assertTrue(
                any(item["kind"] == "state-conflict" and item["priority"] == "P0" for item in findings)
            )
            self.assertEqual(file_snapshot(control), before)

    def test_referenced_manifest_is_loaded_and_validated_without_writing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            control = create_repository(root)
            event = valid_event()
            manifest_path = attach_manifest(control, event)
            write_events(control / "events.jsonl", [event])
            before = file_snapshot(control)

            events = docs_checker.load_operational_events(root)

            self.assertEqual(events[0]["manifest"], event["manifest"])
            self.assertEqual(manifest_path.read_bytes(), manifest_bytes())
            self.assertEqual(file_snapshot(control), before)

    def test_missing_manifest_is_a_deterministic_state_conflict(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            control = create_repository(root)
            event = valid_event()
            missing_id = event["event_id"]
            event["manifest"] = {
                "path": f".diataxis/manifests/{missing_id}.json",
                "digest": "sha256:" + "c" * 64,
            }
            write_events(control / "events.jsonl", [event])
            before = file_snapshot(control)

            first, _ = docs_checker.check(root)
            second, _ = docs_checker.check(root)
            first_conflicts = [
                item for item in first if item["kind"] == "state-conflict" and "manifest" in item["path"]
            ]
            second_conflicts = [
                item for item in second if item["kind"] == "state-conflict" and "manifest" in item["path"]
            ]

            self.assertEqual(len(first_conflicts), 1)
            self.assertEqual(len(second_conflicts), 1)
            first_conflict = first_conflicts[0]
            second_conflict = second_conflicts[0]
            self.assertEqual(first_conflict["priority"], "P0")
            self.assertEqual(first_conflict["id"], second_conflict["id"])
            self.assertEqual(first_conflict["fingerprint"], second_conflict["fingerprint"])
            self.assertEqual(file_snapshot(control), before)

    def test_manifest_digest_mismatch_is_state_conflict(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            control = create_repository(root)
            event = valid_event()
            attach_manifest(control, event, digest="sha256:" + "0" * 64)
            write_events(control / "events.jsonl", [event])

            findings, _ = docs_checker.check(root)

            self.assertTrue(
                any(
                    item["kind"] == "state-conflict"
                    and item["priority"] == "P0"
                    and "manifest" in item["path"]
                    for item in findings
                )
            )

    def test_oversized_manifest_is_memory_capacity_without_truncation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            control = create_repository(root)
            event = valid_event()
            data = b"x" * (MAX_MANIFEST_BYTES + 1)
            manifest_path = attach_manifest(control, event, data)
            write_events(control / "events.jsonl", [event])
            before = manifest_path.read_bytes()

            findings, _ = docs_checker.check(root)

            self.assertTrue(
                any(
                    item["kind"] == "memory-capacity"
                    and item["priority"] == "P1"
                    and item["path"] == event["manifest"]["path"]
                    for item in findings
                )
            )
            self.assertEqual(manifest_path.read_bytes(), before)

    def test_malformed_duplicate_merge_and_deep_manifests_are_state_conflicts(self):
        deep = b'{"dispositions":' + b"[" * 1500 + b"0" + b"]" * 1500 + b"}"
        cases = {
            "malformed": b'{"dispositions":',
            "duplicate": b'{"dispositions":[],"dispositions":[]}',
            "merge": b'<<<<<<< ours\n{}\n=======\n{}\n>>>>>>> theirs\n',
            "deep": deep,
        }
        for name, data in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                control = create_repository(root)
                event = valid_event()
                attach_manifest(control, event, data)
                write_events(control / "events.jsonl", [event])

                findings, _ = docs_checker.check(root)

                self.assertTrue(
                    any(
                        item["kind"] == "state-conflict"
                        and item["priority"] == "P0"
                        and "manifest" in item["path"]
                        for item in findings
                    )
                )

    def test_reparse_manifest_is_rejected_without_reading_outside_content(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            control = create_repository(root)
            outside = root / "outside-manifest.json"
            outside.write_bytes(manifest_bytes())
            event = valid_event()
            relative = f".diataxis/manifests/{EVENT_ID}.json"
            manifest_path = control.parent / relative
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                manifest_path.symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("file symlinks unavailable")
            event["manifest"] = {
                "path": relative,
                "digest": "sha256:" + hashlib.sha256(outside.read_bytes()).hexdigest(),
            }
            write_events(control / "events.jsonl", [event])
            outside_before = (outside.read_bytes(), outside.stat().st_mtime_ns)

            findings, _ = docs_checker.check(root)

            self.assertTrue(
                any(item["kind"] == "state-conflict" and item["priority"] == "P0" for item in findings)
            )
            self.assertEqual((outside.read_bytes(), outside.stat().st_mtime_ns), outside_before)

    def test_persisted_event_id_mismatch_is_state_conflict(self):
        event = valid_event()
        event["event_id"] = "EVT-00000000"
        event["manifest"]["path"] = ".diataxis/manifests/EVT-00000000.json"
        findings = docs_checker.validate_operational_events([event])

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["kind"], "state-conflict")
        self.assertEqual(findings[0]["priority"], "P0")
        self.assertEqual(findings[0]["detail"], "operational event is invalid")

    def test_conflicting_duplicate_event_id_is_state_conflict(self):
        events = [
            {"event_id": EVENT_ID, "kind": "fix"},
            {"event_id": EVENT_ID, "kind": "update"},
        ]
        collision = [
            "93a10aff" + "a" * 56,
            "93a10aff" + "b" * 56,
        ]

        with mock.patch(
            "_docs_checker.memory.event_fingerprint", side_effect=collision
        ) as fingerprint:
            findings = docs_checker.validate_operational_events(events)

        duplicate = [
            finding
            for finding in findings
            if "duplicate event ID has conflicting payloads" in finding["detail"]
        ]
        self.assertEqual(fingerprint.call_count, 2)
        self.assertEqual(len(duplicate), 1)
        self.assertEqual(duplicate[0]["kind"], "state-conflict")
        self.assertEqual(duplicate[0]["priority"], "P0")

    def test_deep_in_memory_event_is_state_conflict_not_uncaught_recursion(self):
        event = {"event_id": EVENT_ID, "kind": "fix"}
        current = event
        for _ in range(1500):
            child = {}
            current["nested"] = child
            current = child
        leaked = None
        try:
            findings = docs_checker.validate_operational_events([event])
        except RecursionError as exc:
            leaked = exc
            findings = []

        self.assertIsNone(leaked, "event canonicalization leaked recursion")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["kind"], "state-conflict")
        self.assertEqual(findings[0]["priority"], "P0")

    def test_conflicting_duplicate_finding_id_is_reported_by_checker(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            control = create_repository(root)
            conflict = valid_findings()
            duplicate = dict(conflict["findings"][0])
            duplicate["fingerprint"] = "7f2a91c4" + "b" * 56
            conflict["findings"].append(duplicate)
            write_json(control / "findings.json", conflict)

            findings, _ = docs_checker.check(root)

            self.assertTrue(
                any(
                    finding["kind"] == "state-conflict" and finding["priority"] == "P0"
                    for finding in findings
                )
            )

    def test_malformed_or_merge_conflicted_state_is_reported_without_repair(self):
        for content in ('{"schema_version":', "<<<<<<< ours\n{}\n=======\n{}\n>>>>>>> theirs\n"):
            with self.subTest(content=content[:10]), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                control = create_repository(root)
                state_path = control / "state.json"
                state_path.write_text(content, encoding="utf-8")
                before = state_path.read_bytes()

                findings, _ = docs_checker.check(root)

                self.assertTrue(any(item["kind"] == "state-conflict" for item in findings))
                self.assertEqual(state_path.read_bytes(), before)

    def test_public_memory_findings_sanitize_environmental_and_validation_messages(self):
        private = r"C:\private-checkout\credential.txt WinError 5 SECRET"
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with mock.patch(
                "_docs_checker.memory._operational_control",
                side_effect=PermissionError(private),
            ):
                findings = docs_checker.inspect_operational_memory(root)
            self.assertEqual(findings[0]["detail"], "operational control is unavailable")
            self.assertNotIn(private, json.dumps(findings, sort_keys=True))

        for error, expected in (
            (OSError(5, private), "operational file is unavailable"),
            (ValueError(private), "operational file is invalid"),
        ):
            with self.subTest(error=type(error).__name__), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                create_repository(root)
                with mock.patch(
                    "_docs_checker.memory.load_operational_state",
                    side_effect=error,
                ):
                    findings = docs_checker.inspect_operational_memory(root)
                conflict = next(
                    item for item in findings if item["path"] == ".diataxis/state.json"
                )
                self.assertEqual(conflict["detail"], expected)
                serialized = json.dumps(findings, sort_keys=True)
                self.assertNotIn(private, serialized)
                self.assertNotIn(str(root), serialized)

        with mock.patch(
            "_docs_checker.memory.event_fingerprint",
            side_effect=ValueError(private),
        ):
            findings = docs_checker.validate_operational_events(
                [{"event_id": EVENT_ID, "kind": "init"}]
            )
        self.assertEqual(findings[0]["detail"], "operational event is invalid")
        self.assertNotIn(private, json.dumps(findings, sort_keys=True))

    def test_duplicate_json_keys_are_state_conflicts_in_every_control_file(self):
        state = json.dumps(valid_state(), sort_keys=True)
        duplicate_state = state.replace(
            '"schema_version": 3',
            '"schema_version": 3, "schema_version": 3',
            1,
        )
        stored_findings = json.dumps(valid_findings(), sort_keys=True)
        duplicate_findings = stored_findings.replace(
            '"schema_version": 1',
            '"schema_version": 1, "schema_version": 1',
            1,
        )
        valid = valid_event()
        event = json.dumps(valid, sort_keys=True)
        duplicate_event = event.replace(
            f'"event_id": "{valid["event_id"]}"',
            f'"event_id": "{valid["event_id"]}", "event_id": "{valid["event_id"]}"',
            1,
        )
        cases = {
            "state.json": duplicate_state + "\n",
            "findings.json": duplicate_findings + "\n",
            "events.jsonl": duplicate_event + "\n",
        }
        for filename, content in cases.items():
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                control = create_repository(root)
                path = control / filename
                path.write_text(content, encoding="utf-8")
                before = path.read_bytes()

                findings, _ = docs_checker.check(root)

                self.assertTrue(
                    any(
                        item["kind"] == "state-conflict"
                        and item["priority"] == "P0"
                        and item["path"] == f".diataxis/{filename}"
                        for item in findings
                    )
                )
                self.assertEqual(path.read_bytes(), before)

    def test_malformed_merge_and_invalid_utf8_are_conflicts_in_every_control_file(self):
        malformed = b'{"schema_version":'
        merge = b'<<<<<<< ours\n{}\n=======\n{}\n>>>>>>> theirs\n'
        cases = {"malformed": malformed, "merge": merge, "invalid-utf8": b"\xff"}
        for filename in ("state.json", "findings.json", "events.jsonl"):
            for name, content in cases.items():
                with (
                    self.subTest(filename=filename, case=name),
                    tempfile.TemporaryDirectory() as td,
                ):
                    root = Path(td)
                    control = create_repository(root)
                    path = control / filename
                    path.write_bytes(content)
                    before = path.read_bytes()

                    findings, _ = docs_checker.check(root)

                    self.assertTrue(
                        any(
                            item["kind"] == "state-conflict"
                            and item["priority"] == "P0"
                            and item["path"] == f".diataxis/{filename}"
                            for item in findings
                        )
                    )
                    self.assertEqual(path.read_bytes(), before)

    def test_deep_json_is_state_conflict_not_uncaught_recursion(self):
        deep = ("[" * 5000 + "0" + "]" * 5000 + "\n").encode("utf-8")
        for filename in ("state.json", "findings.json", "events.jsonl"):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                control = create_repository(root)
                (control / filename).write_bytes(deep)
                leaked = None
                try:
                    findings, _ = docs_checker.check(root)
                except RecursionError as exc:
                    leaked = exc
                    findings = []

                self.assertIsNone(leaked, "decoder recursion escaped operational validation")
                self.assertTrue(
                    any(
                        item["kind"] == "state-conflict"
                        and item["priority"] == "P0"
                        and item["path"] == f".diataxis/{filename}"
                        for item in findings
                    )
                )

    def test_capacity_overflow_is_reported_without_reading_or_truncating(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            control = create_repository(root)
            state_path = control / "state.json"
            state_path.write_bytes(b"x" * (32 * 1024 + 1))
            before = state_path.read_bytes()

            findings, _ = docs_checker.check(root)

            self.assertTrue(
                any(
                    item["kind"] == "memory-capacity" and item["priority"] == "P1"
                    for item in findings
                )
            )
            self.assertEqual(state_path.read_bytes(), before)

    def test_each_control_file_enforces_its_capacity_without_truncation(self):
        cases = {
            "state.json": 32 * 1024,
            "findings.json": 256 * 1024,
            "events.jsonl": 256 * 1024,
        }
        for filename, capacity in cases.items():
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                control = create_repository(root)
                path = control / filename
                path.write_bytes(b"x" * (capacity + 1))
                before = path.read_bytes()

                findings, _ = docs_checker.check(root)

                self.assertTrue(
                    any(
                        item["kind"] == "memory-capacity"
                        and item["priority"] == "P1"
                        and item["path"] == f".diataxis/{filename}"
                        for item in findings
                    )
                )
                self.assertEqual(path.read_bytes(), before)

    def test_missing_control_files_are_conflicts_only_after_control_plane_exists(self):
        with tempfile.TemporaryDirectory() as td:
            uninitialized = Path(td) / "uninitialized"
            uninitialized.mkdir()
            findings, _ = docs_checker.check(uninitialized)
            self.assertFalse(any(item["kind"] == "state-conflict" for item in findings))

            initialized = Path(td) / "initialized"
            initialized.mkdir()
            (initialized / ".diataxis").mkdir()
            findings, _ = docs_checker.check(initialized)
            self.assertTrue(any(item["kind"] == "state-conflict" for item in findings))

    def test_checker_inspection_never_changes_operational_memory(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            control = create_repository(root)
            before = file_snapshot(control)

            docs_checker.check(root)

            self.assertEqual(file_snapshot(control), before)

    def test_memory_reference_defines_human_knowledge_and_committed_control_planes(self):
        memory = (SKILL / "references" / "memory.md").read_text(encoding="utf-8")
        required = (
            "Maintained Markdown is repository knowledge for humans and agents.",
            ".diataxis/ is cold operational continuity for the skill.",
            "Read-only commands may inspect both and write neither.",
            "Only approved, verified mutations update operational continuity.",
            "Protected intent is authoritative at its Markdown source; state stores a route and preservation instruction, not a replacement truth.",
            ".diataxis/ is committed so initialization, findings, freshness, and audit evidence survive clones.",
            "stable cross-session findings and verified drift now provide that need",
            "without adding a service, daemon, embedding store, or external database.",
        )

        for phrase in required:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, memory)

        skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("committed `.diataxis/`", skill)
        self.assertIn("cold operational continuity", skill)


if __name__ == "__main__":
    unittest.main()
