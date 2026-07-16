import ast
import copy
import ctypes
import errno
import hashlib
import importlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).parents[1]
SKILL = ROOT / "skills" / "docs"
SCRIPTS = SKILL / "scripts"
PACKAGE = SCRIPTS / "_docs_checker"
sys.path.insert(0, str(SCRIPTS))
import check as docs_checker

from tests.test_repository_memory import (
    BASE_DOCUMENTS,
    complete_init_state,
    create_repository,
    file_snapshot,
    valid_event,
    valid_findings,
    valid_state,
)


READ_ONLY_COMMANDS = ("doctor", "check", "map", "context", "audit", "classify")
MUTATING_COMMANDS = ("init", "write", "update", "fix", "migrate", "cleanup")
TRANSACTION_PREFIX = ".docs-txn-"


def tree_bytes(root):
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in Path(root).rglob("*")
        if path.is_file()
    }


def semantic_finding(key="release-state", **overrides):
    evidence = [{"path": "docs/STATE.md", "key": key}]
    kind = overrides.pop("kind", "semantic-truth-gap")
    fingerprint = docs_checker.finding_fingerprint(kind, evidence)
    record = {
        "id": docs_checker.finding_id(fingerprint, {}),
        "fingerprint": fingerprint,
        "kind": kind,
        "origin": "semantic",
        "priority": "P1",
        "status": "Proposed",
        "summary": "One verified current-truth fact is missing.",
        "why": "The current route would otherwise be incomplete.",
        "evidence": evidence,
        "recommended_action": "Update the maintained truth route.",
        "children": [".1"],
    }
    unknown = sorted(set(overrides) - set(record))
    if unknown:
        raise TypeError(f"unknown finding override(s): {', '.join(unknown)}")
    record.update(overrides)
    return record


def semantic_event(kind="update", approved_ids=()):
    event = {
        "kind": kind,
        "completed_at": "2026-07-13T12:00:00Z",
        "skill_version": "0.3.0" if kind == "init" else "0.1.0",
        "approved_ids": list(approved_ids),
        "score_before": 84,
        "score_after": 90,
        "changed_paths": ["docs/STATE.md"],
        "reason": "Applied one exact approved documentation treatment.",
        "summary": "Verified the result and closed operational continuity.",
    }
    if kind == "init":
        event.update(
            {
                "worktree_kind": "filesystem",
                "repository_identity": "1" * 64,
                "worktree_identity": "2" * 64,
                "worktree_state_identity": "3" * 64,
            }
        )
    return event


def disposition(
    identity="docs/legacy.md#<whole-file>",
    *,
    outcome="ARCHIVED",
    target="docs/archive/legacy.md",
):
    path, section = identity.split("#", 1)
    result = {
        "item_id": identity,
        "path": path,
        "section": section,
        "disposition": outcome,
        "reason": "The exact approved legacy item is superseded.",
        "source_digest": "sha256:" + hashlib.sha256(identity.encode()).hexdigest(),
        "recovery": {
            "kind": "archive",
            "path": target,
            "digest": "sha256:" + hashlib.sha256(target.encode()).hexdigest(),
        },
    }
    if outcome in {"MIGRATED", "DEDUPLICATED", "ARCHIVED"}:
        result["target"] = target
    return result


def retained_disposition(identity="docs/README.md#<whole-file>"):
    path, _section = identity.split("#", 1)
    return {
        "item_id": identity,
        "path": path,
        "section": {"kind": "whole-file"},
        "disposition": "RETAIN",
        "reason": "The item remains part of the exact verified adoption.",
        "source_digest": "sha256:" + hashlib.sha256(identity.encode()).hexdigest(),
    }


def init_disposition(
    identity="docs/legacy.md#<whole-file>",
    *,
    outcome="ARCHIVED",
    target="docs/archive/legacy.md",
    recovery_path=None,
):
    path, _section = identity.split("#", 1)
    recovery_path = recovery_path or target
    result = {
        "item_id": identity,
        "path": path,
        "section": {"kind": "whole-file"},
        "disposition": outcome,
        "reason": "The exact approved whole document is superseded.",
        "source_digest": "sha256:" + hashlib.sha256(identity.encode()).hexdigest(),
        "recovery": {
            "kind": "archive",
            "mode": "planned",
            "path": recovery_path,
            "digest": "sha256:"
            + hashlib.sha256(recovery_path.encode()).hexdigest(),
        },
    }
    if outcome in {"MIGRATED", "DEDUPLICATED", "ARCHIVED"}:
        result["target"] = target
    if outcome == "DEDUPLICATED":
        result["target_digest"] = (
            "sha256:" + hashlib.sha256(target.encode()).hexdigest()
        )
    return result


def canonical_bytes(value):
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode("utf-8")


def init_corpus(paths, *, selected_scope="docs"):
    ordered = sorted(set(paths), key=lambda path: (path.casefold(), path))
    return {
        "coverage_version": "init-corpus-v1",
        "coverage_mode": "selected-scope-exact",
        "ordering_version": "repo-relative-casefold-v1",
        "selected_scope": selected_scope,
        "write_boundary": selected_scope,
        "path_count": len(ordered),
        "paths_digest": "sha256:"
        + hashlib.sha256(
            canonical_bytes(
                {
                    "ordering_version": "repo-relative-casefold-v1",
                    "paths": ordered,
                }
            )
        ).hexdigest(),
    }


def init_manifest_evidence(dispositions, *, selected_scope="docs"):
    starting_paths = [item["path"] for item in dispositions]
    result_paths = []
    for item in dispositions:
        if item["disposition"] == "RETAIN":
            result_paths.append(item["path"])
        elif item["disposition"] in {"MIGRATED", "DEDUPLICATED", "ARCHIVED"}:
            result_paths.append(item["target"])
    return {
        "corpus_transition": {
            "starting": init_corpus(starting_paths, selected_scope=selected_scope),
            "result": init_corpus(result_paths, selected_scope=selected_scope),
        },
        "document_results": [],
    }


def local_route(path=".local/0.3.0-campaign/KICKOFF-PROMPT.md"):
    return {
        "route": path,
        "visibility": "local-only",
        "kind": "campaign-plan",
        "topics": ["0.3.0"],
        "aliases": ["campaign-plan"],
        "authority": "authoritative",
        "status": "current",
        "preservation": "preserve-local-only",
        "last_verified_system": "0.1.0",
        "last_verified_rubric": "2",
    }


def create_uninitialized_repository(root):
    for relative, data in BASE_DOCUMENTS.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    src = root / "src"
    src.mkdir()
    (src / "config.py").write_text("VALUE = 1\n", encoding="utf-8")


class Task7ContractCase(unittest.TestCase):
    def api(self, name):
        self.assertTrue(
            hasattr(docs_checker, name),
            f"Task 7 lifecycle API is missing: {name}",
        )
        return getattr(docs_checker, name)

    def module(self, name):
        path = PACKAGE / f"{name}.py"
        self.assertTrue(path.is_file(), f"Task 7 module is missing: {path.name}")
        try:
            return importlib.import_module(f"_docs_checker.{name}")
        except Exception as exc:  # pragma: no cover - converted to a precise test failure
            self.fail(f"Task 7 module {name}.py is not importable: {type(exc).__name__}")

    def approval(self, record):
        return {"id": record["id"], "fingerprint": record["fingerprint"]}

    def init_manifest(
        self,
        dispositions,
        *,
        approvals=(),
        selected_scope="docs",
        transaction_id=None,
        git_available=True,
    ):
        evidence = init_manifest_evidence(
            dispositions,
            selected_scope=selected_scope,
        )
        prepared = self.api("prepare_dispositions")(
            None,
            dispositions,
            removed_items=[
                item["item_id"]
                for item in dispositions
                if item["disposition"] != "RETAIN"
            ],
            git_available=git_available,
            command="init",
            approval_bindings=approvals,
            transaction_id=transaction_id,
            **evidence,
        )
        return prepared, evidence

    def prepare(
        self,
        root,
        *,
        command="update",
        dispositions=(),
        removed_items=(),
        local_map=None,
        **extra,
    ):
        prepare = self.api("prepare_verified_closeout")
        finding = semantic_finding()
        approvals = [self.approval(finding)]
        state = valid_state()
        dispositions = list(dispositions)
        removed_items = list(removed_items)
        if command == "init":
            if not dispositions:
                dispositions = []
                for path in BASE_DOCUMENTS:
                    item = retained_disposition(f"{path}#<whole-file>")
                    item["source_digest"] = "sha256:" + hashlib.sha256(
                        (Path(root) / path).read_bytes()
                    ).hexdigest()
                    dispositions.append(item)
            preview_manifest, init_evidence = self.init_manifest(
                dispositions,
                approvals=approvals,
            )
            state = complete_init_state(
                root,
                manifest_identity=preview_manifest["manifest_identity"],
                result_corpus=init_evidence["corpus_transition"]["result"],
                document_results_digest=preview_manifest[
                    "document_results_digest"
                ],
                score_before=84,
                score_after=90,
            )
            extra.setdefault("selected_boundary", state["scope"]["selected"])
            extra.setdefault("corpus_transition", init_evidence["corpus_transition"])
            extra.setdefault("document_results", init_evidence["document_results"])
        return prepare(
            root,
            command=command,
            state=state,
            findings={"schema_version": 1, "findings": []},
            event=semantic_event(command, [finding["id"]]),
            approvals=approvals,
            dispositions=dispositions,
            removed_items=removed_items,
            local_map=local_map,
            **extra,
        )

    def prepare_comprehensive(self, root):
        """Build the real five-target closeout used by transaction-boundary probes."""
        try:
            subprocess.run(
                ["git", "init", "-q"],
                cwd=root,
                check=True,
                capture_output=True,
            )
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("Git unavailable")
        (root / ".gitignore").write_text(
            ".diataxis/local-map.json\n",
            encoding="utf-8",
        )
        route = local_route(".local/private-plan.md")
        route_path = root / route["route"]
        route_path.parent.mkdir()
        route_path.write_text("# Private plan\n", encoding="utf-8")
        verified = self.api("verify_local_route_hashes")(
            root,
            [route],
            selected_scope=".local",
            byte_limit=64 * 1024,
        )
        local_map = {
            "schema_version": 2,
            "repository_identity": "a" * 64,
            "worktree_identity": "b" * 64,
            "routes": verified["routes"],
        }
        classification = docs_checker.classify_protected_surfaces(
            ["docs/README.md"],
            host="github",
        )
        protected_preview = docs_checker.preview_protected_dispositions(
            classification,
            [
                {
                    "path": "docs/README.md",
                    "action": "replace",
                    "disposition": "MIGRATED",
                }
            ],
            exact_authorizations=["docs/README.md"],
        )
        dispositions = [
            disposition(f"docs/legacy-{index:04d}.md#<whole-file>")
            for index in range(360)
        ]
        plan = self.prepare(
            root,
            command="cleanup",
            dispositions=dispositions,
            removed_items=[item["item_id"] for item in dispositions],
            local_map=local_map,
            protected_preview=protected_preview,
        )
        self.assertEqual(plan["status"], "approval-required")
        return plan


class Task7ArchitectureTests(Task7ContractCase):
    def test_task7_architecture_is_isolated_cohesive_acyclic_and_read_only(self):
        self._assert_shared_architecture_baseline_is_lifecycle_independent()
        self._assert_task7_modules_are_cohesive_acyclic_stdlib_only_and_registered()
        self._assert_memory_is_read_only_and_facade_is_thin()

    def _assert_shared_architecture_baseline_is_lifecycle_independent(self):
        shared = ast.parse(
            (ROOT / "tests" / "test_docs_checker_architecture.py").read_text(
                encoding="utf-8"
            )
        )
        module_names = set()
        imported_names = set()
        for node in ast.walk(shared):
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "MODULES"
                for target in node.targets
            ):
                module_names.update(
                    item.value
                    for item in node.value.elts
                    if isinstance(item, ast.Constant) and isinstance(item.value, str)
                )
            elif isinstance(node, ast.alias):
                imported_names.add(node.name.rsplit(".", 1)[-1])
        self.assertTrue(
            {"lifecycle", "lifecycle_io"}.isdisjoint(module_names),
            "shared architecture invariants must not require Task 7 modules",
        )
        self.assertTrue(
            {"lifecycle", "lifecycle_io"}.isdisjoint(imported_names),
            "shared architecture imports must remain lifecycle independent",
        )

    def _assert_task7_modules_are_cohesive_acyclic_stdlib_only_and_registered(self):
        lifecycle_path = PACKAGE / "lifecycle.py"
        io_path = PACKAGE / "lifecycle_io.py"
        self.assertTrue(lifecycle_path.is_file(), "Task 7 lifecycle policy module is missing")
        self.assertTrue(io_path.is_file(), "Task 7 transaction I/O module is missing")

        modules = {
            path.stem: path
            for path in PACKAGE.glob("*.py")
            if path.name != "__init__.py"
        }
        graph = {name: set() for name in modules}
        for name, path in modules.items():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported = [alias.name.split(".", 1)[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    imported = [(node.module or "").split(".", 1)[0]]
                    if node.level and imported[0] in graph:
                        graph[name].add(imported[0])
                else:
                    continue
                if not isinstance(node, ast.ImportFrom) or node.level == 0:
                    for imported_name in imported:
                        self.assertIn(imported_name, sys.stdlib_module_names, path.name)
                self.assertNotIn("check", imported, path.name)

        self.assertNotIn("lifecycle", graph["memory"])
        self.assertNotIn("lifecycle_io", graph["memory"])
        self.assertNotIn("lifecycle_io", graph["lifecycle"])
        self.assertIn("lifecycle", graph["lifecycle_io"])
        self.assertLessEqual(
            graph["lifecycle"],
            {"identity", "knowledge", "memory", "paths", "surfaces"},
        )
        self.assertLessEqual(
            graph["lifecycle_io"],
            {
                "discovery",
                "identity",
                "knowledge",
                "lifecycle",
                "memory",
                "paths",
                "surfaces",
            },
        )

        visiting = set()
        visited = set()

        def visit(name):
            if name in visiting:
                self.fail(f"Task 7 dependency cycle reaches {name}")
            if name in visited:
                return
            visiting.add(name)
            for dependency in graph[name]:
                visit(dependency)
            visiting.remove(name)
            visited.add(name)

        for name in graph:
            visit(name)

        sys.path.insert(0, str(ROOT))
        try:
            from tools import build_adapters
        finally:
            sys.path.pop(0)
        for relative in (
            "scripts/_docs_checker/lifecycle.py",
            "scripts/_docs_checker/lifecycle_io.py",
        ):
            self.assertIn(relative, build_adapters.CHECKER_FILES)

    def _assert_memory_is_read_only_and_facade_is_thin(self):
        lifecycle_names = {
            "apply_state_conflict_recovery",
            "apply_verified_closeout",
            "build_verified_event",
            "prepare_dispositions",
            "prepare_verified_closeout",
            "preview_memory_compaction",
            "preview_state_conflict_recovery",
            "select_persisted_findings",
            "transition_finding",
            "validate_protected_intent_change",
            "verify_local_route_hashes",
        }
        missing = sorted(name for name in lifecycle_names if not hasattr(docs_checker, name))
        self.assertEqual(missing, [], f"Task 7 façade re-exports are missing: {missing}")

        checker_tree = ast.parse((SCRIPTS / "check.py").read_text(encoding="utf-8"))
        definitions = {
            node.name
            for node in checker_tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertEqual(definitions, {"check", "main"})

        memory_tree = ast.parse((PACKAGE / "memory.py").read_text(encoding="utf-8"))
        for node in ast.walk(memory_tree):
            if isinstance(node, ast.Attribute):
                self.assertNotEqual(node.attr, "replace", "memory.py must not replace files")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                self.assertNotIn(node.func.id, {"open", "replace"})


class Task7PolicyTests(Task7ContractCase):
    def test_exact_transition_table_identity_recurrence_and_persistence_policy(self):
        transition = self.api("transition_finding")
        select = self.api("select_persisted_findings")
        proposed = semantic_finding()
        approved = transition(proposed, "Approved", priority="P0")
        applied = transition(approved, "Applied")
        parked = transition(proposed, "Parked")
        self.assertEqual((approved["id"], approved["children"]), (proposed["id"], proposed["children"]))
        self.assertEqual(approved["priority"], "P0")
        self.assertEqual(
            transition(approved, "Proposed", revalidation_invalidated=True)["status"],
            "Proposed",
        )
        recurring = transition(
            applied,
            "Proposed",
            recurrence_fingerprint=applied["fingerprint"],
            prior_event="EVT-12345678",
        )
        self.assertEqual((recurring["id"], recurring["prior_event"]), (applied["id"], "EVT-12345678"))
        self.assertEqual(transition(parked, "Proposed", evidence_changed=True)["status"], "Proposed")
        reprioritized = transition(parked, "Proposed", priority="P0")
        self.assertEqual((reprioritized["status"], reprioritized["priority"]), ("Proposed", "P0"))
        for record, target, kwargs in (
            (proposed, "Applied", {}),
            (approved, "Parked", {}),
            (approved, "Proposed", {}),
            (applied, "Proposed", {"recurrence_fingerprint": "f" * 64}),
            (parked, "Approved", {}),
        ):
            with self.subTest(source=record["status"], target=target):
                with self.assertRaises(ValueError):
                    transition(record, target, **kwargs)

        records = [
            semantic_finding("det-proposed", origin="deterministic", priority="P0"),
            semantic_finding("det-approved", origin="deterministic", status="Approved", priority="P2"),
            semantic_finding("semantic-p0", priority="P0"),
            semantic_finding("semantic-p1", priority="P1"),
            semantic_finding("semantic-p2", priority="P2"),
            semantic_finding("semantic-parked", priority="P2", status="Parked"),
            semantic_finding("semantic-applied", priority="P1", status="Applied"),
        ]
        persisted = select(records)
        self.assertEqual(
            {record["evidence"][0]["key"] for record in persisted},
            {"det-approved", "semantic-p0", "semantic-p1", "semantic-parked"},
        )
        changed = semantic_finding("different-identity")
        self.assertNotEqual(changed["id"], proposed["id"])

    def test_memory_capacity_preview_never_drops_active_or_protected_truth(self):
        preview_compaction = self.api("preview_memory_compaction")
        active = semantic_finding(priority="P0")
        obsolete = [
            semantic_finding(
                f"obsolete-{index}",
                priority="P2",
                status="Parked",
                summary="x" * 4096,
            )
            for index in range(80)
        ]
        state = valid_state()
        preview = preview_compaction(
            state,
            {"schema_version": 1, "findings": [active, *obsolete]},
            obsolete_ids=[record["id"] for record in obsolete],
        )
        self.assertEqual((preview["status"], preview["writes"]), ("memory-capacity", 0))
        self.assertIn(active["id"], preview["retained_finding_ids"])
        self.assertEqual(preview["protected_intent"], state["protected_intent"])
        self.assertEqual(preview["verified_documents"], state["verified_documents"])

    def test_dispositions_are_complete_unique_bounded_and_no_git_safe(self):
        prepare = self.api("prepare_dispositions")
        event_id = "EVT-12345678"
        items = [
            disposition(),
            disposition(
                "docs/legacy.md#unique-section",
                outcome="MIGRATED",
                target="docs/GUIDE.md#unique-section",
            ),
        ]
        removed = [item["item_id"] for item in items]
        inline = prepare(event_id, items, removed_items=removed, git_available=True)
        self.assertEqual(inline["storage"], "inline")
        self.assertLessEqual(inline["canonical_bytes"], 32 * 1024)
        self.assertEqual({item["item_id"] for item in inline["dispositions"]}, set(removed))
        for invalid_items, invalid_removed in (
            (items[:1], removed),
            ([items[0], items[0]], [removed[0]]),
        ):
            with self.assertRaises(ValueError):
                prepare(event_id, invalid_items, removed_items=invalid_removed, git_available=True)

        discarded = disposition(
            "docs/obsolete.md#<whole-file>",
            outcome="DISCARDED",
            target="docs/archive/obsolete.md",
        )
        safe = prepare(
            event_id,
            [discarded],
            removed_items=[discarded["item_id"]],
            git_available=False,
        )
        self.assertEqual(safe["dispositions"][0]["disposition"], "ARCHIVED")
        with self.assertRaises(ValueError):
            prepare(
                event_id,
                [discarded],
                removed_items=[discarded["item_id"]],
                git_available=False,
                hard_delete_approval={"accepted": True, "discarded_ids": ["wrong"]},
            )
        accepted = prepare(
            event_id,
            [discarded],
            removed_items=[discarded["item_id"]],
            git_available=False,
            hard_delete_approval={"accepted": True, "discarded_ids": [discarded["item_id"]]},
        )
        self.assertTrue(accepted["no_git_hard_delete_accepted"])
        self.assertEqual(accepted["discarded_ids"], [discarded["item_id"]])

    def test_external_manifest_digest_is_semantic_before_event_id_derivation(self):
        prepare = self.api("prepare_dispositions")
        build = self.api("build_verified_event")
        items = [
            disposition(f"docs/legacy-{index:04d}.md#<whole-file>")
            for index in range(360)
        ]
        transaction_id = "TXN-" + "a" * 16
        external = prepare(
            None,
            items,
            removed_items=[item["item_id"] for item in items],
            git_available=True,
            transaction_id=transaction_id,
        )
        self.assertEqual(external["storage"], "external")
        self.assertRegex(external["digest"], r"^sha256:[0-9a-f]{64}$")
        self.assertNotIn("event_id", json.loads(external["bytes"]))

        event = build(semantic_event("cleanup"), transaction_id=transaction_id, dispositions=external)
        changed_manifest = dict(external)
        changed_manifest["digest"] = "sha256:" + "f" * 64
        changed = build(semantic_event("cleanup"), transaction_id=transaction_id, dispositions=changed_manifest)
        self.assertNotEqual(event["event_id"], changed["event_id"])
        self.assertEqual(
            event["manifest"]["path"],
            f".diataxis/manifests/{event['event_id']}.json",
        )

    def test_init_retain_manifest_is_complete_external_and_transactionally_bound(self):
        prepare_manifest = self.api("prepare_dispositions")
        prepare_closeout = self.api("prepare_verified_closeout")
        apply_closeout = self.api("apply_verified_closeout")
        finding = semantic_finding()
        approvals = [self.approval(finding)]
        retained = [
            retained_disposition(f"{path}#<whole-file>")
            for path in BASE_DOCUMENTS
        ] + [
            retained_disposition(f"docs/adopted-{index:03d}.md#<whole-file>")
            for index in range(100)
        ]
        preview_manifest, init_evidence = self.init_manifest(
            retained,
            approvals=approvals,
        )
        rebound_manifest, _ = self.init_manifest(
            retained,
            approvals=approvals,
            transaction_id="TXN-AAAAAAAAAAAAAAAA",
        )

        self.assertEqual(preview_manifest["storage"], "external")
        self.assertEqual(preview_manifest["digest"], rebound_manifest["digest"])
        self.assertEqual(preview_manifest["bytes"], rebound_manifest["bytes"])
        payload = json.loads(preview_manifest["bytes"])
        self.assertEqual(len(payload["dispositions"]), 103)
        self.assertEqual({item["disposition"] for item in payload["dispositions"]}, {"RETAIN"})
        self.assertTrue(all("recovery" not in item for item in payload["dispositions"]))
        self.assertNotIn("transaction_id", payload)

        with self.assertRaises(ValueError):
            prepare_manifest(
                None,
                retained,
                removed_items=[],
                git_available=True,
                command="update",
                approval_bindings=approvals,
            )

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            create_uninitialized_repository(root)
            for item in retained:
                target = root / item["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(item["item_id"].encode("utf-8"))
            state = complete_init_state(
                root,
                manifest_identity=preview_manifest["manifest_identity"],
                result_corpus=init_evidence["corpus_transition"]["result"],
                document_results_digest=preview_manifest[
                    "document_results_digest"
                ],
                score_before=84,
                score_after=90,
            )
            state["protected_intent"] = []
            before = tree_bytes(root)
            plan = prepare_closeout(
                root,
                command="init",
                state=state,
                findings={"schema_version": 1, "findings": []},
                event=semantic_event("init", [finding["id"]]),
                approvals=approvals,
                dispositions=retained,
                removed_items=[],
                selected_boundary="docs",
                corpus_transition=init_evidence["corpus_transition"],
                document_results=init_evidence["document_results"],
            )
            self.assertEqual(tree_bytes(root), before)

            event = plan["event"]
            manifest_path = event["manifest"]["path"]
            manifest_bytes = preview_manifest["bytes"].encode("utf-8")
            self.assertEqual(plan["targets"][manifest_path], manifest_bytes)
            self.assertEqual(event["manifest_digest"], preview_manifest["digest"])
            self.assertEqual(event["manifest_identity"], preview_manifest["manifest_identity"])
            self.assertEqual(event["approval_bindings"], approvals)
            self.assertIn("manifest", event["transaction_targets"])
            planned_state = json.loads(plan["targets"][".diataxis/state.json"])
            self.assertEqual(
                planned_state["initialization"]["manifest_identity"],
                preview_manifest["manifest_identity"],
            )

            result = apply_closeout(
                root,
                plan,
                approved_transaction=plan["transaction_id"],
                verification=lambda: True,
            )
            self.assertEqual(result["status"], "applied")
            self.assertEqual((root / manifest_path).read_bytes(), manifest_bytes)
            self.assertEqual(docs_checker.inspect_operational_memory(root), [])

    def test_init_preparation_confines_boundary_and_manifest_routes_without_writes(self):
        prepare_manifest = self.api("prepare_dispositions")
        prepare_closeout = self.api("prepare_verified_closeout")
        finding = semantic_finding()
        approvals = [self.approval(finding)]

        def assert_rejected(
            *, selected_scope, selected_boundary, dispositions, expected_storage
        ):
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                create_repository(root)
                preview_manifest, init_evidence = self.init_manifest(
                    dispositions,
                    approvals=approvals,
                    selected_scope=selected_scope,
                )
                self.assertEqual(preview_manifest["storage"], expected_storage)
                state = complete_init_state(
                    root,
                    manifest_identity=preview_manifest["manifest_identity"],
                    result_corpus=init_evidence["corpus_transition"]["result"],
                    document_results_digest=preview_manifest[
                        "document_results_digest"
                    ],
                    selected_scope=selected_scope,
                    inspected_scope=selected_scope,
                    score_before=84,
                    score_after=90,
                )
                before = tree_bytes(root)

                with self.assertRaises(ValueError):
                    prepare_closeout(
                        root,
                        command="init",
                        state=state,
                        findings={"schema_version": 1, "findings": []},
                        event=semantic_event("init", [finding["id"]]),
                        approvals=approvals,
                        dispositions=dispositions,
                        removed_items=[],
                        selected_boundary=selected_boundary,
                        corpus_transition=init_evidence["corpus_transition"],
                        document_results=init_evidence["document_results"],
                    )

                self.assertEqual(tree_bytes(root), before)

        inline = [retained_disposition()]
        assert_rejected(
            selected_scope="docs",
            selected_boundary=".",
            dispositions=inline,
            expected_storage="external",
        )
        assert_rejected(
            selected_scope="docs",
            selected_boundary="docs",
            dispositions=[retained_disposition("other/private.md#<whole-file>")],
            expected_storage="external",
        )
        for private_root in (".local", ".LOCAL"):
            private = [
                retained_disposition(
                    f"{private_root}/private-campaign.md#<whole-file>"
                )
            ]
            private_evidence = init_manifest_evidence(
                private,
                selected_scope=".",
            )
            with self.assertRaises(ValueError):
                prepare_manifest(
                    None,
                    private,
                    removed_items=[],
                    git_available=True,
                    command="init",
                    approval_bindings=approvals,
                    **private_evidence,
                )
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                create_repository(root)
                state = complete_init_state(
                    root,
                    manifest_identity="a" * 64,
                    result_corpus=private_evidence["corpus_transition"]["result"],
                    document_results_digest=(
                        "sha256:" + hashlib.sha256(canonical_bytes([])).hexdigest()
                    ),
                    selected_scope=".",
                    inspected_scope=".",
                    score_before=84,
                    score_after=90,
                )
                before = tree_bytes(root)
                with self.assertRaises(ValueError):
                    prepare_closeout(
                        root,
                        command="init",
                        state=state,
                        findings={"schema_version": 1, "findings": []},
                        event=semantic_event("init", [finding["id"]]),
                        approvals=approvals,
                        dispositions=private,
                        removed_items=[],
                        selected_boundary=".",
                        **private_evidence,
                    )
                self.assertEqual(tree_bytes(root), before)
        external = [
            retained_disposition(f"docs/adopted-{index:03d}.md#<whole-file>")
            for index in range(102)
        ]
        external.append(retained_disposition("other/private.md#<whole-file>"))
        assert_rejected(
            selected_scope="docs",
            selected_boundary="docs",
            dispositions=external,
            expected_storage="external",
        )

    def test_init_manifest_rejects_route_exposure_in_disposition_reason(self):
        prepare_manifest = self.api("prepare_dispositions")
        dispositions = [retained_disposition()]
        dispositions[0]["reason"] = "See route=.local/secret.md"
        evidence = init_manifest_evidence(dispositions)

        with self.assertRaisesRegex(ValueError, "disposition reason"):
            prepare_manifest(
                None,
                dispositions,
                removed_items=[],
                git_available=True,
                command="init",
                approval_bindings=[],
                **evidence,
            )

    def test_init_confinement_covers_target_and_recovery_routes_without_writes(self):
        prepare_manifest = self.api("prepare_dispositions")
        prepare_closeout = self.api("prepare_verified_closeout")
        finding = semantic_finding()
        approvals = [self.approval(finding)]

        def routed_disposition(identity, *, target, recovery_path):
            return init_disposition(
                identity,
                outcome="MIGRATED",
                target=target,
                recovery_path=recovery_path,
            )

        for field, private_root in (
            ("target", ".local"),
            ("target", ".LOCAL"),
            ("recovery", ".local"),
            ("recovery", ".LOCAL"),
        ):
            private_route = f"{private_root}/private.md"
            item = routed_disposition(
                "docs/legacy.md#<whole-file>",
                target=private_route if field == "target" else "docs/current.md",
                recovery_path=(
                    private_route if field == "recovery" else "docs/archive/legacy.md"
                ),
            )
            init_evidence = init_manifest_evidence([item])
            with self.subTest(field=field, private_root=private_root), self.assertRaises(
                ValueError
            ):
                prepare_manifest(
                    None,
                    [item],
                    removed_items=[item["item_id"]],
                    git_available=True,
                    command="init",
                    approval_bindings=approvals,
                    **init_evidence,
                )

        def assert_closeout_rejected(dispositions, expected_storage):
            removed_items = [item["item_id"] for item in dispositions]
            preview_manifest, init_evidence = self.init_manifest(
                dispositions,
                approvals=approvals,
            )
            self.assertEqual(preview_manifest["storage"], expected_storage)
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                create_repository(root)
                state = complete_init_state(
                    root,
                    manifest_identity=preview_manifest["manifest_identity"],
                    result_corpus=init_evidence["corpus_transition"]["result"],
                    document_results_digest=preview_manifest[
                        "document_results_digest"
                    ],
                    score_before=84,
                    score_after=90,
                )
                before = tree_bytes(root)
                with self.assertRaises(ValueError):
                    prepare_closeout(
                        root,
                        command="init",
                        state=state,
                        findings={"schema_version": 1, "findings": []},
                        event=semantic_event("init", [finding["id"]]),
                        approvals=approvals,
                        dispositions=dispositions,
                        removed_items=removed_items,
                        selected_boundary="docs",
                        corpus_transition=init_evidence["corpus_transition"],
                        document_results=init_evidence["document_results"],
                    )
                self.assertEqual(tree_bytes(root), before)

        inline = [
            routed_disposition(
                "docs/legacy.md#<whole-file>",
                target="docs/current.md",
                recovery_path="outside/recovery.md",
            )
        ]
        with self.subTest(storage="inline"):
            assert_closeout_rejected(inline, "external")

        external = [
            routed_disposition(
                f"docs/legacy-{index:03d}.md#<whole-file>",
                target=f"docs/current-{index:03d}.md",
                recovery_path=f"docs/archive/legacy-{index:03d}.md",
            )
            for index in range(103)
        ]
        external[-1]["target"] = "outside/current-102.md"
        with self.subTest(storage="external"):
            assert_closeout_rejected(external, "external")

    def test_init_prepare_rejects_anchored_and_disallowed_private_targets_without_writes(
        self,
    ):
        prepare_manifest = self.api("prepare_dispositions")
        cases = []
        for field, private_route in (
            ("target", ".local#secret"),
            ("target", ".LOCAL#secret"),
            ("recovery", ".local#secret"),
            ("recovery", ".LOCAL#secret"),
        ):
            item = init_disposition(
                "docs/legacy.md#<whole-file>",
                outcome="MIGRATED",
                target="docs/current.md",
            )
            if field == "target":
                item["target"] = private_route
            else:
                item["recovery"]["path"] = private_route
                item["recovery"]["digest"] = (
                    "sha256:" + hashlib.sha256(private_route.encode()).hexdigest()
                )
            cases.append((f"anchored-{field}-{private_route}", item))

        discarded = init_disposition(
            "docs/obsolete.md#<whole-file>", outcome="DISCARDED"
        )
        discarded["target"] = ".local/private.md"
        cases.append(("discarded-extraneous-target", discarded))

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            create_repository(root)
            before = tree_bytes(root)
            for label, item in cases:
                init_evidence = init_manifest_evidence([item])
                with self.subTest(case=label), self.assertRaises(ValueError):
                    prepare_manifest(
                        None,
                        [item],
                        removed_items=[item["item_id"]],
                        git_available=True,
                        command="init",
                        approval_bindings=[],
                        **init_evidence,
                    )
                self.assertEqual(tree_bytes(root), before)

    def test_event_builder_links_recurrence_and_timestamp_is_audit_only(self):
        build = self.api("build_verified_event")
        finding = semantic_finding()
        recurrence = {
            "id": finding["id"],
            "fingerprint": finding["fingerprint"],
            "prior_event": "EVT-12345678",
        }
        first = build(
            semantic_event("fix", [finding["id"]]),
            transaction_id="TXN-" + "b" * 16,
            recurring_findings=[recurrence],
        )
        moved = build(
            {**semantic_event("fix", [finding["id"]]), "completed_at": "2030-01-01T00:00:00Z"},
            transaction_id="TXN-" + "b" * 16,
            recurring_findings=[recurrence],
        )
        self.assertEqual(first["event_id"], moved["event_id"])
        self.assertEqual(first["recurrences"][0]["prior_event"], "EVT-12345678")


class Task7TransactionTests(Task7ContractCase):
    def test_transaction_identity_excludes_audit_time_but_binds_semantic_result(self):
        prepare = self.api("prepare_verified_closeout")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            create_repository(root)
            finding = semantic_finding()
            common = {
                "command": "update",
                "state": valid_state(),
                "findings": {"schema_version": 1, "findings": []},
                "approvals": [self.approval(finding)],
            }
            first_event = semantic_event("update", [finding["id"]])
            moved_event = {
                **first_event,
                "completed_at": "2030-01-01T00:00:00Z",
            }
            changed_event = {**first_event, "score_after": 91}
            before = tree_bytes(root)
            first = prepare(root, event=first_event, **common)
            moved = prepare(root, event=moved_event, **common)
            changed = prepare(root, event=changed_event, **common)
            narrowed = prepare(root, event=first_event, selected_boundary="docs", **common)
            self.assertEqual(first["transaction_id"], moved["transaction_id"])
            self.assertEqual(first["event"]["event_id"], moved["event"]["event_id"])
            self.assertNotEqual(first["transaction_id"], changed["transaction_id"])
            self.assertNotEqual(first["transaction_id"], narrowed["transaction_id"])
            self.assertEqual(tree_bytes(root), before)

    def test_transaction_identity_binds_policy_approval_start_roles_and_order(self):
        apply = self.api("apply_verified_closeout")

        def mutate_start(plan):
            plan["starting_digests"][".diataxis/state.json"] = "sha256:ABSENT"

        def mutate_approval(plan):
            plan["approvals"][0]["fingerprint"] = "0" * 64

        def mutate_roles(plan):
            plan["target_roles"][".diataxis/state.json"] = "findings"

        def mutate_target_type(plan):
            plan["targets"][".diataxis/state.json"] = plan["targets"][
                ".diataxis/state.json"
            ].decode("utf-8")

        def remove_target(plan):
            plan["targets"].pop(".diataxis/state.json")

        mutations = {
            "transaction-schema": lambda plan: plan.update(transaction_schema_version=99),
            "transaction-policy": lambda plan: plan.update(transaction_policy_version="other"),
            "selected-boundary": lambda plan: plan.update(selected_boundary="docs"),
            "visibility": lambda plan: plan.update(visibility=["local-only"]),
            "approval": mutate_approval,
            "starting-digest": mutate_start,
            "target-role": mutate_roles,
            "replacement-order": lambda plan: plan["replacement_order"].reverse(),
            "target-byte-type": mutate_target_type,
            "missing-target": remove_target,
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                create_repository(root)
                plan = self.prepare(root)
                before = tree_bytes(root)
                tampered = copy.deepcopy(plan)
                mutate(tampered)
                result = apply(
                    root,
                    tampered,
                    approved_transaction=plan["transaction_id"],
                    verification=lambda: True,
                )
                self.assertEqual(
                    (result["status"], result["classification"]),
                    ("closeout-failed", "transaction-authorization-mismatch"),
                )
                self.assertFalse(result["successful_event_recorded"])
                self.assertEqual(tree_bytes(root), before)

    def test_approved_identity_rejects_coordinated_complete_result_substitution(self):
        apply = self.api("apply_verified_closeout")
        lifecycle_module = self.module("lifecycle")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            create_repository(root)
            plan = self.prepare_comprehensive(root)
            before = tree_bytes(root)
            tampered = copy.deepcopy(plan)

            state_path = ".diataxis/state.json"
            findings_path = ".diataxis/findings.json"
            events_path = ".diataxis/events.jsonl"
            state = json.loads(tampered["targets"][state_path])
            findings = valid_findings()
            findings["findings"][0]["summary"] = "Coordinated substituted finding."
            event = copy.deepcopy(tampered["event"])
            state["rubric"]["last_verified_score"] = 77
            event["score_after"] = 77
            event["summary"] = "Coordinated substituted result."

            local_map = json.loads(tampered["targets"][".diataxis/local-map.json"])
            local_map["routes"][0]["topics"].append("substituted-result")
            local_bytes = (
                json.dumps(local_map, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode("utf-8")
            event["local_map_digest"] = "sha256:" + hashlib.sha256(local_bytes).hexdigest()
            tampered["targets"][".diataxis/local-map.json"] = local_bytes

            old_manifest_path = event["manifest"]["path"]
            manifest = json.loads(tampered["targets"].pop(old_manifest_path))
            manifest["dispositions"][0]["reason"] = "Coordinated substituted disposition."
            manifest_bytes = (
                json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode("utf-8")
            manifest_digest = "sha256:" + hashlib.sha256(manifest_bytes).hexdigest()
            event["manifest_digest"] = manifest_digest
            event["manifest"]["digest"] = manifest_digest

            protected = copy.deepcopy(tampered["protected_preview"])
            protected["protected_evidence"][0]["compatibility_evidence"].append(
                "coordinated-substituted-evidence"
            )
            event["protected_preview_digest"] = "sha256:" + hashlib.sha256(
                (json.dumps(protected, sort_keys=True, separators=(",", ":")) + "\n").encode()
            ).hexdigest()
            tampered["protected_preview"] = protected

            event["state_semantic_digest"] = lifecycle_module.state_semantic_digest(state)
            event["findings_digest"] = lifecycle_module.findings_digest(findings)
            event["event_id"] = docs_checker.event_id(docs_checker.event_fingerprint(event))
            event["manifest"]["path"] = (
                f".diataxis/manifests/{event['event_id']}.json"
            )
            new_manifest_path = event["manifest"]["path"]
            tampered["targets"][new_manifest_path] = manifest_bytes
            tampered["starting_digests"].pop(old_manifest_path)
            tampered["starting_digests"][new_manifest_path] = "sha256:ABSENT"
            state["last_completed_event"] = event["event_id"]
            for record in state["verified_documents"]:
                record["verified_event"] = event["event_id"]
            event["state_semantic_digest"] = lifecycle_module.state_semantic_digest(state)

            prior_events = [
                json.loads(line)
                for line in tampered["targets"][events_path].splitlines()
                if line.strip()
            ]
            prior_events[-1] = event
            tampered["targets"][events_path] = b"".join(
                (
                    json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n"
                ).encode("utf-8")
                for item in prior_events
            )
            tampered["targets"][state_path] = (
                json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode("utf-8")
            tampered["targets"][findings_path] = (
                json.dumps(findings, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode("utf-8")
            tampered["event"] = event

            result = apply(
                root,
                tampered,
                approved_transaction=plan["transaction_id"],
                verification=lambda: True,
                protected_preview=protected,
                protected_verification=lambda: True,
            )
            self.assertEqual(result.get("status"), "closeout-failed")
            self.assertEqual(
                result.get("classification"),
                "transaction-authorization-mismatch",
            )
            self.assertFalse(result["successful_event_recorded"])
            self.assertEqual(tree_bytes(root), before)

    def test_read_only_commands_never_prepare_or_write_control_plane(self):
        prepare = self.api("prepare_verified_closeout")
        for command in READ_ONLY_COMMANDS:
            with self.subTest(command=command), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                control = create_repository(root)
                before = file_snapshot(control)
                finding = semantic_finding()
                with self.assertRaisesRegex(ValueError, "read-only command"):
                    prepare(
                        root,
                        command=command,
                        state=valid_state(),
                        findings=valid_findings(),
                        event=semantic_event(command, [finding["id"]]),
                        approvals=[self.approval(finding)],
                    )
                self.assertEqual(file_snapshot(control), before)

    def test_all_approved_mutations_use_one_verified_event_last_protocol(self):
        apply = self.api("apply_verified_closeout")
        for command in MUTATING_COMMANDS:
            with self.subTest(command=command), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                if command == "init":
                    create_uninitialized_repository(root)
                else:
                    create_repository(root)
                plan = self.prepare(root, command=command)
                self.assertEqual(plan["status"], "approval-required")
                self.assertEqual(plan["writes"], 0)
                result = apply(
                    root,
                    plan,
                    approved_transaction=plan["transaction_id"],
                    verification=lambda: True,
                )
                self.assertEqual(result["status"], "applied")
                self.assertTrue(result["successful_event_recorded"])
                events = docs_checker.load_operational_events(root)
                self.assertEqual(events[-1]["event_id"], plan["event"]["event_id"])
                self.assertFalse(list((root / ".diataxis").glob(f"{TRANSACTION_PREFIX}*")))

    def test_verification_failure_writes_zero_control_bytes_or_mtimes(self):
        apply = self.api("apply_verified_closeout")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            control = create_repository(root)
            plan = self.prepare(root)
            before = file_snapshot(control)
            result = apply(
                root,
                plan,
                approved_transaction=plan["transaction_id"],
                verification=lambda: False,
            )
            self.assertEqual(result["status"], "verification-failed")
            self.assertFalse(result["successful_event_recorded"])
            self.assertEqual(file_snapshot(control), before)

    def test_compare_before_write_rejects_every_stale_target(self):
        apply = self.api("apply_verified_closeout")
        for target in ("state.json", "findings.json", "events.jsonl"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                control = create_repository(root)
                plan = self.prepare(root)
                path = control / target
                path.write_bytes(path.read_bytes() + b" ")
                stale = tree_bytes(root)
                result = apply(
                    root,
                    plan,
                    approved_transaction=plan["transaction_id"],
                    verification=lambda: True,
                )
                self.assertEqual(result["status"], "stale-target")
                self.assertEqual(result["path"], f".diataxis/{target}")
                self.assertEqual(tree_bytes(root), stale)

    def test_approved_transaction_rejects_plan_payload_tampering(self):
        apply = self.api("apply_verified_closeout")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            create_repository(root)
            plan = self.prepare(root)
            before = tree_bytes(root)
            tampered = copy.deepcopy(plan)
            state_path = ".diataxis/state.json"
            state = json.loads(tampered["targets"][state_path])
            state["rubric"]["last_verified_status"] = "coordinated-different-result"
            tampered["targets"][state_path] = (
                json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode()

            result = apply(
                root,
                tampered,
                approved_transaction=plan["transaction_id"],
                verification=lambda: True,
            )
            self.assertEqual(result["status"], "closeout-failed")
            self.assertFalse(result["successful_event_recorded"])
            self.assertEqual(tree_bytes(root), before)

    def test_stage_files_are_same_directory_fsynced_and_event_replace_is_last(self):
        apply = self.api("apply_verified_closeout")
        io_module = self.module("lifecycle_io")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            create_repository(root)
            plan = self.prepare(root)
            replace_targets = []
            original_replace = io_module.os.replace

            def observe(source, target):
                source = Path(source)
                target = Path(target)
                self.assertEqual(source.parent, target.parent)
                self.assertTrue(source.name.startswith(TRANSACTION_PREFIX))
                replace_targets.append(target.name)
                return original_replace(source, target)

            with mock.patch.object(io_module.os, "replace", side_effect=observe):
                result = apply(
                    root,
                    plan,
                    approved_transaction=plan["transaction_id"],
                    verification=lambda: True,
                )
            self.assertEqual(result["status"], "applied")
            self.assertEqual(replace_targets[-1], "events.jsonl")
            self.assertEqual(replace_targets[:2], ["state.json", "findings.json"])

    def test_every_low_level_write_fsync_replace_and_boundary_failure_has_no_loss(self):
        apply = self.api("apply_verified_closeout")
        io_module = self.module("lifecycle_io")
        primitive_names = ("write", "fsync", "replace")

        for primitive_name in primitive_names:
            with self.subTest(probe=primitive_name), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                create_repository(root)
                plan = self.prepare(root)
                primitive = getattr(io_module.os, primitive_name)
                count = 0

                def count_calls(*args, **kwargs):
                    nonlocal count
                    count += 1
                    return primitive(*args, **kwargs)

                with mock.patch.object(io_module.os, primitive_name, side_effect=count_calls):
                    result = apply(
                        root,
                        plan,
                        approved_transaction=plan["transaction_id"],
                        verification=lambda: True,
                    )
                self.assertEqual(result["status"], "applied")
                self.assertGreater(count, 0, f"transaction does not exercise os.{primitive_name}")

            for ordinal in range(1, count + 1):
                for failure in (OSError("forced I/O failure"), KeyboardInterrupt()):
                    with (
                        self.subTest(primitive=primitive_name, ordinal=ordinal, failure=type(failure).__name__),
                        tempfile.TemporaryDirectory() as td,
                    ):
                        root = Path(td)
                        create_repository(root)
                        plan = self.prepare(root)
                        before = tree_bytes(root)
                        primitive = getattr(io_module.os, primitive_name)
                        calls = 0

                        def fail_at(*args, **kwargs):
                            nonlocal calls
                            calls += 1
                            if calls == ordinal:
                                raise failure
                            return primitive(*args, **kwargs)

                        with mock.patch.object(io_module.os, primitive_name, side_effect=fail_at):
                            if isinstance(failure, KeyboardInterrupt):
                                with self.assertRaises(KeyboardInterrupt):
                                    apply(
                                        root,
                                        plan,
                                        approved_transaction=plan["transaction_id"],
                                        verification=lambda: True,
                                    )
                            else:
                                result = apply(
                                    root,
                                    plan,
                                    approved_transaction=plan["transaction_id"],
                                    verification=lambda: True,
                                )
                                self.assertEqual(result["status"], "closeout-failed")
                        self.assertEqual(tree_bytes(root), before)

    def test_directory_fsync_failure_does_not_leave_unregistered_recovery_temp(self):
        """A marker-fsync failure must clean its staged marker or publish recovery."""
        apply = self.api("apply_verified_closeout")
        io_module = self.module("lifecycle_io")
        for failure in (OSError("forced directory-fsync failure"), KeyboardInterrupt()):
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                create_repository(root)
                plan = self.prepare(root)
                before = tree_bytes(root)
                original_directory_fsync = io_module._directory_fsync
                calls = 0

                def fail_first_directory_fsync(directory):
                    nonlocal calls
                    calls += 1
                    if calls == 1:
                        raise failure
                    return original_directory_fsync(directory)

                with mock.patch.object(
                    io_module,
                    "_directory_fsync",
                    side_effect=fail_first_directory_fsync,
                ):
                    if isinstance(failure, KeyboardInterrupt):
                        with self.assertRaises(KeyboardInterrupt):
                            apply(
                                root,
                                plan,
                                approved_transaction=plan["transaction_id"],
                                verification=lambda: True,
                            )
                    else:
                        result = apply(
                            root,
                            plan,
                            approved_transaction=plan["transaction_id"],
                            verification=lambda: True,
                        )
                        self.assertEqual(result["status"], "closeout-failed")
                self.assertEqual(tree_bytes(root), before)
                self.assertEqual(
                    list((root / ".diataxis").glob(".docs-txn-*-recovery.tmp")),
                    [],
                )

    def test_named_target_boundaries_reject_semantic_failure_and_post_replace_crash(self):
        apply = self.api("apply_verified_closeout")
        io_module = self.module("lifecycle_io")
        failure_kinds = (ValueError("semantic verification failed"),)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            create_repository(root)
            plan = self.prepare_comprehensive(root)
            self.assertIn(
                "replacement_order",
                plan,
                "closeout plans must expose their authorized deterministic replacement order",
            )
            order = plan["replacement_order"]
            self.assertEqual(order[-1], ".diataxis/events.jsonl")
            self.assertEqual(set(order), set(plan["targets"]))

        for relative in order:
            for failure in failure_kinds:
                with self.subTest(phase="verify", path=relative), tempfile.TemporaryDirectory() as td:
                    root = Path(td)
                    create_repository(root)
                    plan = self.prepare_comprehensive(root)
                    before = tree_bytes(root)
                    original_verify = io_module._verify_staged

                    def reject_selected(path, data, staged, selected_root):
                        if path == relative:
                            raise failure
                        return original_verify(path, data, staged, selected_root)

                    with mock.patch.object(io_module, "_verify_staged", side_effect=reject_selected):
                        result = apply(
                            root,
                            plan,
                            approved_transaction=plan["transaction_id"],
                            verification=lambda: True,
                            protected_verification=lambda: True,
                        )
                    self.assertEqual(result["status"], "closeout-failed")
                    self.assertEqual(result["boundary"], f"verify:{relative}")
                    self.assertEqual(tree_bytes(root), before)

            for failure in (OSError("forced boundary failure"), KeyboardInterrupt()):
                with (
                    self.subTest(
                        phase="post-replace",
                        path=relative,
                        failure=type(failure).__name__,
                    ),
                    tempfile.TemporaryDirectory() as td,
                ):
                    root = Path(td)
                    create_repository(root)
                    plan = self.prepare_comprehensive(root)
                    before = tree_bytes(root)
                    original_replace = io_module.os.replace
                    raised = False

                    def replace_then_fail(source, target):
                        nonlocal raised
                        result = original_replace(source, target)
                        target_relative = Path(target).relative_to(root).as_posix()
                        if target_relative == relative and not raised:
                            raised = True
                            raise failure
                        return result

                    with mock.patch.object(io_module.os, "replace", side_effect=replace_then_fail):
                        if isinstance(failure, KeyboardInterrupt):
                            with self.assertRaises(KeyboardInterrupt):
                                apply(
                                    root,
                                    plan,
                                    approved_transaction=plan["transaction_id"],
                                    verification=lambda: True,
                                    protected_verification=lambda: True,
                                )
                        else:
                            result = apply(
                                root,
                                plan,
                                approved_transaction=plan["transaction_id"],
                                verification=lambda: True,
                                protected_verification=lambda: True,
                            )
                            self.assertEqual(result["status"], "closeout-failed")
                            self.assertEqual(result["boundary"], f"replace:{relative}")
                    self.assertTrue(raised)
                    self.assertEqual(tree_bytes(root), before)

    def test_exdev_and_windows_sharing_violations_are_stable_failures(self):
        apply = self.api("apply_verified_closeout")
        io_module = self.module("lifecycle_io")
        failures = []
        exdev = OSError(errno.EXDEV, "private path must not escape")
        sharing = OSError("private Windows path must not escape")
        sharing.winerror = 32
        failures.extend(((exdev, "cross-device-atomic-replace-unavailable"), (sharing, "target-sharing-violation")))
        for error, classification in failures:
            with self.subTest(classification=classification), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                create_repository(root)
                plan = self.prepare(root)
                before = tree_bytes(root)
                with mock.patch.object(io_module.os, "replace", side_effect=error):
                    result = apply(
                        root,
                        plan,
                        approved_transaction=plan["transaction_id"],
                        verification=lambda: True,
                    )
                self.assertEqual(result["classification"], classification)
                self.assertNotIn(str(root), json.dumps(result, sort_keys=True))
                self.assertEqual(tree_bytes(root), before)

    def test_failed_rollback_leaves_durable_read_only_recovery_evidence(self):
        apply = self.api("apply_verified_closeout")
        io_module = self.module("lifecycle_io")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            control = create_repository(root)
            plan = self.prepare_comprehensive(root)
            before_tree = tree_bytes(root)
            before_semantics = {
                "state": docs_checker.load_operational_state(root),
                "findings": docs_checker.load_operational_findings(root),
                "events": docs_checker.load_operational_events(root),
            }
            order = plan["replacement_order"]
            findings_relative = ".diataxis/findings.json"
            later_relative = order[order.index(findings_relative) + 1]
            original_replace = io_module.os.replace
            findings_installed = False

            def fail_later_and_lose_findings_rollback(source, target):
                nonlocal findings_installed
                relative = Path(target).relative_to(root).as_posix()
                if relative == later_relative:
                    raise OSError("forced later transaction boundary failure")
                if relative == findings_relative:
                    if findings_installed:
                        Path(source).unlink()
                        raise OSError("forced findings rollback failure")
                    findings_installed = True
                return original_replace(source, target)

            with mock.patch.object(
                io_module.os,
                "replace",
                side_effect=fail_later_and_lose_findings_rollback,
            ):
                result = apply(
                    root,
                    plan,
                    approved_transaction=plan["transaction_id"],
                    verification=lambda: True,
                    protected_verification=lambda: True,
                )

            self.assertEqual(result["status"], "closeout-failed")
            self.assertFalse(result["control_plane_rolled_back"])
            self.assertFalse(result["successful_event_recorded"])
            after_tree = tree_bytes(root)
            changed = {
                path
                for path in before_tree.keys() | after_tree.keys()
                if before_tree.get(path) != after_tree.get(path)
            }
            recovery_markers = {
                path
                for path in changed
                if Path(path).name.startswith(TRANSACTION_PREFIX)
            }
            self.assertEqual(changed - recovery_markers, {findings_relative})
            self.assertEqual(len(recovery_markers), 1)
            after_semantics = {
                "state": docs_checker.load_operational_state(root),
                "findings": docs_checker.load_operational_findings(root),
                "events": docs_checker.load_operational_events(root),
            }
            self.assertEqual(after_semantics["state"], before_semantics["state"])
            self.assertEqual(after_semantics["events"], before_semantics["events"])
            self.assertNotEqual(after_semantics["findings"], before_semantics["findings"])
            self.assertFalse((control / "local-map.json").exists())
            self.assertEqual(
                {
                    path
                    for path in after_tree
                    if path.startswith(".diataxis/manifests/")
                },
                {
                    path
                    for path in before_tree
                    if path.startswith(".diataxis/manifests/")
                },
            )
            conflicts = docs_checker.inspect_operational_memory(root)
            self.assertTrue(
                any(
                    finding["kind"] == "state-conflict"
                    and finding["priority"] == "P0"
                    and "recovery" in finding["detail"]
                    for finding in conflicts
                )
            )
            self.assertNotIn(str(root), json.dumps(conflicts, sort_keys=True))

    def test_restart_detects_orphan_temp_and_every_torn_control_set(self):
        apply = self.api("apply_verified_closeout")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            control = create_repository(root)
            orphan = control / f"{TRANSACTION_PREFIX}ORPHAN-state.json.tmp"
            orphan.write_bytes(b"private unfinished bytes")
            findings = docs_checker.inspect_operational_memory(root)
            self.assertTrue(
                any(item["kind"] == "state-conflict" and item["priority"] == "P0" for item in findings)
            )
            orphan.unlink()

            before = {
                name: (control / name).read_bytes()
                for name in ("state.json", "findings.json", "events.jsonl")
            }
            plan = self.prepare(root)
            result = apply(
                root,
                plan,
                approved_transaction=plan["transaction_id"],
                verification=lambda: True,
            )
            self.assertEqual(result["status"], "applied")
            after = {name: (control / name).read_bytes() for name in before}
            for reverted in before:
                with self.subTest(reverted=reverted):
                    (control / reverted).write_bytes(before[reverted])
                    conflicts = docs_checker.inspect_operational_memory(root)
                    self.assertTrue(any(item["kind"] == "state-conflict" for item in conflicts))
                    (control / reverted).write_bytes(after[reverted])

    def test_state_conflict_recovery_rejects_caller_supplied_reconstruction(self):
        preview_recovery = self.api("preview_state_conflict_recovery")
        io_module = self.module("lifecycle_io")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            control = create_repository(root)
            (control / "state.json").write_text(
                "<<<<<<< ours\n{}\n=======\n{}\n>>>>>>> theirs\n",
                encoding="utf-8",
            )
            before = tree_bytes(root)
            with self.assertRaises(TypeError):
                preview_recovery(
                    root,
                    canonical_state=valid_state(),
                    recomputed_findings=valid_findings(),
                    surviving_events=[valid_event()],
                )
            self.assertEqual(tree_bytes(root), before)
        self.assertFalse(hasattr(io_module, "_preview_state_conflict_recovery_legacy"))
        self.assertFalse(hasattr(io_module, "_apply_state_conflict_recovery_legacy"))


class Task7LocalAndProtectedTests(Task7ContractCase):
    def test_protected_intent_anchor_and_contradiction_are_blocking_and_read_only(self):
        guard = self.api("validate_protected_intent_change")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            control = create_repository(root)
            before = file_snapshot(control)
            (root / "docs" / "DESIGN.md").write_text("# Design\n\n## Renamed\n", encoding="utf-8")
            findings, _ = docs_checker.check(root)
            missing = [item for item in findings if item["kind"] == "protected-intent-missing"]
            self.assertEqual(len(missing), 1)
            self.assertEqual(missing[0]["priority"], "P0")
            self.assertEqual(file_snapshot(control), before)

            (root / "docs" / "DESIGN.md").write_text("# Design\n\n## Visual language\n", encoding="utf-8")
            contradiction = [{"intent_id": "INTENT-001", "effect": "contradicts"}]
            blocked = guard(root, valid_state()["protected_intent"], contradiction)
            allowed = guard(
                root,
                valid_state()["protected_intent"],
                contradiction,
                exact_intent_change_authorizations=["INTENT-001"],
            )
            self.assertEqual(blocked["status"], "blocked")
            self.assertEqual(blocked["findings"][0]["priority"], "P0")
            self.assertEqual(allowed["status"], "authorized-intent-change")

    def test_local_hashes_are_scope_bound_and_timestamp_independent(self):
        verify = self.api("verify_local_route_hashes")
        route = local_route()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / route["route"]
            path.parent.mkdir(parents=True)
            path.write_text("# 0.3.0\n\nNine staged PRs.\n", encoding="utf-8")
            first = verify(root, [route], selected_scope=".local/0.3.0-campaign", byte_limit=64 * 1024)
            path.touch()
            second = verify(root, [route], selected_scope=".local/0.3.0-campaign", byte_limit=64 * 1024)
            self.assertEqual(first["routes"][0]["content_digest"], second["routes"][0]["content_digest"])
            self.assertEqual(first["content_reads"], 1)
            self.assertNotIn("mtime", json.dumps(first, sort_keys=True).casefold())
            with self.assertRaises(ValueError):
                verify(root, [local_route("docs/STATE.md")], selected_scope=".local", byte_limit=64 * 1024)

    def test_local_map_requires_mechanical_git_ignore_proof_and_never_leaks_shared(self):
        apply = self.api("apply_verified_closeout")
        verify = self.api("verify_local_route_hashes")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            create_repository(root)
            try:
                subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
            except (OSError, subprocess.CalledProcessError):
                self.skipTest("Git unavailable")
            route = local_route(".local/private-plan.md")
            path = root / route["route"]
            path.parent.mkdir()
            path.write_text("# Private plan\n", encoding="utf-8")
            verified = verify(root, [route], selected_scope=".local", byte_limit=64 * 1024)
            local_map = {
                "schema_version": 2,
                "repository_identity": "a" * 64,
                "worktree_identity": "b" * 64,
                "routes": verified["routes"],
            }
            before = tree_bytes(root)
            not_ignored = self.prepare(root, local_map=local_map)
            self.assertEqual(not_ignored["status"], "requires_user_action")
            self.assertEqual(not_ignored["reason"], "local-map-path-not-ignored")
            self.assertEqual(tree_bytes(root), before)
            self.assertFalse((root / ".diataxis" / "local-map.json").exists())

            (root / ".gitignore").write_text(".diataxis/local-map.json\n", encoding="utf-8")
            plan = self.prepare(root, local_map=local_map)
            result = apply(
                root,
                plan,
                approved_transaction=plan["transaction_id"],
                verification=lambda: True,
            )
            self.assertEqual(result["status"], "applied")
            inspected = docs_checker.inspect_local_map(
                root,
                repository_identity="a" * 64,
                worktree_identity="b" * 64,
            )
            self.assertEqual(inspected["status"], "present-uninspected")
            self.assertEqual(inspected["schema_version"], 2)
            self.assertEqual(inspected["content_reads"], 0)
            self.assertEqual(
                inspected["routes"][0]["content_digest"],
                verified["routes"][0]["content_digest"],
            )
            shared = b"".join(
                (root / ".diataxis" / name).read_bytes()
                for name in ("state.json", "findings.json", "events.jsonl")
            )
            for private in (b"private-plan.md", b".local/", b"campaign-plan"):
                self.assertNotIn(private, shared)

            forbidden = json.loads(json.dumps(local_map))
            forbidden["routes"][0]["prompt"] = "hidden reasoning"
            with self.assertRaisesRegex(ValueError, "local map"):
                self.prepare(root, local_map=forbidden)

            installed = root / ".diataxis" / "local-map.json"
            swapped = json.loads(installed.read_text(encoding="utf-8"))
            swapped["routes"][0]["content_digest"] = "sha256-text:" + "0" * 64
            installed.write_text(json.dumps(swapped, sort_keys=True) + "\n", encoding="utf-8")
            self.assertTrue(
                any(
                    item["kind"] == "state-conflict" and item["priority"] == "P0"
                    for item in docs_checker.inspect_operational_memory(root)
                )
            )

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            create_repository(root)
            route = local_route(".local/private-plan.md")
            path = root / route["route"]
            path.parent.mkdir()
            path.write_text("# Private plan\n", encoding="utf-8")
            verified = verify(root, [route], selected_scope=".local", byte_limit=64 * 1024)
            no_git = self.prepare(
                root,
                local_map={
                    "schema_version": 2,
                    "repository_identity": "a" * 64,
                    "worktree_identity": "b" * 64,
                    "routes": verified["routes"],
                },
            )
            self.assertEqual(no_git["status"], "requires_user_action")
            self.assertEqual(no_git["reason"], "local-map-git-protection-unavailable")
            self.assertFalse(no_git["git_ignore_protected"])

    def test_git_proof_rejects_dubious_ownership_without_bypass_or_write(self):
        io_module = self.module("lifecycle_io")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            create_repository(root)
            local_map = {
                "schema_version": 2,
                "repository_identity": "a" * 64,
                "worktree_identity": "b" * 64,
                "routes": [],
            }
            before = tree_bytes(root)
            calls = []

            def reject(command, *args, **kwargs):
                calls.append(list(command))
                return subprocess.CompletedProcess(
                    command,
                    128,
                    "",
                    "fatal: detected dubious ownership; safe.directory required",
                )

            with mock.patch.object(
                io_module.subprocess,
                "run",
                side_effect=reject,
            ):
                result = self.prepare(root, local_map=local_map)

            self.assertEqual(result["status"], "requires_user_action")
            self.assertEqual(result["reason"], "local-map-git-protection-unavailable")
            self.assertFalse(result["git_ignore_protected"])
            self.assertFalse(any("-c" in command for command in calls))
            self.assertEqual(tree_bytes(root), before)


    def test_unavailable_git_proof_fails_closed_without_writing_local_map(self):
        io_module = self.module("lifecycle_io")
        verify = self.api("verify_local_route_hashes")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            create_repository(root)
            route = local_route(".local/private-plan.md")
            path = root / route["route"]
            path.parent.mkdir()
            path.write_text("# Private plan\n", encoding="utf-8")
            verified = verify(
                root,
                [route],
                selected_scope=".local",
                byte_limit=64 * 1024,
            )
            local_map = {
                "schema_version": 2,
                "repository_identity": "a" * 64,
                "worktree_identity": "b" * 64,
                "routes": verified["routes"],
            }
            before = tree_bytes(root)
            with mock.patch.object(
                io_module.subprocess,
                "run",
                side_effect=FileNotFoundError("git unavailable"),
            ):
                result = self.prepare(root, local_map=local_map)
            self.assertEqual(result["status"], "requires_user_action")
            self.assertEqual(result["reason"], "local-map-git-protection-unavailable")
            self.assertFalse(result["git_ignore_protected"])
            self.assertEqual(tree_bytes(root), before)

    def test_git_proof_accepts_windows_short_root_alias_end_to_end(self):
        if os.name != "nt":
            self.skipTest("Windows short-path aliases are not available")
        io_module = self.module("lifecycle_io")
        with tempfile.TemporaryDirectory() as td:
            long_root = Path(td) / "long-directory-for-git-proof"
            long_root.mkdir()
            try:
                subprocess.run(
                    ["git", "-C", str(long_root), "init", "-q"],
                    check=True,
                    capture_output=True,
                )
            except (OSError, subprocess.CalledProcessError):
                self.skipTest("Git unavailable")
            buffer = ctypes.create_unicode_buffer(32768)
            length = ctypes.windll.kernel32.GetShortPathNameW(
                str(long_root), buffer, len(buffer)
            )
            short_root = buffer.value if length else ""
            if not short_root or os.path.normcase(short_root) == os.path.normcase(
                str(long_root)
            ):
                self.skipTest("Windows short-path alias unavailable")
            root = Path(short_root)

            self.assertEqual(io_module._git_ignore_status(root), "not-ignored")
            (long_root / ".gitignore").write_text(
                ".diataxis/local-map.json\n",
                encoding="utf-8",
            )
            self.assertEqual(io_module._git_ignore_status(root), "ignored")
            local_map = long_root / ".diataxis" / "local-map.json"
            local_map.parent.mkdir()
            local_map.write_text("{}", encoding="utf-8")
            subprocess.run(
                ["git", "add", "-f", str(local_map)],
                cwd=long_root,
                check=True,
                capture_output=True,
            )
            self.assertEqual(io_module._git_ignore_status(root), "not-ignored")
            subprocess.run(
                ["git", "reset", "-q", "--", ".diataxis/local-map.json"],
                cwd=long_root,
                check=True,
                capture_output=True,
            )
            local_map.unlink()
            plan = self.prepare_comprehensive(root)
            self.assertEqual(plan["status"], "approval-required")

    def test_tracked_local_map_cannot_be_reclassified_as_safe_by_gitignore(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            create_repository(root)
            try:
                subprocess.run(
                    ["git", "init", "-q"],
                    cwd=root,
                    check=True,
                    capture_output=True,
                )
            except (OSError, subprocess.CalledProcessError):
                self.skipTest("Git unavailable")
            local_map = {
                "schema_version": 2,
                "repository_identity": "a" * 64,
                "worktree_identity": "b" * 64,
                "routes": [],
            }
            installed = root / ".diataxis" / "local-map.json"
            installed.write_text(json.dumps(local_map) + "\n", encoding="utf-8")
            subprocess.run(
                ["git", "add", "-f", ".diataxis/local-map.json"],
                cwd=root,
                check=True,
                capture_output=True,
            )
            (root / ".gitignore").write_text(
                ".diataxis/local-map.json\n",
                encoding="utf-8",
            )
            before = tree_bytes(root)
            result = self.prepare(root, local_map=local_map)
            self.assertEqual(result.get("status"), "requires_user_action")
            self.assertEqual(result.get("reason"), "local-map-path-not-ignored")
            self.assertFalse(result["git_ignore_protected"])
            self.assertEqual(tree_bytes(root), before)

    def test_changed_protected_path_requires_nonempty_bound_evidence(self):
        classification = docs_checker.classify_protected_surfaces(
            ["docs/README.md"],
            host="github",
        )
        preview = docs_checker.preview_protected_dispositions(
            classification,
            [
                {
                    "path": "docs/README.md",
                    "action": "replace",
                    "disposition": "MIGRATED",
                }
            ],
            exact_authorizations=["docs/README.md"],
        )
        forged = copy.deepcopy(preview)
        forged["protected_evidence"] = []
        self.assertFalse(docs_checker.validate_protected_disposition_preview(forged))
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            create_repository(root)
            before = tree_bytes(root)
            with self.assertRaisesRegex(ValueError, "protected surface preview"):
                self.prepare(root, protected_preview=forged)
            self.assertEqual(tree_bytes(root), before)

    def test_protected_replacement_cannot_omit_authorization_and_evidence_together(self):
        classification = docs_checker.classify_protected_surfaces(
            ["docs/README.md"],
            host="github",
        )
        forged = docs_checker.preview_protected_dispositions(
            classification,
            [
                {
                    "path": "docs/README.md",
                    "action": "replace",
                    "disposition": "MIGRATED",
                }
            ],
            exact_authorizations=["docs/README.md"],
        )
        forged["exact_authorizations"] = []
        forged["protected_evidence"] = []
        self.assertFalse(docs_checker.validate_protected_disposition_preview(forged))
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            create_repository(root)
            before = tree_bytes(root)
            with self.assertRaisesRegex(ValueError, "protected surface preview"):
                self.prepare(root, protected_preview=forged)
            self.assertEqual(tree_bytes(root), before)

    def test_protected_surface_verification_failure_rolls_back_document_and_memory(self):
        apply = self.api("apply_verified_closeout")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            control = create_repository(root)
            readme = root / "docs" / "README.md"
            original = readme.read_bytes()
            readme.write_text("# Empty front door\n", encoding="utf-8")
            before_control = file_snapshot(control)
            classification = docs_checker.classify_protected_surfaces(["docs/README.md"], host="github")
            blocked = docs_checker.preview_protected_dispositions(
                classification,
                [{"path": "docs/README.md", "action": "replace", "disposition": "MIGRATED"}],
            )
            blocked_plan = self.prepare(root, command="update", protected_preview=blocked)
            self.assertEqual(blocked_plan["status"], "requires_user_action")
            self.assertEqual(blocked_plan["reason"], "protected-surface-authorization-required")

            preview = docs_checker.preview_protected_dispositions(
                classification,
                [{"path": "docs/README.md", "action": "replace", "disposition": "MIGRATED"}],
                exact_authorizations=["docs/README.md"],
            )
            self.assertEqual(preview["effects"][0]["path"], "docs/README.md")
            self.assertEqual(preview["exact_authorizations"], ["docs/README.md"])
            self.assertEqual(
                preview["protected_evidence"][0]["protection_reason"],
                "platform-recognized",
            )
            plan = self.prepare(root, protected_preview=preview)
            other_classification = docs_checker.classify_protected_surfaces(
                ["SECURITY.md"], host="github"
            )
            other_preview = docs_checker.preview_protected_dispositions(
                other_classification,
                [{"path": "SECURITY.md", "action": "replace", "disposition": "MIGRATED"}],
                exact_authorizations=["SECURITY.md"],
            )
            with self.assertRaisesRegex(ValueError, "approved transaction"):
                apply(
                    root,
                    plan,
                    approved_transaction=plan["transaction_id"],
                    verification=lambda: True,
                    protected_preview=other_preview,
                    protected_verification=lambda: True,
                )
            self.assertEqual(file_snapshot(control), before_control)
            result = apply(
                root,
                plan,
                approved_transaction=plan["transaction_id"],
                verification=lambda: True,
                protected_preview=preview,
                protected_verification=lambda: False,
                documentation_rollback=lambda: readme.write_bytes(original),
            )
            self.assertEqual(result["status"], "protected-verification-failed")
            self.assertTrue(result["documentation_rolled_back"])
            self.assertEqual(readme.read_bytes(), original)
            self.assertEqual(file_snapshot(control), before_control)


class Task7RestartIntegrityTests(Task7ContractCase):
    def test_nested_temporaries_and_unreferenced_control_artifacts_are_conflicts(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            control = create_repository(root)
            manifests = control / "manifests"
            manifests.mkdir(exist_ok=True)
            nested_temp = manifests / f"{TRANSACTION_PREFIX}ABCDEF0123456789-manifest.tmp"
            nested_temp.write_bytes(b"private unfinished bytes")
            orphan_manifest = manifests / "EVT-DEADBEEF.json"
            orphan_manifest.write_text("{}\n", encoding="utf-8")
            local_map = control / "local-map.json"
            local_map.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "repository_identity": "a" * 64,
                        "worktree_identity": "b" * 64,
                        "routes": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            before = tree_bytes(root)
            findings = docs_checker.inspect_operational_memory(root)
            details = [
                item["detail"]
                for item in findings
                if item["kind"] == "state-conflict" and item["priority"] == "P0"
            ]
            self.assertTrue(any("temporary" in detail for detail in details))
            self.assertTrue(any("unreferenced manifest" in detail for detail in details))
            self.assertTrue(any("unreferenced local map" in detail for detail in details))
            self.assertNotIn(str(root), json.dumps(findings, sort_keys=True))
            self.assertEqual(tree_bytes(root), before)

    def test_swapped_external_manifest_under_same_event_id_is_state_conflict(self):
        apply = self.api("apply_verified_closeout")
        items = [
            disposition(f"docs/legacy-{index:04d}.md#<whole-file>")
            for index in range(360)
        ]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            control = create_repository(root)
            plan = self.prepare(
                root,
                command="cleanup",
                dispositions=items,
                removed_items=[item["item_id"] for item in items],
            )
            result = apply(
                root,
                plan,
                approved_transaction=plan["transaction_id"],
                verification=lambda: True,
            )
            self.assertEqual(result["status"], "applied")
            events = docs_checker.load_operational_events(root)
            latest = events[-1]
            manifest = root / latest["manifest"]["path"]
            swapped = json.loads(manifest.read_text(encoding="utf-8"))
            swapped["dispositions"][0]["reason"] = "coordinated different payload"
            payload = (json.dumps(swapped, sort_keys=True, separators=(",", ":")) + "\n").encode()
            manifest.write_bytes(payload)
            stored = [json.loads(line) for line in (control / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            stored[-1]["manifest"]["digest"] = "sha256:" + hashlib.sha256(payload).hexdigest()
            stored[-1]["manifest_digest"] = stored[-1]["manifest"]["digest"]
            (control / "events.jsonl").write_text(
                "".join(json.dumps(event, sort_keys=True) + "\n" for event in stored),
                encoding="utf-8",
            )
            conflicts = docs_checker.inspect_operational_memory(root)
            self.assertTrue(
                any(
                    item["kind"] == "state-conflict"
                    and item["priority"] == "P0"
                    and "event" in item["detail"]
                    for item in conflicts
                )
            )

    def test_canonical_references_define_one_transaction_and_no_hidden_write_contract(self):
        memory = (SKILL / "references" / "memory.md").read_text(encoding="utf-8").casefold()
        commands = (SKILL / "references" / "commands.md").read_text(encoding="utf-8").casefold()
        doctor = (SKILL / "references" / "doctor.md").read_text(encoding="utf-8").casefold()
        combined = "\n".join((memory, commands, doctor))
        for phrase in (
            "compare-before-write",
            "success event last",
            "reserved transaction temporary",
            "orphan",
            "cross-device",
            "sharing violation",
            "local-map.json",
            "mechanically verified as ignored",
            "protected-intent-missing",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, combined)
        for command in READ_ONLY_COMMANDS:
            self.assertRegex(combined, rf"{command}[^\n]{{0,180}}(?:write neither|zero writes|read-only)")


if __name__ == "__main__":
    unittest.main()
