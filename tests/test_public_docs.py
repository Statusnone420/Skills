import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]


class PublicDocumentationContractTests(unittest.TestCase):
    def test_public_doc_contract(self):
        required = [
            "README.md", "GETTING_STARTED.md", "INSTALL.md", "COMMANDS.md",
            "ARCHITECTURE.md", "ORIGIN.md", "EVALUATION.md", "COMPATIBILITY.md",
            "BENCHMARK.md", "CHANGELOG.md", "CONTRIBUTING.md", "SECURITY.md",
            "LICENSE", "NOTICE", "AGENTS.md", "docs/README.md", "docs/STATE.md",
        ]
        for name in required:
            self.assertTrue((ROOT / name).is_file(), name)
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertTrue(readme.startswith("# Statusnone Skills"))
        self.assertLess(readme.index("Bounded repository memory"), readme.index("## 60-second use"))
        self.assertIn("Diátaxis Docs", readme)
        self.assertIn("Benchmark status", readme)
        self.assertIn("Compatibility", readme)
        benchmark = (ROOT / "BENCHMARK.md").read_text(encoding="utf-8")
        self.assertIn("108-run matrix", benchmark)
        self.assertIn("not run", benchmark.lower())
        compatibility = (ROOT / "COMPATIBILITY.md").read_text(encoding="utf-8")
        self.assertIn("unpublished preview", compatibility.lower())
        self.assertIn("canonical source", compatibility.lower())
        self.assertIn("adapters", compatibility.lower())
        install = (ROOT / "INSTALL.md").read_text(encoding="utf-8")
        for phrase in ("PowerShell", "Windows 11", "$HOME/.agents/skills/docs", "New-Item", "mkdir -p", "docs/SKILL.md", "$docs help", "restart", "inspect"):
            self.assertIn(phrase.lower(), install.lower())
        getting = (ROOT / "GETTING_STARTED.md").read_text(encoding="utf-8")
        for phrase in ("Prerequisites", "repository access", "Python", "read-only", "expected", "skill is missing", "file tools"):
            self.assertIn(phrase.lower(), getting.lower())
        origin = (ROOT / "ORIGIN.md").read_text(encoding="utf-8")
        self.assertIn("independent", origin.lower())
        self.assertIn("290,542", origin)
        self.assertNotRegex(origin, r"[A-Za-z]:[\\/](?:Users|home)[\\/]")
        self.assertNotIn("ADHD Matrix code", origin)
        docs_hot = (ROOT / "docs/README.md").stat().st_size + (ROOT / "docs/STATE.md").stat().st_size
        self.assertLessEqual(docs_hot, 16 * 1024)


if __name__ == "__main__":
    unittest.main()
