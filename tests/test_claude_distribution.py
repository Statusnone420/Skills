import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).parents[1]
BUILDER = ROOT / "tools" / "build_adapters.py"


class ClaudeDistributionContractTests(unittest.TestCase):
    def test_marketplace_routes_to_generated_claude_adapter(self):
        marketplace_path = ROOT / ".claude-plugin" / "marketplace.json"
        self.assertTrue(marketplace_path.is_file())
        marketplace = json.loads(marketplace_path.read_text(encoding="utf-8"))

        self.assertEqual(marketplace["name"], "statusnone-skills")
        self.assertEqual(marketplace["owner"]["name"], "Statusnone")
        self.assertEqual(len(marketplace["plugins"]), 1)
        plugin = marketplace["plugins"][0]
        self.assertEqual(plugin["name"], "diataxis-docs")
        self.assertEqual(plugin["displayName"], "Diátaxis Docs")
        self.assertEqual(plugin["source"], "./adapters/claude")
        self.assertEqual(plugin["version"], "0.1.7")

        source = PurePosixPath(plugin["source"])
        self.assertEqual(source.parts[0], "adapters")
        self.assertNotIn("..", source.parts)
        self.assertTrue((ROOT / source).is_dir())
        self.assertFalse((ROOT / source / "SKILL.md").exists())
        self.assertTrue((ROOT / source / "skills" / "docs" / "SKILL.md").is_file())

    def test_generated_claude_plugin_uses_the_canonical_version(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as td:
            output = Path(td) / "out"
            subprocess.run(
                [sys.executable, str(BUILDER), "generate", "--output", str(output)],
                cwd=ROOT,
                check=True,
            )
            manifest_path = output / "claude" / ".claude-plugin" / "plugin.json"
            self.assertTrue(manifest_path.is_file())
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["name"], "diataxis-docs")
            self.assertEqual(manifest["description"], "Bounded repository memory. Evidence-backed documentation.")
            self.assertEqual(manifest["repository"], "https://github.com/Statusnone420/Skills")
            self.assertEqual(manifest["license"], "Apache-2.0")
            self.assertEqual(manifest["version"], "0.1.7")
            skill_root = output / "claude" / "skills" / "docs"
            self.assertFalse((output / "claude" / "SKILL.md").exists())
            generated_skill = (skill_root / "SKILL.md").read_text(encoding="utf-8")
            canonical_skill = (ROOT / "skills" / "docs" / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn("user-invocable: true", generated_skill)
            self.assertIn("disable-model-invocation: true", generated_skill)
            self.assertEqual(
                generated_skill.split("---", 2)[-1],
                canonical_skill.split("---", 2)[-1],
            )
            for resource in ("references", "agents", "scripts", "assets"):
                self.assertTrue((skill_root / resource).is_dir(), resource)

    def test_validator_rejects_missing_or_drifted_claude_manifest(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as td:
            output = Path(td) / "out"
            subprocess.run(
                [sys.executable, str(BUILDER), "generate", "--output", str(output)],
                cwd=ROOT,
                check=True,
            )
            manifest_path = output / "claude" / ".claude-plugin" / "plugin.json"
            manifest_path.write_text("{}\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(BUILDER), "--check", "--output", str(output)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("claude plugin manifest parity", result.stdout + result.stderr)

    def test_public_install_guide_documents_claude_marketplace_commands(self):
        install = (ROOT / "INSTALL.md").read_text(encoding="utf-8")
        for command in (
            "/plugin marketplace add Statusnone420/Skills",
            "/plugin install diataxis-docs@statusnone-skills",
            "/diataxis-docs:docs help",
        ):
            self.assertIn(command, install)

    def test_public_docs_distinguish_claude_desktop_from_terminal_invocation(self):
        install = (ROOT / "INSTALL.md").read_text(encoding="utf-8")
        compatibility = (ROOT / "COMPATIBILITY.md").read_text(encoding="utf-8")

        for phrase in (
            "Claude Desktop",
            "Plugins → Diátaxis Docs → docs",
            "Claude Code terminal",
            "typed namespaced command is not recognized in Claude Desktop",
        ):
            self.assertIn(phrase, install)
        self.assertIn("live-tested through the plugin picker", compatibility)
        self.assertIn("terminal invocation not yet live-tested", compatibility)


if __name__ == "__main__":
    unittest.main()
