import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]


class PublicDocumentationContractTests(unittest.TestCase):
    def test_public_doc_contract(self):
        required = [
            "README.md", "GETTING_STARTED.md", "INSTALL.md", "COMMANDS.md",
            "ARCHITECTURE.md", "ORIGIN.md", "EVALUATION.md", "COMPATIBILITY.md",
            "BENCHMARK.md", "CHANGELOG.md", "CONTRIBUTING.md", "SECURITY.md",
            "ROADMAP.md", "LICENSE", "NOTICE", "AGENTS.md", "docs/README.md",
            "docs/STATE.md", ".github/dependabot.yml", ".github/workflows/codeql.yml",
            ".github/ISSUE_TEMPLATE/bug-report.yml",
            ".github/ISSUE_TEMPLATE/feature-request.yml",
            ".github/ISSUE_TEMPLATE/config.yml",
            ".github/PULL_REQUEST_TEMPLATE.md",
        ]
        for name in required:
            self.assertTrue((ROOT / name).is_file(), name)
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertTrue(readme.startswith("# Statusnone Skills"))
        self.assertLess(readme.index("Bounded repository memory"), readme.index("## 60-second use"))
        self.assertIn("Diátaxis Docs", readme)
        self.assertIn("Benchmark status", readme)
        self.assertIn("Compatibility", readme)
        self.assertIn("Your repository's documentation should help agents", readme)
        self.assertIn("Public alpha", readme)
        self.assertIn("$docs doctor", readme)
        self.assertIn("100+ deterministic tests", readme)
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

    def test_public_alpha_repository_safeguards(self):
        tracked = subprocess.run(
            ["git", "ls-files", ".superpowers/**", "docs/superpowers/**"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
        published_internal = [path for path in tracked if (ROOT / path).is_file()]
        self.assertEqual(published_internal, [])

        dependabot = (ROOT / ".github/dependabot.yml").read_text(encoding="utf-8")
        self.assertIn('package-ecosystem: "github-actions"', dependabot)
        self.assertIn('interval: "monthly"', dependabot)

        codeql = (ROOT / ".github/workflows/codeql.yml").read_text(encoding="utf-8")
        self.assertIn("security-events: write", codeql)
        self.assertIn("contents: read", codeql)
        self.assertIn("languages: python", codeql)
        action_lines = [line.strip() for line in codeql.splitlines() if "uses:" in line]
        self.assertTrue(action_lines)
        for line in action_lines:
            self.assertRegex(line, r"uses:\s+[\w.-]+/[\w./-]+@[0-9a-f]{40}$")

        security = (ROOT / "SECURITY.md").read_text(encoding="utf-8").lower()
        self.assertIn("do not open a public issue", security)
        self.assertIn("no response-time sla", security)

    def test_windows_install_verification_fails_when_skill_missing(self):
        install = (ROOT / "INSTALL.md").read_text(encoding="utf-8")
        self.assertRegex(install, r"if\s*\(-not\s*\(Test-Path .*SKILL\.md")
        self.assertIn("throw", install.lower())


if __name__ == "__main__":
    unittest.main()
