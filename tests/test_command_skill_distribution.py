import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from unittest import mock


ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / "skills" / "docs" / "scripts"
sys.path.insert(0, str(SCRIPTS))
try:
    import check as docs_checker
    from _docs_checker import doctor_baseline as doctor_baseline_module
finally:
    sys.path.pop(0)

COMMANDS = (
    "doctor",
    "init",
    "context",
    "write",
    "update",
    "audit",
    "fix",
    "map",
    "classify",
    "migrate",
    "check",
    "cleanup",
    "help",
)


def frontmatter(text):
    parts = text.split("---", 2)
    if len(parts) != 3:
        raise AssertionError("skill must contain frontmatter")
    values = {}
    for line in parts[1].strip().splitlines():
        if ":" not in line or line.startswith("  "):
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip().strip('"')
    return values, parts[2]


class CommandSkillDistributionTests(unittest.TestCase):
    def _run_doctor_baseline_raw(self, files, *extra):
        with tempfile.TemporaryDirectory() as td:
            repository = Path(td) / "repo"
            repository.mkdir()
            for relative, content in files.items():
                path = repository / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            subprocess.run(["git", "init", "-q", str(repository)], check=True)
            subprocess.run(
                ["git", "-C", str(repository), "add", "--all"], check=True
            )
            before = subprocess.run(
                ["git", "-C", str(repository), "status", "--porcelain=v1"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(ROOT / "skills" / "docs" / "scripts" / "check.py"),
                    str(repository),
                    "--json",
                    "--agent",
                    "--doctor-baseline",
                    *extra,
                ],
                capture_output=True,
                text=True,
            )
            after = subprocess.run(
                ["git", "-C", str(repository), "status", "--porcelain=v1"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            return result, before, after

    def _run_doctor_baseline(self, files, *extra):
        result, before, after = self._run_doctor_baseline_raw(files, *extra)
        return result, json.loads(result.stdout), before, after

    def test_codex_marketplace_routes_to_the_named_plugin_package(self):
        marketplace_path = ROOT / ".agents" / "plugins" / "marketplace.json"
        self.assertTrue(marketplace_path.is_file())
        marketplace = json.loads(marketplace_path.read_text(encoding="utf-8"))

        self.assertEqual(marketplace["name"], "statusnone-skills")
        self.assertEqual(marketplace["interface"]["displayName"], "Statusnone Skills")
        self.assertEqual(len(marketplace["plugins"]), 1)
        entry = marketplace["plugins"][0]
        self.assertEqual(entry["name"], "diataxis-docs")
        self.assertEqual(
            entry["source"],
            {"source": "local", "path": "./plugins/diataxis-docs"},
        )
        self.assertEqual(
            entry["policy"],
            {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
        )
        self.assertEqual(entry["category"], "Developer Tools")

        source = PurePosixPath(entry["source"]["path"])
        self.assertNotIn("..", source.parts)
        plugin_root = ROOT / source
        self.assertTrue(plugin_root.is_dir())
        manifest = json.loads(
            (plugin_root / ".codex-plugin" / "plugin.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(manifest["name"], entry["name"])
        self.assertEqual(plugin_root.name, entry["name"])
        self.assertEqual(manifest["version"], "0.1.5")
        self.assertEqual(manifest["interface"]["displayName"], "Diátaxis Docs")

    def test_codex_and_claude_publish_the_umbrella_plus_focused_skills(self):
        roots = {
            "codex": ROOT / "plugins" / "diataxis-docs" / "skills",
            "claude": ROOT / "adapters" / "claude" / "skills",
        }
        expected = {"docs", *(f"docs-{command}" for command in COMMANDS)}
        for vendor, root in roots.items():
            with self.subTest(vendor=vendor):
                self.assertEqual(
                    {path.name for path in root.iterdir() if path.is_dir()}, expected
                )
                for skill_name in expected:
                    self.assertTrue((root / skill_name / "SKILL.md").is_file())

    def test_focused_skills_are_explicit_thin_routes_to_the_shared_engine(self):
        import tools.build_adapters as builder

        self.assertEqual(set(builder.COMMAND_SPECS), set(COMMANDS))
        for command in COMMANDS:
            codex_root = ROOT / "plugins" / "diataxis-docs" / "skills" / f"docs-{command}"
            claude_root = ROOT / "adapters" / "claude" / "skills" / f"docs-{command}"
            codex_text = (codex_root / "SKILL.md").read_text(encoding="utf-8")
            claude_text = (claude_root / "SKILL.md").read_text(encoding="utf-8")
            codex_meta, codex_body = frontmatter(codex_text)
            claude_meta, claude_body = frontmatter(claude_text)

            with self.subTest(command=command, vendor="codex"):
                self.assertEqual(codex_meta["name"], f"docs-{command}")
                self.assertNotIn("user-invocable", codex_meta)
                self.assertNotIn("disable-model-invocation", codex_meta)
                self.assertEqual(codex_text, builder.command_skill(command, "codex"))
                self.assertIn(f"fixed command `{command}`", codex_body)
                self.assertIn("../docs/SKILL.md", codex_body)
                self.assertNotIn("Generic web mode", codex_body)
                self.assertNotIn("{{REPOSITORY_MATERIAL}}", codex_body)
                self.assertLess(len(codex_body.split()), 180)
                agent = (codex_root / "agents" / "openai.yaml").read_text(
                    encoding="utf-8"
                )
                self.assertIn("allow_implicit_invocation: false", agent)
                self.assertIn(f"$docs-{command}", agent)

            with self.subTest(command=command, vendor="claude"):
                self.assertEqual(claude_meta["name"], f"docs-{command}")
                self.assertEqual(claude_meta["user-invocable"], "true")
                self.assertEqual(claude_meta["disable-model-invocation"], "true")
                self.assertEqual(claude_text, builder.command_skill(command, "claude"))
                self.assertEqual(claude_body, codex_body)

    def test_help_contract_guarantees_the_command_tree(self):
        commands = (ROOT / "skills" / "docs" / "references" / "commands.md").read_text(
            encoding="utf-8"
        )
        expected_tree = """Diátaxis Docs
├── doctor
├── init
├── context
├── write
├── update
├── audit
├── fix
├── map
├── classify
├── migrate
├── check
├── cleanup
└── help"""
        self.assertIn(expected_tree, commands)
        help_contract = re.search(
            r"`help \[all\]`:(.*?)(?:\n`[a-z]|\Z)", commands, re.DOTALL
        )
        self.assertIsNotNone(help_contract)
        self.assertIn("always render", help_contract.group(1).lower())
        self.assertIn("no repo I/O", help_contract.group(1))

    def test_doctor_can_measure_a_safe_no_map_orientation_fallback(self):
        doctor = (ROOT / "skills" / "docs" / "references" / "doctor.md").read_text(
            encoding="utf-8"
        )
        commands = (ROOT / "skills" / "docs" / "references" / "commands.md").read_text(
            encoding="utf-8"
        )
        combined = " ".join((doctor + commands).split()).lower()
        for phrase in (
            "orientation fallback",
            "not a maintained documentation map",
            "structural baseline",
            "recommend `$docs init`",
            "zero writes",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, combined)
        self.assertIn("--doctor-baseline", doctor)
        self.assertIn("unsupported provider", combined)
        self.assertIn("remain unmeasured", combined)

    def test_engine_measures_safe_tracked_root_readme_fallback_without_writes(self):
        result, payload, before, after = self._run_doctor_baseline(
            {
                "README.md": "# Project\n\nRepository overview.\n",
                "docs/guide.md": "# Guide\n\nUseful guidance.\n",
            }
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(before, after)
        self.assertEqual(payload["doctor_baseline"]["status"], "measured")
        self.assertEqual(payload["doctor_baseline"]["maintained_map"], False)
        self.assertEqual(payload["doctor_baseline"]["writes"], 0)
        self.assertFalse(payload["doctor_baseline"]["treatment_authority"])
        self.assertEqual(payload["doctor_baseline"]["recommendation"], "$docs init")
        self.assertEqual(payload["map"], "README.md")
        self.assertEqual(payload["scope"], "docs")
        self.assertEqual(payload["health"]["rubric_version"], 2)

    def test_engine_rejects_unsupported_provider_without_score_or_init(self):
        result, payload, before, after = self._run_doctor_baseline(
            {
                "docs/guide.md": "# Guide\n",
                "docs/docs.json": '{"navigation":{"tabs":[]}}\n',
            }
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(before, after)
        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(payload["doctor_baseline"]["reason"], "navigation-unavailable")
        self.assertEqual(
            payload["doctor_baseline"]["label"], "Doctor baseline unavailable"
        )
        self.assertNotIn("orientation fallback", payload["doctor_baseline"]["label"].lower())
        self.assertIsNone(payload["doctor_baseline"]["recommendation"])
        self.assertNotIn("health", payload)
        self.assertEqual(payload["navigation"]["status"], "unmeasured")

    def test_engine_returns_supported_provider_measurement_without_init(self):
        result, payload, before, after = self._run_doctor_baseline(
            {
                "docs/guide.md": "# Guide\n",
                "docs/docs.json": json.dumps(
                    {
                        "$schema": "https://mintlify.com/docs.json",
                        "navigation": {
                            "tabs": [
                                {
                                    "tab": "Docs",
                                    "groups": [
                                        {"group": "Guides", "pages": ["guide"]}
                                    ],
                                }
                            ]
                        }
                    }
                ),
            }
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(before, after)
        self.assertEqual(payload["doctor_baseline"]["status"], "measured")
        self.assertEqual(payload["doctor_baseline"]["reason"], "supported-provider")
        self.assertEqual(payload["doctor_baseline"]["authority_kind"], "provider")
        self.assertTrue(payload["doctor_baseline"]["treatment_authority"])
        self.assertIsNone(payload["doctor_baseline"]["recommendation"])
        self.assertEqual(payload["navigation"]["provider"], "mintlify")
        self.assertEqual(payload["navigation"]["status"], "measured")
        self.assertEqual(payload["health"]["rubric_version"], 2)

    def test_engine_rejects_explicit_scope_for_root_fallback(self):
        result, payload, before, after = self._run_doctor_baseline(
            {"README.md": "# Project\n", "docs/guide.md": "# Guide\n"},
            "--scope",
            "docs",
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(before, after)
        self.assertEqual(
            payload["error"],
            "--doctor-baseline does not accept --scope, --map, --hot, or --continuation",
        )

    def test_engine_rejects_equals_form_baseline_overrides(self):
        for option in (
            "--scope=docs",
            "--map=README.md",
            "--hot=docs/guide.md",
            "--continuation=opaque",
        ):
            with self.subTest(option=option):
                result, payload, before, after = self._run_doctor_baseline(
                    {"README.md": "# Project\n", "docs/guide.md": "# Guide\n"},
                    option,
                )
                self.assertEqual(result.returncode, 2)
                self.assertEqual(before, after)
                self.assertEqual(
                    payload["error"],
                    "--doctor-baseline does not accept --scope, --map, --hot, or --continuation",
                )

    def test_engine_rejects_abbreviated_baseline_overrides(self):
        for option in (
            ("--sc", "docs"),
            ("--ma=README.md",),
            ("--ho", "docs/guide.md"),
            ("--cont=opaque",),
        ):
            with self.subTest(option=option):
                result, before, after = self._run_doctor_baseline_raw(
                    {"README.md": "# Project\n", "docs/guide.md": "# Guide\n"},
                    *option,
                )
                self.assertEqual(result.returncode, 2)
                self.assertEqual(before, after)
                self.assertEqual(result.stdout, "")
                self.assertIn("unrecognized arguments:", result.stderr)
                self.assertIn(option[0], result.stderr)

    def test_engine_accepts_content_batch_limited_root_fallback(self):
        files = {"README.md": "# Project\n"}
        files.update({f"docs/page-{index:02}.md": f"# Page {index}\n" for index in range(13)})
        result, payload, before, after = self._run_doctor_baseline(files)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(before, after)
        self.assertEqual(payload["discovery"]["status"], "batch-limited")
        self.assertEqual(payload["doctor_baseline"]["authority_kind"], "orientation-fallback")
        self.assertIn("health", payload)

    def test_engine_accepts_content_batch_limited_entry_candidate(self):
        files = {
            "README.md": "# Project\n",
            "docs/README.md": "# Documentation map\n",
        }
        files.update({f"docs/page-{index:02}.md": f"# Page {index}\n" for index in range(12)})
        result, payload, before, after = self._run_doctor_baseline(files)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(before, after)
        self.assertEqual(payload["discovery"]["status"], "batch-limited")
        self.assertEqual(
            payload["doctor_baseline"]["authority_kind"], "existing-entry-candidate"
        )
        self.assertIn("health", payload)

    def test_engine_accepts_content_batch_limited_supported_provider(self):
        files = {
            "docs/docs.json": json.dumps(
                {
                    "$schema": "https://mintlify.com/docs.json",
                    "navigation": {
                        "tabs": [
                            {
                                "tab": "Docs",
                                "groups": [
                                    {"group": "Guides", "pages": ["page-00"]}
                                ],
                            }
                        ]
                    },
                }
            )
        }
        files.update({f"docs/page-{index:02}.md": f"# Page {index}\n" for index in range(13)})
        result, payload, before, after = self._run_doctor_baseline(files)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(before, after)
        self.assertEqual(payload["discovery"]["status"], "batch-limited")
        self.assertEqual(payload["doctor_baseline"]["authority_kind"], "provider")
        self.assertIn("health", payload)

    def test_engine_rejects_true_metadata_truncation(self):
        files = {"README.md": "# Project\n"}
        files.update({f"docs/page-{index:03}.md": f"# Page {index}\n" for index in range(257)})
        result, payload, before, after = self._run_doctor_baseline(files)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(before, after)
        self.assertEqual(payload["doctor_baseline"]["reason"], "discovery-not-ready")
        self.assertNotIn("health", payload)

    def test_engine_measures_existing_entry_as_provisional_candidate(self):
        result, payload, before, after = self._run_doctor_baseline(
            {
                "README.md": "# Project\n",
                "docs/README.md": "# Documentation map\n",
                "docs/guide.md": "# Guide\n",
            }
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(before, after)
        self.assertEqual(payload["doctor_baseline"]["reason"], "existing-entry-candidate")
        self.assertEqual(payload["doctor_baseline"]["authority_kind"], "existing-entry-candidate")
        self.assertIsNone(payload["doctor_baseline"]["maintained_map"])
        self.assertFalse(payload["doctor_baseline"]["treatment_authority"])
        self.assertEqual(payload["doctor_baseline"]["recommendation"], "$docs map")
        self.assertEqual(payload["map"], "docs/README.md")
        self.assertIn("health", payload)

    def test_engine_does_not_promote_ordinary_index_filename_to_map_fact(self):
        result, payload, before, after = self._run_doctor_baseline(
            {
                "README.md": "# Project\n",
                "docs/index.md": "# Internal release index\n\nOne leaf article.\n",
                "docs/guide.md": "# Guide\n",
            }
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(before, after)
        self.assertEqual(payload["doctor_baseline"]["reason"], "existing-entry-candidate")
        self.assertEqual(payload["doctor_baseline"]["authority_kind"], "existing-entry-candidate")
        self.assertIsNone(payload["doctor_baseline"]["maintained_map"])
        self.assertFalse(payload["doctor_baseline"]["treatment_authority"])
        self.assertEqual(payload["map"], "docs/index.md")
        self.assertEqual(payload["doctor_baseline"]["recommendation"], "$docs map")
        self.assertIn("health", payload)

    def test_engine_preserves_the_discovered_root_readme_case(self):
        result, payload, before, after = self._run_doctor_baseline(
            {
                "Readme.md": "# Project\n",
                "docs/guide.md": "# Guide\n",
            }
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(before, after)
        self.assertEqual(payload["doctor_baseline"]["authority_kind"], "orientation-fallback")
        self.assertEqual(payload["map"], "Readme.md")
        self.assertEqual(payload["navigation"]["authority"], "Readme.md")

    def test_engine_binds_measurement_to_one_navigation_snapshot(self):
        provider = json.dumps(
            {
                "$schema": "https://mintlify.com/docs.json",
                "navigation": {
                    "tabs": [
                        {
                            "tab": "Docs",
                            "groups": [{"group": "Guides", "pages": ["guide"]}],
                        }
                    ]
                },
            }
        )
        cases = (
            (
                "provider-appears",
                {"README.md": "# Project\n", "docs/guide.md": "# Guide\n"},
                lambda root: (root / "docs" / "docs.json").write_text(
                    provider, encoding="utf-8"
                ),
                "orientation-fallback",
            ),
            (
                "provider-disappears",
                {"docs/guide.md": "# Guide\n", "docs/docs.json": provider},
                lambda root: (root / "docs" / "docs.json").unlink(),
                "provider",
            ),
            (
                "provider-changes",
                {"docs/guide.md": "# Guide\n", "docs/docs.json": provider},
                lambda root: (root / "docs" / "docs.json").write_text(
                    '{"navigation":{"tabs":[]}}', encoding="utf-8"
                ),
                "provider",
            ),
        )
        for name, files, mutate, expected_authority in cases:
            with self.subTest(case=name), tempfile.TemporaryDirectory() as td:
                repository = Path(td) / "repo"
                repository.mkdir()
                for relative, content in files.items():
                    path = repository / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(content, encoding="utf-8")
                subprocess.run(["git", "init", "-q", str(repository)], check=True)
                subprocess.run(["git", "-C", str(repository), "add", "--all"], check=True)

                def measure_after_mutation(*args, **kwargs):
                    mutate(repository)
                    with mock.patch.object(
                        docs_checker,
                        "select_navigation",
                        side_effect=AssertionError("navigation was selected twice"),
                    ):
                        return docs_checker.check(*args, **kwargs)

                with mock.patch.object(
                    doctor_baseline_module,
                    "select_navigation",
                    wraps=doctor_baseline_module.select_navigation,
                ) as selector:
                    payload = doctor_baseline_module.doctor_orientation_baseline(
                        repository, measure_after_mutation
                    )
                self.assertEqual(selector.call_count, 1)
                self.assertEqual(
                    payload["doctor_baseline"]["authority_kind"], expected_authority
                )
                self.assertEqual(
                    payload["navigation"]["provider"] == "mintlify",
                    expected_authority == "provider",
                )

    def test_engine_normalizes_tracked_reparse_discovery_failure(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repository = base / "repo"
            repository.mkdir()
            (repository / "README.md").write_text("# Project\n", encoding="utf-8")
            (repository / "docs").mkdir()
            external = base / "external.md"
            external.write_text("# External\n", encoding="utf-8")
            try:
                (repository / "docs" / "linked.md").symlink_to(external)
            except (OSError, NotImplementedError):
                self.skipTest("file symlinks unavailable")
            subprocess.run(["git", "init", "-q", str(repository)], check=True)
            subprocess.run(["git", "-C", str(repository), "add", "--all"], check=True)
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(SCRIPTS / "check.py"),
                    str(repository),
                    "--json",
                    "--agent",
                    "--doctor-baseline",
                ],
                capture_output=True,
                text=True,
            )
            payload = json.loads(result.stdout)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(payload["status"], "unavailable")
            self.assertEqual(payload["doctor_baseline"]["reason"], "discovery-unavailable")
            self.assertIsNone(payload["doctor_baseline"]["recommendation"])
            self.assertNotIn("health", payload)

    def test_engine_normalizes_git_inventory_failure(self):
        with tempfile.TemporaryDirectory() as td:
            repository = Path(td) / "repo"
            repository.mkdir()
            (repository / "README.md").write_text("# Project\n", encoding="utf-8")
            (repository / "docs").mkdir()
            (repository / "docs" / "guide.md").write_text("# Guide\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q", str(repository)], check=True)
            subprocess.run(["git", "-C", str(repository), "add", "--all"], check=True)

            with mock.patch.object(
                doctor_baseline_module,
                "tracked_markdown_scope",
                side_effect=OSError("Git unavailable"),
            ):
                payload = doctor_baseline_module.doctor_orientation_baseline(
                    repository, docs_checker.check
                )
            self.assertEqual(payload["status"], "unavailable")
            self.assertEqual(
                payload["doctor_baseline"]["reason"], "git-tracking-unavailable"
            )
            self.assertIsNone(payload["doctor_baseline"]["recommendation"])
            self.assertNotIn("health", payload)


if __name__ == "__main__":
    unittest.main()
