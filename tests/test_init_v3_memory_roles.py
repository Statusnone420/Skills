import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SKILL = ROOT / "skills" / "docs"
sys.path.insert(0, str(SKILL / "scripts"))

from _docs_checker import memory


ABSENT = "sha256:ABSENT"
DIGEST = "sha256:" + "a" * 64
DOCUMENT_PATH = "docs/source.md"
EVENT_PATH = ".diataxis/events.jsonl"
FINDINGS_PATH = ".diataxis/findings.json"
STATE_PATH = ".diataxis/state.json"


def document_result(operation, role):
    return {
        "path": DOCUMENT_PATH,
        "operation": operation,
        "role": role,
        "starting_digest": ABSENT if operation == "CREATE" else DIGEST,
        "result_digest": ABSENT if operation == "DELETE" else DIGEST,
        "bytes": 0 if operation == "DELETE" else 12,
        "source_item_ids": ["SEC-0123456789ABCDEF01234567"],
    }


def binding_event(result):
    controls = [EVENT_PATH, FINDINGS_PATH, STATE_PATH, "manifest"]
    targets = sorted([*controls, result["path"]])
    roles = {
        EVENT_PATH: "event",
        FINDINGS_PATH: "findings",
        STATE_PATH: "state",
        "manifest": "manifest",
        result["path"]: result["role"],
    }
    order = ["manifest"]
    if result["operation"] == "CREATE" and result["role"] == "recovery-archive":
        order.append(result["path"])
    if (
        result["operation"] in {"CREATE", "REPLACE"}
        and result["role"] != "recovery-archive"
    ):
        order.append(result["path"])
    order.extend([STATE_PATH, FINDINGS_PATH])
    if result["operation"] == "DELETE":
        order.append(result["path"])
    order.append(EVENT_PATH)
    return {
        "transaction_targets": targets,
        "starting_digests": {
            target: result["starting_digest"] if target == result["path"] else ABSENT
            for target in targets
        },
        "target_roles": dict(sorted(roles.items())),
        "replacement_order": order,
    }


class InitV3MemoryDocumentRoleTests(unittest.TestCase):
    def validate(self, operation, role):
        result = document_result(operation, role)
        memory._validate_init_transaction_bindings(binding_event(result), [result])

    def test_document_source_replace_is_the_aggregate_section_update_variant(self):
        self.validate("REPLACE", "document-source")
        self.validate("DELETE", "document-source")

    def test_document_role_operation_matrix_rejects_every_other_pair(self):
        allowed = {
            ("CREATE", "recovery-archive"),
            ("CREATE", "document-result"),
            ("REPLACE", "document-result"),
            ("REPLACE", "document-source"),
            ("DELETE", "document-source"),
        }
        for role in ("recovery-archive", "document-result", "document-source"):
            for operation in ("CREATE", "REPLACE", "DELETE"):
                if (operation, role) in allowed:
                    continue
                with self.subTest(operation=operation, role=role):
                    with self.assertRaisesRegex(
                        ValueError,
                        "document operation role does not match its operation",
                    ):
                        self.validate(operation, role)


if __name__ == "__main__":
    unittest.main()
