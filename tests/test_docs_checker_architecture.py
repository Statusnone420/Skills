import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / "skills" / "docs" / "scripts"
CHECKER = SCRIPTS / "check.py"
PACKAGE = SCRIPTS / "_docs_checker"
MODULES = (
    "formats",
    "paths",
    "metadata_io",
    "continuation",
    "knowledge",
    "root_evidence",
    "discovery_policy",
    "surfaces",
    "receipt",
    "evidence",
    "discovery_io",
    "discovery",
    "scan",
    "identity",
    "memory",
    "health",
    "navigation",
)


class DocsCheckerArchitectureTests(unittest.TestCase):
    def test_direct_script_preserves_json_text_and_missing_root_contracts(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            docs = root / "docs"
            docs.mkdir()
            (docs / "README.md").write_text("# Home\n", encoding="utf-8")

            json_run = subprocess.run(
                [sys.executable, str(CHECKER), str(root), "--json"],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(json_run.returncode, 0, json_run.stderr)
            payload = json.loads(json_run.stdout)
            self.assertEqual(
                set(payload),
                {
                    "status",
                    "has_findings",
                    "root",
                    "scope",
                    "map",
                    "prunes",
                    "hot_path",
                    "navigation",
                    "health",
                    "findings",
                },
            )
            self.assertEqual(payload["status"], "clean")
            self.assertFalse(payload["has_findings"])
            self.assertEqual(payload["findings"], [])
            self.assertRegex(payload["health"]["meter"], r"^Docs \[[█░]{20}\] \d+%$")

            text_run = subprocess.run(
                [sys.executable, str(CHECKER), str(root)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(text_run.returncode, 0, text_run.stderr)
            self.assertEqual(text_run.stdout, "clean\n")

            missing = subprocess.run(
                [sys.executable, str(CHECKER), "--json"],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(missing.returncode, 2)
            self.assertEqual(
                set(json.loads(missing.stdout)),
                {"status", "has_findings", "error", "findings"},
            )

    def test_internal_package_has_required_cohesive_modules(self):
        expected = {"__init__.py", *(f"{name}.py" for name in MODULES)}
        self.assertLessEqual(
            expected,
            {path.name for path in PACKAGE.glob("*.py")},
        )

    def test_facade_defines_only_check_and_main(self):
        tree = ast.parse(CHECKER.read_text(encoding="utf-8"), filename=str(CHECKER))
        definitions = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        classes = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
        self.assertEqual(definitions, {"check", "main"})
        self.assertEqual(classes, set())

    def test_facade_preserves_compatibility_helper_reexports(self):
        self.assertTrue((PACKAGE / "discovery.py").is_file())
        sys.path.insert(0, str(SCRIPTS))
        try:
            import check
            from _docs_checker import (
                discovery,
                health,
                identity,
                knowledge,
                memory,
                metadata_io,
                paths,
                scan,
                surfaces,
            )
        finally:
            sys.path.pop(0)

        owners = {
            paths: (
                "safe_path",
                "normalize_repo_relative",
                "iter_markdown_scope",
                "prune_summary",
                "route_matches_patterns",
                "unique_relative_paths",
            ),
            discovery: ("discover_init_scope",),
            metadata_io: ("is_expected_environmental_error",),
            knowledge: ("inspect_local_map", "route_local_knowledge"),
            surfaces: (
                "classify_protected_surfaces",
                "inspect_protected_surfaces",
                "preview_protected_dispositions",
                "validate_protected_disposition_preview",
            ),
            scan: ("strip_fences", "hot_path_summary"),
            identity: (
                "slug",
                "event_fingerprint",
                "event_id",
                "finding_fingerprint",
                "finding_id",
            ),
            memory: (
                "validate_operational_state",
                "load_operational_state",
                "load_operational_findings",
                "validate_operational_findings",
                "validate_operational_events",
                "load_operational_events",
            ),
            health: (
                "evaluate_coverage",
                "evaluate_freshness",
                "health_meter",
                "health_summary",
                "normalized_content_digest",
            ),
        }
        for owner, names in owners.items():
            for name in names:
                with self.subTest(name=name):
                    self.assertIs(getattr(check, name), getattr(owner, name))

    def test_identity_preserves_strict_nonfinite_value_rejection(self):
        sys.path.insert(0, str(SCRIPTS))
        try:
            import check
        finally:
            sys.path.pop(0)

        with self.assertRaisesRegex(
            ValueError, "canonical finding evidence is malformed JSON"
        ):
            check.finding_fingerprint("identity", [{"field": float("nan")}])

    def test_internal_dependencies_are_acyclic_and_never_import_facade(self):
        self.assertTrue((PACKAGE / "discovery.py").is_file())
        graph = {name: set() for name in MODULES}
        for name in MODULES:
            tree = ast.parse(
                (PACKAGE / f"{name}.py").read_text(encoding="utf-8"),
                filename=f"{name}.py",
            )
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported = {alias.name.split(".", 1)[0] for alias in node.names}
                    self.assertNotIn("check", imported, name)
                elif isinstance(node, ast.ImportFrom):
                    imported_module = (node.module or "").split(".", 1)[0]
                    self.assertNotEqual(imported_module, "check", name)
                    if node.level and imported_module in graph:
                        graph[name].add(imported_module)

        self.assertEqual(graph["formats"], set())
        self.assertEqual(graph["paths"], {"formats"})
        self.assertEqual(graph["identity"], set())
        self.assertEqual(graph["metadata_io"], set())
        self.assertEqual(graph["continuation"], {"paths"})
        self.assertEqual(graph["knowledge"], {"formats", "paths"})
        self.assertEqual(graph["root_evidence"], {"formats", "paths"})
        self.assertEqual(graph["discovery_policy"], {"paths"})
        self.assertEqual(graph["surfaces"], {"formats", "knowledge", "paths"})
        self.assertEqual(
            graph["receipt"],
            {"continuation", "knowledge", "paths", "surfaces"},
        )
        self.assertEqual(graph["evidence"], {"formats", "paths"})
        self.assertEqual(
            graph["discovery_io"],
            {"discovery_policy", "formats", "metadata_io"},
        )
        self.assertEqual(
            graph["discovery"],
            {
                "continuation",
                "discovery_io",
                "discovery_policy",
                "formats",
                "knowledge",
                "paths",
                "receipt",
                "root_evidence",
                "surfaces",
            },
        )
        self.assertLessEqual(graph["health"], {"identity", "paths"})
        self.assertLessEqual(
            graph["scan"], {"formats", "paths", "identity", "health", "navigation"}
        )
        self.assertLessEqual(graph["memory"], {"formats", "paths", "identity"})
        self.assertEqual(graph["navigation"], {"formats", "paths"})
        visiting = set()
        visited = set()

        def visit(module):
            if module in visiting:
                self.fail(f"internal dependency cycle reaches {module}")
            if module in visited:
                return
            visiting.add(module)
            for dependency in graph[module]:
                visit(dependency)
            visiting.remove(module)
            visited.add(module)

        for module in graph:
            visit(module)

    def test_document_suffix_policy_has_one_owner(self):
        forbidden = (
            'endswith(".md")',
            "endswith('.md')",
            'suffix.lower() == ".md"',
            'suffix.lower() != ".md"',
            'suffix.casefold() == ".md"',
            'suffix.casefold() != ".md"',
        )
        for path in PACKAGE.glob("*.py"):
            if path.name == "formats.py":
                continue
            source = path.read_text(encoding="utf-8")
            for fragment in forbidden:
                with self.subTest(path=path.name, fragment=fragment):
                    self.assertNotIn(fragment, source)

    def test_physical_discovery_work_has_one_owner_and_receipt_uses_canonical_policy(self):
        discovery_tree = ast.parse(
            (PACKAGE / "discovery.py").read_text(encoding="utf-8")
        )
        io_tree = ast.parse(
            (PACKAGE / "discovery_io.py").read_text(encoding="utf-8")
        )
        physical_names = {
            "_lstat_path",
            "_entry_stat",
            "_list_entries",
            "_scan_selected_scope",
            "_take_metadata_operation",
        }
        discovery_functions = {
            node.name for node in discovery_tree.body if isinstance(node, ast.FunctionDef)
        }
        io_functions = {
            node.name for node in io_tree.body if isinstance(node, ast.FunctionDef)
        }
        self.assertTrue(physical_names.isdisjoint(discovery_functions))
        self.assertLessEqual(physical_names, io_functions)

        sys.path.insert(0, str(SCRIPTS))
        try:
            from _docs_checker import knowledge, receipt, surfaces
        finally:
            sys.path.pop(0)
        self.assertIs(
            receipt.validate_local_knowledge_receipt,
            knowledge.validate_local_knowledge_receipt,
        )
        self.assertIs(
            receipt.validate_protected_surfaces,
            surfaces.validate_protected_surfaces,
        )

    def test_internal_modules_are_stdlib_only_and_import_without_writes(self):
        for path in PACKAGE.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name.split(".", 1)[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    names = [(node.module or "").split(".", 1)[0]]
                else:
                    continue
                for name in names:
                    self.assertIn(name, sys.stdlib_module_names, f"{path.name}: {name}")

        with tempfile.TemporaryDirectory() as td:
            probe = (
                "from pathlib import Path\n"
                "before = list(Path('.').rglob('*'))\n"
                "import _docs_checker.paths, _docs_checker.metadata_io\n"
                "import _docs_checker.continuation, _docs_checker.knowledge\n"
                "import _docs_checker.root_evidence, _docs_checker.surfaces\n"
                "import _docs_checker.discovery_policy, _docs_checker.receipt\n"
                "import _docs_checker.discovery_io\n"
                "import _docs_checker.discovery, _docs_checker.scan\n"
                "import _docs_checker.identity, _docs_checker.memory, _docs_checker.health\n"
                "after = list(Path('.').rglob('*'))\n"
                "raise SystemExit(0 if before == after else 1)\n"
            )
            env = os.environ.copy()
            env["PYTHONPATH"] = str(SCRIPTS)
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            run = subprocess.run(
                [sys.executable, "-c", probe],
                cwd=td,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(run.returncode, 0, run.stderr)

        with tempfile.TemporaryDirectory() as td:
            temp_root = Path(td)
            tool = temp_root / "tool"
            repository = temp_root / "repository"
            tool.mkdir()
            (repository / "docs").mkdir(parents=True)
            shutil.copy2(CHECKER, tool / "check.py")
            shutil.copytree(
                PACKAGE,
                tool / "_docs_checker",
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
            (repository / "docs" / "README.md").write_text(
                "# Home\n", encoding="utf-8"
            )

            def snapshot(root):
                return {
                    path.relative_to(root).as_posix(): path.read_bytes()
                    for path in root.rglob("*")
                    if path.is_file()
                }

            before_tool = snapshot(tool)
            before_repository = snapshot(repository)
            run = subprocess.run(
                [sys.executable, str(tool / "check.py"), str(repository), "--json"],
                cwd=temp_root,
                capture_output=True,
                text=True,
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(snapshot(tool), before_tool)
            self.assertEqual(snapshot(repository), before_repository)

if __name__ == "__main__":
    unittest.main()
