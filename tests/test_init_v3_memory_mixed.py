import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SKILL = ROOT / "skills" / "docs"
sys.path.insert(0, str(SKILL / "scripts"))

from _docs_checker import init_closeout as closeout
from _docs_checker import memory
from _docs_checker.identity import event_fingerprint, event_id
from _docs_checker.lifecycle import init_event_fingerprint
from tests.init_v3_fixture import (
    document_change,
    evidence_v3,
    request_v3,
    sha256_digest,
    whole_file_disposition,
)
from tests.test_init_v3_matrix_recovery import initialize_git


def canonical_bytes(value):
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode("utf-8")


def p0_conflicts(root):
    return [
        finding
        for finding in memory.inspect_operational_memory(root)
        if finding["kind"] == "state-conflict" and finding["priority"] == "P0"
    ]


class InitV3MixedMemoryBindingTests(unittest.TestCase):
    def install_mixed_preview(self, root):
        root = Path(root)
        source = b"# Documentation\n"
        document = root / "docs" / "README.md"
        document.parent.mkdir(parents=True)
        document.write_bytes(source)
        initialize_git(root)

        item = whole_file_disposition(
            "docs/README.md",
            source,
            disposition="ARCHIVED",
            target="docs/archive/README.md",
            recovery={
                "kind": "archive",
                "mode": "planned",
                "path": "docs/archive/README.md",
                "digest": sha256_digest(source),
            },
        )
        evidence = evidence_v3(dispositions=[item])
        evidence["map_path"] = "docs/archive/README.md"
        evidence["hot_path_bytes"] = {
            "before": {"value": 0, "unit": "bytes", "provenance": []},
            "after": {
                "value": len(source),
                "unit": "bytes",
                "provenance": [
                    {
                        "route": "docs/archive/README.md",
                        "bytes": len(source),
                        "source": "filesystem-stat",
                    }
                ],
            },
        }
        changes = [
            document_change(
                "CREATE",
                "docs/archive/README.md",
                source,
                source_item_ids=[item["item_id"]],
            ),
            document_change(
                "DELETE",
                "docs/README.md",
                source_item_ids=[item["item_id"]],
            ),
        ]
        prepared = closeout.prepare_initialization_closeout(
            root,
            request_v3(evidence=evidence, document_changes=changes),
        )
        plan = prepared["plan"]
        operations = {
            operation["path"]: operation for operation in plan["document_operations"]
        }
        for relative in plan["replacement_order"]:
            operation = operations.get(relative)
            target = root / relative
            if operation is not None:
                if operation["operation"] == "DELETE":
                    target.unlink()
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(operation["result_bytes"])
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(plan["targets"][relative])
        return plan

    def append_later_event(self, root):
        root = Path(root)
        events_path = root / ".diataxis" / "events.jsonl"
        init_event = json.loads(events_path.read_text(encoding="utf-8"))
        later = {
            "event_id": "EVT-00000000",
            "kind": "doctor",
            "completed_at": "2026-07-15T13:00:00Z",
            "changed_paths": [],
            "summary": "Verified the installed operational state.",
        }
        later["event_id"] = event_id(event_fingerprint(later))
        events_path.write_bytes(canonical_bytes(init_event) + canonical_bytes(later))

        state_path = root / ".diataxis" / "state.json"
        state = json.loads(state_path.read_bytes())
        state["last_completed_event"] = later["event_id"]
        for record in state["verified_documents"]:
            record["verified_event"] = later["event_id"]
        state_path.write_bytes(canonical_bytes(state))
        return init_event, later

    def coherently_rebind_init_event(self, root, mutate):
        root = Path(root)
        events_path = root / ".diataxis" / "events.jsonl"
        events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
        init_event = copy.deepcopy(events[0])
        old_manifest = root / init_event["manifest"]["path"]
        mutate(init_event)
        init_event["event_id"] = "EVT-" + init_event_fingerprint(init_event)[:8].upper()
        init_event["manifest"]["path"] = (
            f".diataxis/manifests/{init_event['event_id']}.json"
        )
        new_manifest = root / init_event["manifest"]["path"]
        old_manifest.replace(new_manifest)
        events[0] = init_event
        events_path.write_bytes(b"".join(canonical_bytes(event) for event in events))

    def test_doctor_accepts_exact_mixed_document_operation_bindings(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plan = self.install_mixed_preview(root)

            event = plan["event"]
            self.assertEqual(
                {
                    "docs/archive/README.md": "recovery-archive",
                    "docs/README.md": "document-source",
                },
                {
                    path: event["target_roles"][path]
                    for path in ("docs/archive/README.md", "docs/README.md")
                },
            )
            self.assertEqual(p0_conflicts(root), [])

    def test_later_non_init_event_does_not_hide_init_manifest_operation_tampering(self):
        def rebind_document_path(event):
            old = "docs/archive/README.md"
            new = "docs/archive/rebound.md"
            event["transaction_targets"] = sorted(
                new if path == old else path for path in event["transaction_targets"]
            )
            event["starting_digests"][new] = event["starting_digests"].pop(old)
            event["target_roles"][new] = event["target_roles"].pop(old)
            event["replacement_order"] = [
                new if path == old else path for path in event["replacement_order"]
            ]

        mutators = {
            "document path": rebind_document_path,
            "document starting digest": lambda event: event["starting_digests"].update(
                {"docs/archive/README.md": "sha256:" + "0" * 64}
            ),
            "document role": lambda event: event["target_roles"].update(
                {"docs/archive/README.md": "document-result"}
            ),
            "replacement order": lambda event: event["replacement_order"].reverse(),
        }
        for label, mutate in mutators.items():
            with self.subTest(binding=label), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                self.install_mixed_preview(root)
                self.append_later_event(root)
                self.assertEqual(p0_conflicts(root), [])

                self.coherently_rebind_init_event(root, mutate)

                self.assertTrue(
                    p0_conflicts(root),
                    f"Doctor accepted coherently rebound Init {label}",
                )


if __name__ == "__main__":
    unittest.main()
