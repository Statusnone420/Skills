import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / "skills" / "docs" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from _docs_checker import discovery
from _docs_checker import init_closeout as closeout
from _docs_checker import lifecycle
from _docs_checker import memory
from tests.init_v3_fixture import evidence_v3, request_v3
from tests.test_init_v3_matrix_recovery import initialize_git


EVENT_FIELDS = {
    "event_id",
    "kind",
    "completed_at",
    "skill_version",
    "approved_ids",
    "score_before",
    "score_after",
    "reason",
    "summary",
    "worktree_kind",
    "repository_identity",
    "worktree_identity",
    "worktree_state_identity",
    "changed_paths",
    "transaction_id",
    "transaction_schema_version",
    "transaction_policy_version",
    "starting_digests",
    "state_semantic_digest",
    "findings_digest",
    "transaction_targets",
    "target_roles",
    "replacement_order",
    "approval_bindings",
    "selected_boundary",
    "visibility",
    "manifest",
    "manifest_digest",
    "manifest_schema_version",
    "manifest_identity",
    "approval_identity",
    "corpus_transition",
    "corpus_transition_digest",
    "document_results_digest",
}


def canonical_bytes(value):
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode("utf-8")


def make_root():
    temporary = tempfile.TemporaryDirectory()
    root = Path(temporary.name)
    document = root / "docs" / "README.md"
    document.parent.mkdir(parents=True)
    document.write_bytes(b"# Documentation\n")
    return temporary, root


class InitV3PersistenceTests(unittest.TestCase):
    def test_manifest_capacity_accepts_exactly_one_mib_and_rejects_plus_one(self):
        maximum = 1024 * 1024
        self.assertEqual(
            len(lifecycle._enforce_init_manifest_capacity(b"x" * maximum)),
            maximum,
        )
        with self.assertRaisesRegex(ValueError, "capacity"):
            lifecycle._enforce_init_manifest_capacity(b"x" * (maximum + 1))

    def test_state_is_schema3_only_and_binds_result_corpus_and_document_results(self):
        temporary, root = make_root()
        with temporary:
            evidence = evidence_v3()
            corpus = discovery.scan_selected_document_corpus(
                root,
                "docs",
                "selected-scope-exact",
            )["corpus"]
            results_digest = "sha256:" + hashlib.sha256(canonical_bytes([])).hexdigest()
            inputs = {
                key: copy.deepcopy(evidence[key])
                for key in closeout._STATE_FIELDS
            }
            state = memory.build_initialization_state(
                root,
                **inputs,
                manifest_identity="a" * 64,
                result_corpus=corpus,
                document_results_digest=results_digest,
                last_completed_event="EVT-12345678",
            )

            self.assertEqual(state["schema_version"], 3)
            self.assertEqual(
                set(state),
                {
                    "schema_version",
                    "initialized",
                    "rubric",
                    "cold_paths",
                    "verified_documents",
                    "protected_intent",
                    "last_completed_event",
                    "scope",
                    "structural_scores",
                    "hot_path_bytes",
                    "trust_coverage",
                    "initialization",
                },
            )
            self.assertEqual(
                state["initialization"],
                {
                    "manifest_identity": "a" * 64,
                    "result_corpus": corpus,
                    "document_results_digest": results_digest,
                },
            )

            legacy = copy.deepcopy(state)
            legacy["schema_version"] = 2
            with self.assertRaisesRegex(ValueError, "unsupported"):
                memory.validate_operational_state(legacy, root)

    def test_preview_persists_one_external_canonical_body_free_schema3_manifest(self):
        temporary, root = make_root()
        with temporary:
            initialize_git(root)
            prepared = closeout.prepare_initialization_closeout(root, request_v3())
            event = prepared["plan"]["event"]
            manifest_path = event["manifest"]["path"]
            manifest_bytes = prepared["plan"]["targets"][manifest_path]
            manifest = json.loads(manifest_bytes)

            self.assertEqual(manifest_bytes, canonical_bytes(manifest))
            self.assertEqual(
                set(manifest),
                {
                    "schema_version",
                    "approval_identity",
                    "corpus_transition",
                    "dispositions",
                    "document_results",
                },
            )
            self.assertEqual(manifest["schema_version"], 3)
            self.assertEqual(
                event["manifest_digest"],
                "sha256:" + hashlib.sha256(manifest_bytes).hexdigest(),
            )
            self.assertEqual(event["manifest_identity"], prepared["manifest_sha256"])
            self.assertNotIn("content_base64", manifest_bytes.decode("utf-8"))
            self.assertLessEqual(len(manifest_bytes), 1024 * 1024)

    def test_no_git_preview_binds_filesystem_repository_and_worktree_state(self):
        temporary, root = make_root()
        with temporary:
            prepared = closeout.prepare_initialization_closeout(root, request_v3())
            event = prepared["plan"]["event"]
            self.assertEqual(event["worktree_kind"], "filesystem")
            self.assertRegex(event["repository_identity"], r"^[0-9a-f]{64}$")
            self.assertRegex(event["worktree_identity"], r"^[0-9a-f]{64}$")
            self.assertRegex(event["worktree_state_identity"], r"^[0-9a-f]{64}$")

    def test_success_event_has_only_the_exact_v3_fields_and_cross_bindings(self):
        temporary, root = make_root()
        with temporary:
            initialize_git(root)
            prepared = closeout.prepare_initialization_closeout(root, request_v3())
            plan = prepared["plan"]
            event = plan["event"]
            manifest = json.loads(plan["targets"][event["manifest"]["path"]])
            state = json.loads(plan["targets"][".diataxis/state.json"])

            self.assertEqual(set(event), EVENT_FIELDS)
            self.assertEqual(event["manifest_schema_version"], 3)
            self.assertEqual(event["approval_identity"], manifest["approval_identity"])
            self.assertEqual(event["corpus_transition"], manifest["corpus_transition"])
            self.assertEqual(
                state["initialization"]["result_corpus"],
                manifest["corpus_transition"]["result"],
            )
            self.assertEqual(
                state["initialization"]["document_results_digest"],
                event["document_results_digest"],
            )
            self.assertEqual(
                event["corpus_transition_digest"],
                "sha256:"
                + hashlib.sha256(canonical_bytes(manifest["corpus_transition"])).hexdigest(),
            )
            self.assertEqual(
                event["document_results_digest"],
                "sha256:"
                + hashlib.sha256(canonical_bytes(manifest["document_results"])).hexdigest(),
            )
            fingerprint = lifecycle.init_event_fingerprint(event)
            self.assertEqual(event["event_id"], "EVT-" + fingerprint[:8].upper())
            relocated = copy.deepcopy(event)
            relocated["manifest"]["path"] = ".diataxis/manifests/ignored.json"
            self.assertEqual(lifecycle.init_event_fingerprint(relocated), fingerprint)
            rebound = copy.deepcopy(event)
            rebound["manifest"]["digest"] = "sha256:" + "0" * 64
            self.assertNotEqual(lifecycle.init_event_fingerprint(rebound), fingerprint)

    def test_manifest_contract_rejects_extra_corpus_and_document_body_fields(self):
        temporary, root = make_root()
        with temporary:
            corpus = discovery.scan_selected_document_corpus(
                root,
                "docs",
                "selected-scope-exact",
            )["corpus"]
            result = {
                "path": "docs/new.md",
                "operation": "CREATE",
                "role": "document-result",
                "starting_digest": "sha256:ABSENT",
                "result_digest": "sha256:" + "b" * 64,
                "bytes": 6,
                "source_item_ids": [],
            }
            arguments = {
                "event_id_value": None,
                "dispositions": evidence_v3()["dispositions"],
                "removed_items": [],
                "git_available": True,
                "command": "init",
                "approval_bindings": [],
                "corpus_transition": {"starting": corpus, "result": corpus},
                "document_results": [result],
            }
            lifecycle.prepare_dispositions(**arguments)

            with self.assertRaises(ValueError):
                lifecycle.prepare_dispositions(
                    **{
                        **arguments,
                        "corpus_transition": {
                            "starting": {**corpus, "legacy": True},
                            "result": corpus,
                        },
                    }
                )
            with self.assertRaises(ValueError):
                lifecycle.prepare_dispositions(
                    **{
                        **arguments,
                        "document_results": [
                            {**result, "content_base64": "IyBOZXcK"}
                        ],
                    }
                )

    def test_doctor_accepts_complete_v3_bindings_and_rejects_manifest_tampering(self):
        temporary, root = make_root()
        with temporary:
            initialize_git(root)
            prepared = closeout.prepare_initialization_closeout(root, request_v3())
            applied = closeout.apply_response(root, prepared, prepared["approval"])
            self.assertEqual(applied["status"], "applied")
            self.assertFalse(
                any(
                    finding["priority"] == "P0"
                    for finding in memory.inspect_operational_memory(root)
                )
            )

            event = memory.load_operational_events(root)[0]
            manifest_path = root / event["manifest"]["path"]
            original = manifest_path.read_bytes()
            manifest = json.loads(original)
            manifest["document_results"].append(
                {
                    "path": "docs/leaked.md",
                    "operation": "CREATE",
                    "role": "document-result",
                    "starting_digest": "sha256:ABSENT",
                    "result_digest": "sha256:" + "c" * 64,
                    "bytes": 1,
                    "source_item_ids": [],
                    "content_base64": "eA==",
                }
            )
            manifest_path.write_bytes(canonical_bytes(manifest))
            findings = memory.inspect_operational_memory(root)
            self.assertTrue(
                any(
                    finding["priority"] == "P0"
                    and finding["kind"] == "state-conflict"
                    for finding in findings
                )
            )

    def test_doctor_rejects_each_v3_cross_binding_and_hidden_recovery(self):
        temporary, root = make_root()
        with temporary:
            initialize_git(root)
            prepared = closeout.prepare_initialization_closeout(root, request_v3())
            applied = closeout.apply_response(root, prepared, prepared["approval"])
            self.assertEqual(applied["status"], "applied")
            state_path = root / ".diataxis" / "state.json"
            events_path = root / ".diataxis" / "events.jsonl"
            original_state = state_path.read_bytes()
            original_events = events_path.read_bytes()

            def assert_p0():
                self.assertTrue(
                    any(
                        finding["kind"] == "state-conflict"
                        and finding["priority"] == "P0"
                        for finding in memory.inspect_operational_memory(root)
                    )
                )

            state_mutators = {
                "manifest identity": lambda value: value["initialization"].update(
                    manifest_identity="0" * 64
                ),
                "result corpus": lambda value: value["initialization"][
                    "result_corpus"
                ].update(paths_digest="sha256:" + "0" * 64),
                "document results": lambda value: value["initialization"].update(
                    document_results_digest="sha256:" + "0" * 64
                ),
                "completed event": lambda value: value.update(
                    last_completed_event="EVT-00000000"
                ),
            }
            for label, mutate in state_mutators.items():
                with self.subTest(binding=label):
                    value = json.loads(original_state)
                    mutate(value)
                    state_path.write_bytes(canonical_bytes(value))
                    assert_p0()
                    state_path.write_bytes(original_state)

            event_mutators = {
                "approval": lambda value: value.update(approval_identity="0" * 64),
                "corpus transition": lambda value: value.update(
                    corpus_transition_digest="sha256:" + "0" * 64
                ),
                "document result digest": lambda value: value.update(
                    document_results_digest="sha256:" + "0" * 64
                ),
                "transaction roles": lambda value: value["target_roles"].update(
                    manifest="state"
                ),
            }
            for label, mutate in event_mutators.items():
                with self.subTest(binding=label):
                    value = json.loads(original_events)
                    mutate(value)
                    events_path.write_bytes(canonical_bytes(value))
                    assert_p0()
                    events_path.write_bytes(original_events)

            recovery = root / ".diataxis" / "recovery" / "TXN-0000000000000000"
            recovery.mkdir(parents=True)
            assert_p0()

    def test_doctor_rejects_coherently_rebound_fake_hard_delete_and_long_event_id(self):
        for mutation in ("fake-hard-delete", "long-event-id"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                document = root / "docs" / "README.md"
                document.parent.mkdir(parents=True)
                document.write_bytes(b"# Documentation\n")
                initialize_git(root)
                prepared = closeout.prepare_initialization_closeout(root, request_v3())
                applied = closeout.apply_response(root, prepared, prepared["approval"])
                self.assertEqual(applied["status"], "applied")

                state_path = root / ".diataxis" / "state.json"
                events_path = root / ".diataxis" / "events.jsonl"
                state = json.loads(state_path.read_bytes())
                event = json.loads(events_path.read_bytes())
                old_manifest_path = root / event["manifest"]["path"]

                if mutation == "fake-hard-delete":
                    event["hard_delete_acceptance_digest"] = "sha256:" + "d" * 64
                    fingerprint = lifecycle.init_event_fingerprint(event)
                    new_event_id = "EVT-" + fingerprint[:8].upper()
                else:
                    fingerprint = lifecycle.init_event_fingerprint(event)
                    new_event_id = "EVT-" + fingerprint[:12].upper()
                event["event_id"] = new_event_id
                event["manifest"]["path"] = (
                    f".diataxis/manifests/{new_event_id}.json"
                )
                state["last_completed_event"] = new_event_id
                for record in state["verified_documents"]:
                    record["verified_event"] = new_event_id
                new_manifest_path = root / event["manifest"]["path"]
                old_manifest_path.rename(new_manifest_path)
                state_path.write_bytes(canonical_bytes(state))
                events_path.write_bytes(canonical_bytes(event))

                self.assertTrue(
                    any(
                        finding["kind"] == "state-conflict"
                        and finding["priority"] == "P0"
                        for finding in memory.inspect_operational_memory(root)
                    )
                )


if __name__ == "__main__":
    unittest.main()
